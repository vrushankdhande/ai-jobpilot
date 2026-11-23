"""Drives ONE permitted application form in a browser: open, detect, map, fill, upload, preview - and, only when told to by the service
after explicit candidate approval, submit once and read the evidence.

Stateless by design: every call opens a fresh browser, so what is submitted is exactly what was re-detected and re-filled, and it is compared
(by hash) to what the candidate approved. The state machine, approval, policy and duplicate checks live in ``backend/services/application_service.py``;
this engine never decides whether to submit - it only refuses to click when the form or the values differ from the approved plan.

It stops (and reports why) on: CAPTCHA / bot checks, login walls, HTTP 401/403/429, multi-step or unrecognised forms, unidentifiable fields.
It never solves, bypasses or works around any of them.
"""
import logging
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

from browser.browser_manager import BrowserError, BrowserManager
from browser.locator_engine import FormField, FormSnapshot, LocatorEngine
from browser.screenshot_manager import ScreenshotManager
from browser.session_manager import SessionManager
from config.automation_config import AutomationLimits
from config.settings import Settings
from database.models.application import FieldMapping, UploadRecord, plan_hash
from engines.application.document_uploader import Selection, choose_format, upload_file
from engines.application.form_mapper import Facts, map_form
from engines.application.submission_verifier import Verdict, verify_submission
from integrations.base_adapter import ApplicationAdapter

logger = logging.getLogger(__name__)


@dataclass
class SubmitTracker:
    """Set to True immediately BEFORE the submit click. From then on nothing is ever retried."""
    clicked: bool = False


@dataclass
class FormRun:
    stopped: str | None = None            # why automation ended early (user-safe)
    handoff: bool = False                 # True: only the candidate can continue this one (captcha, login, unsupported form)
    plan_changed: bool = False            # the form/values differ from what was approved - nothing was clicked
    snapshot: FormSnapshot | None = None
    mapping: list[FieldMapping] = field(default_factory=list)
    uploads: list[UploadRecord] = field(default_factory=list)
    screenshots: list[str] = field(default_factory=list)
    verdict: Verdict | None = None
    clicked: bool = False
    plan: str | None = None
    fingerprint: str | None = None
    final_host_ok: bool = True
    channel: str | None = None
    session_used: bool = False


class ApplicationEngine:
    def __init__(self, settings: Settings, sessions: SessionManager, screenshots: ScreenshotManager, limits: AutomationLimits, *,
                 sleep=time.sleep, browser_factory=None):
        self.settings, self.sessions, self.screenshots, self.limits, self._sleep = settings, sessions, screenshots, limits, sleep
        self._factory = browser_factory or (lambda s, hosts: BrowserManager(s, allowed_hosts=hosts))

    # ----------------------------------------------------------------------------------------------- one run
    def run_form(self, *, application_id: str, adapter: ApplicationAdapter, apply_url: str, facts: Facts, selection: Selection,
                 job_id: str, submit: bool = False, expected_plan_hash: str | None = None, tracker: SubmitTracker | None = None,
                 gate=None) -> FormRun:
        """``gate(run) -> str | None`` is the service's final check, evaluated on the freshly filled form right before the click;
        a returned string means "do not submit" (it becomes ``run.stopped``)."""
        adapter.require_automation()                         # policy gate: raises PolicyViolation (nothing is opened)
        tracker = tracker or SubmitTracker()
        host = (urlparse(apply_url).hostname or "").lower()
        run = FormRun()
        with self._factory(self.settings, [host]) as bm:
            run.channel = getattr(bm, "channel_used", None)
            state = self.sessions.load(host)
            run.session_used = state is not None
            with bm.session(state) as page:
                status = bm.goto(page, apply_url)
                loc = LocatorEngine(page)
                verdict = self.sessions.assess(page, status)
                if verdict.must_stop:
                    run.stopped, run.handoff = verdict.reason(), True
                    run.screenshots += _shot(self.screenshots, page, application_id)
                    return run
                if urlparse(page.url).hostname and (urlparse(page.url).hostname or "").lower() != host:
                    run.stopped, run.handoff, run.final_host_ok = "The page redirected to a different site; stopping.", True, False
                    return run
                snap = loc.read()
                run.snapshot, run.fingerprint = snap, snap.fingerprint
                if not snap.automatable:
                    run.stopped, run.handoff = " ".join(snap.issues), True
                    run.screenshots += _shot(self.screenshots, page, application_id)
                    return run

                mapping = map_form(snap.fields, facts)
                run.mapping = self._fill_all(loc, snap, mapping, selection, run)
                post = loc.read()                            # structure after filling: dynamic forms are not supported
                if post.fingerprint != snap.fingerprint:
                    run.stopped, run.handoff = "The form changed while it was being filled (dynamic fields); only static single-page forms are supported.", True
                    run.screenshots += _shot(self.screenshots, page, application_id)
                    return run
                run.snapshot = post
                run.plan = plan_hash(run.mapping, run.uploads, job_id, snap.fingerprint, apply_url)
                run.screenshots += _shot(self.screenshots, page, application_id)
                if not submit:
                    return run

                # ---------------------------------------------------------------- the submit step (service has already checked approval)
                if expected_plan_hash and run.plan != expected_plan_hash:
                    run.plan_changed = True
                    run.stopped = "The form or the values differ from what you approved. Nothing was submitted; review and approve again."
                    return run
                unfilled = [m.label for m in run.mapping if m.required and m.status not in ("filled", "ready")]
                if unfilled or any(m.required and m.canonical in ("resume_upload", "cover_letter_upload") and not _uploaded(run, m) for m in run.mapping):
                    run.stopped = f"Required fields are not completed: {', '.join(unfilled[:5]) or 'a required upload'}. Nothing was submitted."
                    return run
                if post.errors:
                    run.stopped = "The form shows validation errors: " + "; ".join(post.errors[:3]) + ". Nothing was submitted."
                    return run
                if len(post.submit) != 1:
                    run.stopped, run.handoff = "Could not identify a single submit control. Nothing was submitted.", True
                    return run
                blocked = gate(run) if gate else None
                if blocked:
                    run.stopped = blocked
                    return run
                self._sleep(self.limits.delay_seconds_between_actions)          # courtesy pause, not randomised
                tracker.clicked = True                                          # from here on: never retried
                run.clicked = True
                try:
                    loc.submit_locator(post.submit[0].id).click()
                    run.verdict = self._observe(page, loc, adapter, host)
                except Exception as exc:  # noqa: BLE001 - anything after the click is "unknown", never "failed"
                    logger.warning("Browser error after submit click: %s", type(exc).__name__)
                    run.verdict = verify_submission(text="", url=None, form_still_present=False, errors=[], adapter=adapter, navigation_error=True)
                run.screenshots += _shot(self.screenshots, page, application_id)
        return run

    # ----------------------------------------------------------------------------------------------- helpers
    def _observe(self, page, loc: LocatorEngine, adapter: ApplicationAdapter, host: str) -> Verdict:
        """What does the page show after the click? Waits for it to settle, then reads text + form presence."""
        try:
            page.wait_for_load_state("load", timeout=self.settings.browser_timeout_ms)
            page.wait_for_load_state("networkidle", timeout=min(5000, self.settings.browser_timeout_ms))
        except Exception:  # noqa: BLE001
            pass
        for _ in range(2):                                    # one retry: reading during a navigation can fail
            try:
                text, url = loc.page_text(), page.url
                after = loc.read()
                break
            except Exception:  # noqa: BLE001
                self._sleep(0.5)
        else:
            return verify_submission(text="", url=None, form_still_present=False, errors=[], adapter=adapter, navigation_error=True)
        same_host = (urlparse(url).hostname or "").lower() == host
        if not same_host:                                     # a page on another site proves nothing about THIS application
            return Verdict("unconfirmed", ["redirected to a different site"], None, "After submitting, the browser ended up on a different site; the outcome is unknown. Check yourself.")
        form_present = after.found and bool(after.fields)
        return verify_submission(text=text, url=url, form_still_present=form_present, errors=after.errors if form_present else [], adapter=adapter,
                                 captcha=after.has_captcha)

    def _fill_all(self, loc: LocatorEngine, snap: FormSnapshot, mapping: list[FieldMapping], selection: Selection, run: FormRun) -> list[FieldMapping]:
        by_id = {f.id: f for f in snap.fields}
        for m in mapping:
            f = by_id[m.field_id]
            if m.status != "ready":
                continue
            try:
                if m.canonical in ("resume_upload", "cover_letter_upload"):
                    self._upload(loc, f, m, selection, run)
                elif not self._fill_one(loc, f, m):
                    m.status, m.note = "needs_input", "The value could not be entered into the form; enter it yourself."
                else:
                    m.status = "filled"
            except Exception as exc:  # noqa: BLE001
                m.status, m.note = "needs_input", f"The field could not be filled ({type(exc).__name__})."
        return mapping

    def _upload(self, loc: LocatorEngine, f: FormField, m: FieldMapping, selection: Selection, run: FormRun) -> None:
        role = "resume" if m.canonical == "resume_upload" else "cover_letter"
        sf = getattr(selection, role)
        if sf is None:                                       # never upload anything that is not an approved document
            m.status = "needs_input" if m.required else "optional_skipped"
            m.note = f"No approved {role.replace('_', ' ')} available. Approve one in Document Generation."
            return
        chosen = choose_format(sf, f.accept)
        if chosen is None:
            m.status, m.note = "needs_input", f"The form does not accept {sf.format.upper()} files ({f.accept}); upload it yourself."
            return
        rec = upload_file(loc.locator(f.id), chosen)
        rec.field_id = f.id
        run.uploads.append(rec)
        if rec.verified:
            m.status, m.value, m.source = "filled", chosen.name, f"document:{chosen.document_id}:v{chosen.version}"
        else:
            m.status, m.note = "needs_input", rec.detail or "The upload could not be verified."

    @staticmethod
    def _fill_one(loc: LocatorEngine, f: FormField, m: FieldMapping) -> bool:
        el, value = loc.locator(f.id), m.value
        if f.disabled:
            return False
        if f.kind == "select":
            el.select_option(label=str(value))
            return el.evaluate("e => (e.options[e.selectedIndex] || {}).text || ''").strip() == str(value).strip()
        if f.kind == "radio":
            if value not in f.options:
                return False
            target = loc.locator(f.option_marks[f.options.index(value)])
            target.check()
            return target.is_checked()
        if f.kind == "checkbox":
            el.set_checked(bool(value))
            return el.is_checked() == bool(value)
        if f.kind == "date":
            return False                                      # date pickers vary too much: the candidate enters dates
        el.fill(str(value))
        got = el.input_value()
        return got.replace("\r\n", "\n").strip() == str(value).replace("\r\n", "\n").strip()


def _shot(mgr: ScreenshotManager, page, application_id: str) -> list[str]:
    sid = mgr.capture(page, application_id)
    return [sid] if sid else []


def _uploaded(run: FormRun, m: FieldMapping) -> bool:
    return m.status == "filled" and any(u.field_id == m.field_id and u.verified for u in run.uploads)
