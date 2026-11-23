"""Application workflow: prepare -> (fill + validate) -> candidate approval -> submit -> verify, or manual handoff.

Every rule that matters is enforced HERE (the UI only displays them):
* automation needs the application policy (master switch, platform status, authorised site) - re-checked at approval and again at submit;
* a submit needs an explicit approval bound (by hash) to exactly the values, files and form that are filled at submit time;
* documents must be APPROVED and still pass the fact check; the profile must be unchanged;
* one application per job: duplicates are refused; the APPROVED -> SUBMITTING step is an atomic compare-and-set (a double click cannot submit twice);
* SUBMITTED / VERIFIED only on evidence; anything uncertain is VERIFICATION_PENDING and is never retried automatically.
"""
import logging
from datetime import datetime, time as dtime, timezone

from pymongo.errors import DuplicateKeyError

from ai.schemas.candidate_profile import CandidateProfile
from backend.services.document_service import DocumentService
from backend.services.matching_service import JobNotFound, MatchingService
from browser.browser_manager import BrowserError
from browser.screenshot_manager import ScreenshotManager
from config.automation_config import AutomationLimits
from config.settings import Settings
from database.models.application import (
    ApplicationRecord, ApplicationValidation, Approval, FieldMapping, SubmissionResult, UploadRecord, idempotency_key,
)
from database.repositories.applications_repository import ApplicationsRepository
from engines.application.application_engine import ApplicationEngine, FormRun, SubmitTracker
from engines.application.document_uploader import Selection, select_documents
from engines.application.document_validator import ValidationInput, validate_application
from engines.application.form_mapper import Facts, normalize
from engines.application.retry_manager import DuplicateCheck, RetryManager
from engines.application.state_machine import (
    SUBMISSION_MAY_EXIST, AppState, InvalidTransition, allowed_from, check_transition,
)
from engines.application.submission_verifier import Verdict, can_mark_verified
from engines.generation.screening_answers import effective_inputs
from integrations.application_adapters import resolve_application_adapter
from integrations.application_policy import ApplicationPolicy
from integrations.base_adapter import ApplicationAdapter, public_http_url
from integrations.policy import PolicyViolation
from security.audit_logger import AuditLogger

logger = logging.getLogger(__name__)
S = AppState


class ApplicationNotFound(Exception):
    pass


class NotEligible(Exception):
    pass


class DuplicateApplication(Exception):
    def __init__(self, message: str, existing_id: str | None = None):
        super().__init__(message)
        self.existing_id = existing_id


class ActionNotAllowed(Exception):
    """The action is not allowed in the application's current state / by a safety rule. Message is user-safe."""

    def __init__(self, message: str, checks: list | None = None):
        super().__init__(message)
        self.checks = checks or []


class LimitReached(Exception):
    pass


def _dump(x):
    return x.model_dump(mode="python") if hasattr(x, "model_dump") else x


class ApplicationService:
    def __init__(self, apps: ApplicationsRepository, matching: MatchingService, documents: DocumentService, settings: Settings,
                 policy: ApplicationPolicy, engine: ApplicationEngine, retry: RetryManager, limits: AutomationLimits,
                 screenshots: ScreenshotManager, audit: AuditLogger | None = None, now=lambda: datetime.now(timezone.utc)):
        self.apps, self.matching, self.documents, self.settings = apps, matching, documents, settings
        self.policy, self.engine, self.retry, self.limits, self.screenshots = policy, engine, retry, limits, screenshots
        self.audit, self._now = audit or AuditLogger(apps), now

    # ------------------------------------------------------------------------------------------------- helpers
    def _get(self, application_id: str) -> dict:
        rec = self.apps.get(application_id)
        if rec is None:
            raise ApplicationNotFound(application_id)
        return rec

    def _job(self, job_id: str) -> dict:
        job = self.matching.jobs.get_job(job_id)
        if job is None:
            raise JobNotFound(job_id)
        return job

    @staticmethod
    def _apply_url(job: dict, adapter: ApplicationAdapter) -> str | None:
        url = job.get("job_url") or next((r.get("url") for r in job.get("source_refs") or [] if r.get("url")), None)
        if not url:
            return None
        return adapter.application_url(url) if adapter.automation_supported else url

    def _move(self, rec: dict, to: AppState, *, event: str, actor: str = "system", fields: dict | None = None, **detail) -> dict:
        """Validated + atomic state change. Raises ActionNotAllowed if the transition is invalid or the state changed under us."""
        try:
            check_transition(rec["state"], to)
        except InvalidTransition as exc:
            raise ActionNotAllowed(str(exc)) from None
        new = self.apps.transition(rec["application_id"], [rec["state"]], to.value, fields)
        if new is None:
            raise ActionNotAllowed("The application changed while this request was running; reload and try again.")
        self.audit.record(rec["application_id"], event, from_state=rec["state"], to_state=to.value, actor=actor, **detail)
        return new

    def _facts(self, profile: CandidateProfile, selection: Selection, rec: dict) -> Facts:
        # personal details come only from preferences or what the candidate typed into the (approved) screening answers - never guessed
        typed = (selection.screening or {}).get("candidate_inputs") or {}
        return Facts(profile=profile, answers=selection.answers, cover_letter_text=selection.cover_letter_text,
                     candidate_inputs=dict(rec.get("inputs") or {}), personal=effective_inputs(self.documents.prefs, typed))

    def _profile(self, rec: dict) -> CandidateProfile:
        return self.matching.profile_for_matching(rec["resume_id"], bool(rec.get("allow_unreviewed")))

    def _public(self, rec: dict) -> dict:
        out = dict(rec)
        out.pop("idempotency_key", None)
        out["allowed_next_states"] = allowed_from(rec["state"])
        out["can_submit_automatically"] = rec["mode"] == "automated" and rec["state"] == S.APPROVED.value
        return out

    # ------------------------------------------------------------------------------------------------- prepare
    def policy_for_job(self, job_id: str) -> dict:
        job = self._job(job_id)
        adapter = resolve_application_adapter(job, self.policy)
        return {**adapter.metadata(), "apply_url": public_http_url(self._apply_url(job, adapter)), "decision": adapter.decision.as_dict()}

    def prepare(self, job_id: str, *, resume_id: str | None = None, allow_unreviewed: bool = False, acknowledge_ineligible: bool = False,
                acknowledge_possible_duplicate: bool = False, candidate_inputs: dict | None = None) -> dict:
        profile = self.matching.profile_for_matching(resume_id, allow_unreviewed)
        job = self._job(job_id)
        if job.get("status") in ("closed", "expired"):
            raise ActionNotAllowed("This job is no longer active.")
        report, _ = self.matching.score(job_id=job_id, resume_id=profile.resume_id, allow_unreviewed=allow_unreviewed, use_ai=False)
        elig = report.eligibility.status
        if elig == "FAIL" and not acknowledge_ineligible:
            raise NotEligible("This job failed the eligibility check (" + "; ".join(report.eligibility.failed_checks[:3]) + "). "
                              "Acknowledge it explicitly to prepare an application anyway.")
        adapter = resolve_application_adapter(job, self.policy)
        url = self._apply_url(job, adapter)
        key = idempotency_key(profile.resume_id, job_id)
        existing = self.apps.find_by_key(key)
        dup = self._duplicate_check(job, url, profile.resume_id, existing["application_id"] if existing else None)
        if dup.blocked:
            raise DuplicateApplication(dup.reason, dup.existing_id)
        if dup.warnings and not acknowledge_possible_duplicate:
            raise DuplicateApplication(dup.warnings[0] + " Acknowledge it explicitly to continue.", None)
        if existing:
            state = S(existing["state"])
            if state in SUBMISSION_MAY_EXIST:
                raise DuplicateApplication(f"This job already has an application (state {state.value}); it is never prepared or submitted again.", existing["application_id"])
            if state == S.SUBMISSION_FAILED and not self.retry.may_prepare_again(state, existing.get("failure_stage")):
                raise ActionNotAllowed("The previous attempt's outcome is uncertain; verify it manually first.")
            rec = existing
        else:
            new = ApplicationRecord(
                idempotency_key=key, job_id=job_id, resume_id=profile.resume_id, profile_id=profile.profile_id, profile_version=profile.profile_version,
                match_id=report.match_id, job_title=job.get("job_title"), company_name=job.get("company_name"), source=adapter.source,
                provider=adapter.provider, apply_url=url, policy=adapter.decision.as_dict(), eligibility=elig, match_score=report.match_score)
            try:
                rec = self.apps.insert(new)
            except DuplicateKeyError:  # a concurrent request created it first
                raise DuplicateApplication("An application for this job already exists.", (self.apps.find_by_key(key) or {}).get("application_id")) from None
            self.audit.record(rec["application_id"], "created", to_state=S.DISCOVERED.value, actor="candidate", provider=adapter.provider, source=adapter.source)
        inputs = dict(rec.get("inputs") or {})
        for k, v in (candidate_inputs or {}).items():
            if isinstance(v, (str, bool)) and (not isinstance(v, str) or v.strip()):
                inputs[normalize(k) or k] = v.strip() if isinstance(v, str) else v
        self.apps.update(rec["application_id"], {
            "inputs": inputs, "profile_id": profile.profile_id, "profile_version": profile.profile_version, "match_id": report.match_id,
            "eligibility": elig, "match_score": report.match_score, "allow_unreviewed": allow_unreviewed, "apply_url": url,
            "ack_ineligible": bool(acknowledge_ineligible), "ack_possible_duplicate": bool(acknowledge_possible_duplicate),
            "policy": adapter.decision.as_dict(), "provider": adapter.provider, "approval": None, "dismissed_at": None, "dismissed_reason": None})
        self.refresh(rec["application_id"])
        return self.get(rec["application_id"])          # includes the handoff package when the application is a manual handoff

    def _duplicate_check(self, job: dict, url: str | None, resume_id: str, exclude_id: str | None) -> DuplicateCheck:
        same: list[str] = []
        if job.get("company_key") and job.get("title_key"):                # the same vacancy seen through another source (Phase 4 dedup keys)
            same = [j["job_id"] for j in self.matching.jobs._jobs.find(
                {"company_key": job["company_key"], "title_key": job["title_key"], "job_id": {"$ne": job["job_id"]}}, {"job_id": 1, "_id": 0})]
        if (job.get("dedup") or {}).get("matched_job_id"):
            same.append(job["dedup"]["matched_job_id"])
        return self.retry.check_duplicate(job_id=job["job_id"], apply_url=url, resume_id=resume_id, exclude_id=exclude_id, same_posting_job_ids=list(dict.fromkeys(same)))

    # ------------------------------------------------------------------------------------------------- refresh (fill + validate)
    def refresh(self, application_id: str) -> dict:
        """(Re)build the preview: documents -> form detection -> mapping -> fill -> validation. Moves to READY_FOR_REVIEW / AWAITING_USER_INPUT /
        MANUAL_HANDOFF_REQUIRED. Any refresh withdraws an earlier approval (the plan may have changed)."""
        rec = self._get(application_id)
        if S(rec["state"]) in SUBMISSION_MAY_EXIST or S(rec["state"]) == S.SUBMITTING:
            raise ActionNotAllowed("This application was already submitted; it cannot be changed.")
        profile = self._profile(rec)
        job = self._job(rec["job_id"])
        adapter = resolve_application_adapter(job, self.policy)
        url = rec.get("apply_url") or self._apply_url(job, adapter)
        selection = select_documents(self.documents, rec["resume_id"], rec["job_id"])
        try:
            common = {"document_ids": selection.document_ids, "document_versions": selection.document_versions, "policy": adapter.decision.as_dict(),
                      "profile_version": profile.profile_version, "approval": None}
            if not (adapter.automation_supported and adapter.decision.automation_allowed) or not url:
                reason = adapter.decision.reason if not adapter.decision.automation_allowed else "Automation is not available for this job; apply yourself."
                return self._public(self._to_handoff(rec, {**common, "mode": "manual_handoff", "notes": [reason]}, "prepared_manual", reason=reason))
            facts = self._facts(profile, selection, rec)
            try:
                run = self.retry.run(lambda: self.engine.run_form(application_id=rec["application_id"], adapter=adapter, apply_url=url, facts=facts,
                                                                  selection=selection, job_id=rec["job_id"]), is_clicked=lambda: False,
                                     on_retry=lambda n, why: self.audit.record(rec["application_id"], "retry", attempt=n, reason=why))
            except PolicyViolation as exc:
                return self._public(self._to_handoff(rec, {**common, "mode": "manual_handoff", "notes": [str(exc)]}, "policy_denied", reason=str(exc)))
            except BrowserError as exc:
                msg = f"Automation is unavailable right now: {exc}"
                return self._public(self._to_handoff(rec, {**common, "mode": "manual_handoff", "notes": [msg]}, "browser_failed", reason=str(exc)))
            if run.stopped and run.handoff:
                return self._public(self._to_handoff(rec, {**common, "mode": "manual_handoff", "notes": [run.stopped], "screenshots": rec.get("screenshots", []) + run.screenshots,
                                                           "session_meta": {"session_used": run.session_used}}, "automation_stopped", reason=run.stopped))
            validation = self._validate(rec, profile, adapter, run, selection, stage="prepare")
            fields = {**common, "mode": "automated", "mapping": [_dump(m) for m in run.mapping], "uploads": [_dump(u) for u in run.uploads],
                      "form_fingerprint": run.fingerprint, "plan_hash": run.plan, "validation": _dump(validation),
                      "form_summary": {"fields": len(run.mapping), "required": sum(m.required for m in run.mapping), "filled": sum(m.status == "filled" for m in run.mapping),
                                       "needs_input": sum(m.status in ("needs_input", "manual_required") for m in run.mapping), "uploads": len(run.uploads)},
                      "screenshots": (rec.get("screenshots") or [])[-4:] + run.screenshots, "notes": selection.problems,
                      "session_meta": {"session_used": run.session_used, "persistent": self.engine.sessions.persistence_enabled, "browser": run.channel}}
            target = S.READY_FOR_REVIEW if validation.can_submit else S.AWAITING_USER_INPUT
            new = self._move(rec, target, event="prepared", actor="system", fields=fields, can_submit=validation.can_submit,
                             missing=len(validation.missing_fields), failed_checks=[c.name for c in validation.failed_critical])
            return self._public(new)
        finally:
            selection.cleanup()

    def _to_handoff(self, rec: dict, fields: dict, event: str, *, reason: str) -> dict:
        fields = {**fields, "validation": None, "plan_hash": None}
        if S(rec["state"]) == S.MANUAL_HANDOFF_REQUIRED:
            self.apps.update(rec["application_id"], fields)
            self.audit.record(rec["application_id"], event, reason=reason)
            return self._get(rec["application_id"])
        return self._move(rec, S.MANUAL_HANDOFF_REQUIRED, event=event, fields=fields, reason=reason)

    def _validate(self, rec: dict, profile: CandidateProfile, adapter: ApplicationAdapter, run: FormRun, selection: Selection, *, stage: str,
                  approval: dict | None = None) -> ApplicationValidation:
        snap = run.snapshot
        by_canon = {m.canonical for m in run.mapping}
        cl_required = any(m.canonical == "cover_letter_upload" and m.required for m in run.mapping)
        return validate_application(ValidationInput(
            stage=stage, job_id=rec["job_id"], expected_job_id=rec["job_id"], form_job_id=rec["job_id"] if run.final_host_ok else "other", profile=profile,
            mapping=run.mapping, uploads=run.uploads, form_errors=list(snap.errors) if snap else [], form_automatable=bool(snap and snap.automatable),
            form_issues=list(snap.issues) if snap else [], has_resume_field="resume_upload" in by_canon, has_cover_letter_field="cover_letter_upload" in by_canon,
            cover_letter_required=cl_required, resume_approved=selection.resume is not None, cover_letter_approved=selection.cover_letter is not None,
            automation_allowed=adapter.automation_supported and adapter.decision.automation_allowed, policy_reason=adapter.decision.reason,
            approval_plan_hash=(approval or {}).get("plan_hash"), current_plan_hash=run.plan, job_active=self._job(rec["job_id"]).get("status") not in ("closed", "expired"),
            problems=selection.problems, eligibility_acknowledged=rec.get("eligibility") != "FAIL" or bool(rec.get("ack_ineligible"))))

    def validate(self, application_id: str) -> dict:
        """Re-run detection/mapping/validation now (e.g. after the candidate added answers or approved documents)."""
        return self.refresh(application_id)

    def set_answers(self, application_id: str, answers: dict[str, str | bool]) -> dict:
        """The candidate's own answers for this application, keyed by field label (or id). Sensitive questions are only ever filled this way."""
        rec = self._get(application_id)
        if S(rec["state"]) in SUBMISSION_MAY_EXIST or S(rec["state"]) == S.SUBMITTING:
            raise ActionNotAllowed("This application was already submitted; it cannot be changed.")
        labels = {m["field_id"]: m["label"] for m in rec.get("mapping") or []}
        inputs = dict(rec.get("inputs") or {})
        for k, v in answers.items():
            if not isinstance(v, (str, bool)):
                continue
            key = normalize(labels.get(k, k)) or k
            if isinstance(v, str) and not v.strip():
                inputs.pop(key, None)
            else:
                inputs[key] = v.strip() if isinstance(v, str) else v
        self.apps.update(application_id, {"inputs": inputs})
        self.audit.record(application_id, "answers_updated", actor="candidate", count=len(answers))
        return self.refresh(application_id)

    # ------------------------------------------------------------------------------------------------- approve
    def approve(self, application_id: str, *, acknowledge_warnings: bool = False) -> dict:
        rec = self._get(application_id)
        if rec.get("dismissed_at"):
            raise ActionNotAllowed("You rejected this application. Prepare it again to reconsider it.")
        if rec["mode"] != "automated":
            raise ActionNotAllowed("This application is a manual handoff: there is nothing to approve for automated submission. Apply yourself and record the result.")
        if rec["state"] != S.READY_FOR_REVIEW.value:
            raise ActionNotAllowed(f"Only an application that is ready for review can be approved (current state: {rec['state']}).")
        v = ApplicationValidation.model_validate(rec["validation"]) if rec.get("validation") else None
        if v is None or not v.can_submit or not rec.get("plan_hash") or v.plan_hash != rec["plan_hash"]:
            raise ActionNotAllowed("The application is not complete or validated yet.", [c.model_dump() for c in (v.failed_critical if v else [])])
        warns = [c for c in v.checks if c.status == "warn"]
        if warns and not acknowledge_warnings:
            raise ActionNotAllowed("Review the warnings and approve again with acknowledge_warnings=true.", [c.model_dump() for c in warns])
        job = self._job(rec["job_id"])
        adapter = resolve_application_adapter(job, self.policy)
        try:
            adapter.require_automation()
        except PolicyViolation as exc:
            raise ActionNotAllowed(str(exc)) from None
        approval = Approval(plan_hash=rec["plan_hash"], acknowledged_warnings=bool(acknowledge_warnings))
        new = self._move(rec, S.APPROVED, event="approved", actor="candidate", fields={"approval": _dump(approval)}, plan=rec["plan_hash"][:12],
                         acknowledged_warnings=bool(acknowledge_warnings))
        return self._public(new)

    # ------------------------------------------------------------------------------------------------- submit
    def _daily_limit(self, rec: dict) -> None:
        from scheduler.scheduler import get_zone                       # the "day" is the local day (default Asia/Kolkata), as in the daily report
        tz = get_zone(self.settings.scheduler_timezone)
        start = datetime.combine(self._now().astimezone(tz).date(), dtime.min, tzinfo=tz).astimezone(timezone.utc)
        if self.apps.count_submitted_since(start) >= self.limits.max_applications_per_day:
            raise LimitReached(f"The daily limit of {self.limits.max_applications_per_day} automated applications is reached.")
        if self.apps.count_submitted_since(start, rec["source"]) >= self.limits.max_applications_per_platform_per_day:
            raise LimitReached(f"The daily limit for this platform ({self.limits.max_applications_per_platform_per_day}) is reached.")

    def submit(self, application_id: str) -> dict:
        rec = self._get(application_id)
        if rec.get("dismissed_at"):
            raise ActionNotAllowed("You rejected this application. Nothing is submitted for a rejected application.")
        if rec["mode"] != "automated":
            raise ActionNotAllowed("Restricted or unsupported platforms are never submitted automatically. Apply yourself (manual handoff) and record the confirmation.")
        job = self._job(rec["job_id"])
        adapter = resolve_application_adapter(job, self.policy)
        try:
            adapter.require_automation()                                   # policy re-check, on the backend, at the moment of submitting
        except PolicyViolation as exc:
            raise ActionNotAllowed(str(exc)) from None
        if rec["state"] != S.APPROVED.value:
            raise ActionNotAllowed("Nothing is submitted without your explicit approval of this exact application. "
                                   f"Current state: {rec['state']}.")
        appr = rec.get("approval")
        if not appr or appr.get("plan_hash") != rec.get("plan_hash"):
            raise ActionNotAllowed("The recorded approval does not match the current application; review and approve again.")
        dup = self._duplicate_check(job, rec.get("apply_url"), rec["resume_id"], rec["application_id"])
        if dup.blocked:
            raise DuplicateApplication(dup.reason, dup.existing_id)
        self._daily_limit(rec)
        profile = self._profile(rec)
        if profile.profile_version != rec["profile_version"]:
            self._withdraw(rec, "The candidate profile changed after you approved this application.")
        selection = select_documents(self.documents, rec["resume_id"], rec["job_id"])
        try:
            approved_files = {(u["role"], u["document_id"], u["document_version"]) for u in rec.get("uploads") or []}
            current = {(r, getattr(selection, r).document_id, getattr(selection, r).version) for r in ("resume", "cover_letter") if getattr(selection, r)}
            if approved_files != current:
                self._withdraw(rec, "The approved documents changed after you approved this application.")
            url = rec["apply_url"]
            tracker = SubmitTracker()
            facts = self._facts(profile, selection, rec)

            def gate(run: FormRun) -> str | None:                         # final validation on the freshly filled form, just before the click
                v = self._validate(rec, profile, adapter, run, selection, stage="submit", approval=appr)
                return None if v.can_submit else "Submission blocked: " + "; ".join(f"{c.name}: {c.message}" for c in v.failed_critical[:4])

            moved = self._move(rec, S.SUBMITTING, event="submitting", actor="candidate", fields={"attempts": rec.get("attempts", 0) + 1, "failure_stage": None},
                               attempt=rec.get("attempts", 0) + 1)
            try:
                run = self.retry.run(lambda: self.engine.run_form(application_id=rec["application_id"], adapter=adapter, apply_url=url, facts=facts, selection=selection,
                                                                  job_id=rec["job_id"], submit=True, expected_plan_hash=rec["plan_hash"], tracker=tracker, gate=gate),
                                     is_clicked=lambda: tracker.clicked,
                                     on_retry=lambda n, why: self.audit.record(rec["application_id"], "retry", attempt=n, reason=why))
            except Exception as exc:  # noqa: BLE001
                return self._after_exception(moved, tracker, exc)
            return self._after_run(moved, run)
        finally:
            selection.cleanup()

    def _withdraw(self, rec: dict, reason: str) -> None:
        self._move(rec, S.READY_FOR_REVIEW, event="approval_withdrawn", fields={"approval": None, "plan_hash": None, "validation": None}, reason=reason)
        raise ActionNotAllowed(reason + " Review and approve again.")

    def _after_exception(self, rec: dict, tracker: SubmitTracker, exc: Exception) -> dict:
        logger.error("Submission run raised %s (clicked=%s)", type(exc).__name__, tracker.clicked)
        if tracker.clicked:                                                # we do not know what happened: never "failed", never retried
            return self._public(self._move(rec, S.VERIFICATION_PENDING, event="outcome_unknown", fields={
                "failure_stage": "after_submit", "submitted_at": self._now(),
                "submission": _dump(SubmissionResult(outcome="unconfirmed", detail="An error occurred after the submit button was clicked; check whether it went through."))},
                error=type(exc).__name__))
        msg = str(exc) if isinstance(exc, (BrowserError, PolicyViolation)) else "An unexpected error occurred before submitting."
        failed = self._move(rec, S.SUBMISSION_FAILED, event="failed_before_submit", fields={"failure_stage": "before_submit", "approval": None,
                            "submission": _dump(SubmissionResult(outcome="failed", detail=msg))}, error=type(exc).__name__)
        return self._public(failed)

    def _after_run(self, rec: dict, run: FormRun) -> dict:
        fields = {"screenshots": (rec.get("screenshots") or [])[-6:] + run.screenshots, "mapping": [_dump(m) for m in run.mapping] or rec.get("mapping", []),
                  "uploads": [_dump(u) for u in run.uploads] or rec.get("uploads", [])}
        if not run.clicked:                                                # stopped before clicking: certain that nothing was sent
            reason = run.stopped or "Submission was not attempted."
            failed = self._move(rec, S.SUBMISSION_FAILED, event="blocked_before_submit", fields={**fields, "failure_stage": "before_submit", "approval": None,
                                "submission": _dump(SubmissionResult(outcome="not_attempted", detail=reason))}, reason=reason, handoff=run.handoff)
            nxt = S.MANUAL_HANDOFF_REQUIRED if run.handoff else S.AWAITING_USER_INPUT if "Required fields" in reason else S.READY_FOR_REVIEW
            extra = {"mode": "manual_handoff", "plan_hash": None, "validation": None} if run.handoff else {"plan_hash": None, "validation": None}
            return self._public(self._move(failed, nxt, event="needs_attention", fields=extra, reason=reason))
        v: Verdict = run.verdict  # type: ignore[assignment]
        result = v.to_result("page")
        base = {**fields, "submission": _dump(result), "submitted_at": self._now()}
        if v.outcome == "confirmed":
            sub = self._move(rec, S.SUBMITTED, event="submitted", fields=base, evidence=v.evidence, has_reference=bool(v.reference))
            if can_mark_verified(result):
                sub = self._move(sub, S.VERIFIED, event="verified", fields={"verified_at": self._now()}, evidence=v.evidence)
            return self._public(sub)
        if v.outcome == "failed":                                          # the site showed validation errors: certainly NOT submitted
            return self._public(self._move(rec, S.SUBMISSION_FAILED, event="rejected_by_site", fields={**fields, "failure_stage": "rejected", "approval": None,
                                           "submission": _dump(result)}, reason=v.detail))
        return self._public(self._move(rec, S.VERIFICATION_PENDING, event="outcome_unconfirmed", fields={**base, "failure_stage": "after_submit"}, detail_text=v.detail))

    # ------------------------------------------------------------------------------------------------- manual handoff
    def manual_handoff(self, application_id: str) -> dict:
        rec = self._get(application_id)
        if rec["state"] != S.MANUAL_HANDOFF_REQUIRED.value:
            rec = self._move(rec, S.MANUAL_HANDOFF_REQUIRED, event="manual_handoff_started", actor="candidate", fields={
                "mode": "manual_handoff", "approval": None, "plan_hash": None, "validation": None,
                "manual": {**(rec.get("manual") or {}), "started_at": self._now()}})
        return {**self._public(rec), "handoff": self.handoff_package(application_id)}

    def handoff_package(self, application_id: str) -> dict:
        """What the candidate needs to apply themselves: link, approved documents, prepared answers, contact details, what is still missing."""
        rec = self._get(application_id)
        profile = self._profile(rec)
        selection = select_documents(self.documents, rec["resume_id"], rec["job_id"])
        try:
            c = profile.contact
            missing = list(selection.problems)
            answers = [{"question": a["question"], "answer": a["answer"], "status": a["status"]} for a in selection.answers.values()]
            if selection.screening:
                missing += [f"Answer needed: {a['question']}" for a in selection.screening.get("answers", []) if a.get("status") == "needs_input"]
            else:
                missing.append("No approved screening answers yet (optional): generate and approve them in Document Generation.")
            personal = effective_inputs(self.documents.prefs, (selection.screening or {}).get("candidate_inputs"))
            return {"apply_url": public_http_url(rec.get("apply_url")), "job_title": rec.get("job_title"), "company_name": rec.get("company_name"),
                    "documents": {r: {"document_id": selection.document_ids[r], "version": selection.document_versions[r]} for r in selection.document_ids},
                    "contact": {"full_name": c.full_name, "email": c.email, "phone": c.phone, "location": c.location, "linkedin_url": c.linkedin_url,
                                "github_url": c.github_url, "portfolio_url": c.portfolio_url},
                    "prepared_answers": answers, "personal_details": {k: personal.get(k) for k in ("notice_period", "salary_expectation", "relocation", "work_authorization")},
                    "missing": missing, "platform_note": (rec.get("policy") or {}).get("reason")}
        finally:
            selection.cleanup()

    def mark_opened(self, application_id: str) -> dict:
        rec = self._get(application_id)
        if rec["state"] != S.MANUAL_HANDOFF_REQUIRED.value:
            raise ActionNotAllowed("Only a manual handoff can be marked as opened.")
        self.apps.update(application_id, {"manual": {**(rec.get("manual") or {}), "opened_at": self._now()}})
        self.audit.record(application_id, "manual_link_opened", actor="candidate")
        return self._public(self._get(application_id))

    def confirm(self, application_id: str, *, submitted: bool, reference: str | None = None, notes: str | None = None) -> dict:
        """The candidate records the outcome of an application they completed themselves (or checked after an uncertain automated attempt).
        This is human-attested evidence; it is labelled as such and never presented as a page-verified confirmation."""
        rec = self._get(application_id)
        state = S(rec["state"])
        if state not in (S.MANUAL_HANDOFF_REQUIRED, S.VERIFICATION_PENDING, S.SUBMITTED):
            raise ActionNotAllowed(f"Nothing to confirm in state {state.value}.")
        reference = (reference or "").strip()[:80] or None
        notes = (notes or "").strip()[:500] or None
        if not submitted:
            if state == S.SUBMITTED:
                raise ActionNotAllowed("A submitted application cannot be marked as not submitted.")
            if state == S.MANUAL_HANDOFF_REQUIRED:
                self.apps.update(application_id, {"manual": {**(rec.get("manual") or {}), "notes": notes}})
                self.audit.record(application_id, "manual_not_submitted", actor="candidate")
                return self._public(self._get(application_id))
            return self._public(self._move(rec, S.SUBMISSION_FAILED, event="user_reported_not_submitted", actor="candidate", fields={
                "failure_stage": "before_submit", "submission": _dump(SubmissionResult(outcome="user_reported", detail=notes or "Checked by the candidate: not submitted.", verified_by="user"))}))
        has_evidence = bool(reference or notes)
        result = SubmissionResult(outcome="user_reported", evidence=["recorded by the candidate"] + (["reference number"] if reference else []), reference=reference or rec["submission"].get("reference"),
                                  detail=notes or "", verified_by="user")
        base = {"submission": _dump(result), "manual": {**(rec.get("manual") or {}), "confirmed_at": self._now(), "notes": notes}, "submitted_at": rec.get("submitted_at") or self._now()}
        if state == S.SUBMITTED:                         # confirming an automated submission that the page confirmed without a reference
            if not has_evidence:
                raise ActionNotAllowed("Provide the confirmation reference or a note to mark it verified.")
            return self._public(self._move(rec, S.VERIFIED, event="verified_by_candidate", actor="candidate", fields={**base, "verified_at": self._now()}))
        if has_evidence and can_mark_verified(result):
            via = self._move(rec, S.SUBMITTED, event="submitted_recorded_by_candidate", actor="candidate", fields=base)
            return self._public(self._move(via, S.VERIFIED, event="verified_by_candidate", actor="candidate", fields={"verified_at": self._now()}))
        return self._public(self._move(rec, S.SUBMITTED, event="submitted_recorded_by_candidate", actor="candidate", fields=base))

    # ------------------------------------------------------------------------------------------------- one-click record
    def record_applied_yourself(self, job_id: str, *, notes: str | None = None) -> dict:
        """The candidate says: "I applied to this job myself". Recorded through the normal steps (application -> manual handoff -> submitted,
        recorded by the candidate), so it shows on Applied Jobs and in the reports as human-attested - never as a page-verified submission.
        Nothing is sent to the employer. Already applied = returned unchanged (no second record)."""
        profile = self.matching.profile_for_matching(None, False)
        existing = self.apps.find_by_key(idempotency_key(profile.resume_id, job_id))
        if existing is None:
            try:   # the user has already applied, so an eligibility FAIL or a possible duplicate is acknowledged by that statement
                rec = self.prepare(job_id, acknowledge_ineligible=True, acknowledge_possible_duplicate=True)
            except DuplicateApplication as exc:
                if exc.existing_id:
                    return self._public(self._get(exc.existing_id))   # this posting already has an application (e.g. via another source)
                raise
        else:
            rec = self._public(existing)
        state = S(rec["state"])
        if state in SUBMISSION_MAY_EXIST:
            return self._public(self._get(rec["application_id"]))
        if state != S.MANUAL_HANDOFF_REQUIRED:
            self.manual_handoff(rec["application_id"])
        return self.confirm(rec["application_id"], submitted=True, notes=notes)   # a click alone is SUBMITTED (your word), not VERIFIED (needs a reference or note)

    def applied_states(self, job_ids: list[str]) -> dict[str, str]:
        """job_id -> the state of its application (newest), for showing an "applied" switch next to each job."""
        out: dict[str, str] = {}
        for a in self.apps.col.find({"job_id": {"$in": list(job_ids)}}, {"_id": 0, "job_id": 1, "state": 1, "created_at": 1}).sort("created_at", 1):
            out[a["job_id"]] = a["state"]
        return out

    # ------------------------------------------------------------------------------------------------- read
    def get(self, application_id: str) -> dict:
        rec = self._public(self._get(application_id))
        if rec["state"] == S.MANUAL_HANDOFF_REQUIRED.value:
            try:
                rec["handoff"] = self.handoff_package(application_id)
            except Exception:  # noqa: BLE001 - e.g. the profile was deleted
                rec["handoff"] = None
        return rec

    def status(self, application_id: str) -> dict:
        rec = self._get(application_id)
        sub = rec.get("submission") or {}
        return {"application_id": application_id, "state": rec["state"], "mode": rec["mode"], "outcome": sub.get("outcome"), "evidence": sub.get("evidence", []),
                "reference": sub.get("reference"), "verified_by": sub.get("verified_by"), "submitted_at": rec.get("submitted_at"), "verified_at": rec.get("verified_at"),
                "needs_manual_verification": rec["state"] == S.VERIFICATION_PENDING.value, "detail": sub.get("detail"), "allowed_next_states": allowed_from(rec["state"])}

    def set_notes(self, application_id: str, notes: str | None) -> dict:
        """Your own notes about an application (any state). Never read by automation; the audit event records only that they changed."""
        self._get(application_id)
        notes = (notes or "").strip()[:2000] or None
        self.apps.update(application_id, {"user_notes": notes})
        self.audit.record(application_id, "notes_updated", actor="candidate", length=len(notes or ""))
        return self._public(self._get(application_id))

    def reject(self, application_id: str, reason: str | None = None) -> dict:
        """The candidate decides NOT to apply. Allowed only before anything was sent; withdraws approval and cancels the queue entry."""
        rec = self._get(application_id)
        if S(rec["state"]) in SUBMISSION_MAY_EXIST:
            raise ActionNotAllowed("This application may already have been submitted; it cannot be rejected.")
        if rec.get("dismissed_at"):
            return self._public(rec)
        reason = (reason or "").strip()[:300] or None
        fields = {"dismissed_at": self._now(), "dismissed_reason": reason, "approval": None}
        if S(rec["state"]) == S.APPROVED:
            rec = self._move(rec, S.READY_FOR_REVIEW, event="approval_withdrawn", actor="candidate", fields=fields, reason="rejected by the candidate")
        else:
            self.apps.update(application_id, fields)
        self.audit.record(application_id, "rejected", actor="candidate", has_reason=bool(reason))
        from database.repositories.queue_repository import CANCELLED, QueueRepository
        qr = QueueRepository(self.apps.col.database)
        task = qr.find_by_key(rec["idempotency_key"])
        if task:
            qr.transition(task["task_id"], ["queued", "paused", "processing", "awaiting_approval", "failed", "manual_handoff"], CANCELLED,
                          {"lease_expires_at": None, "worker_id": None}, event="cancelled", detail="application rejected by the candidate")
        return self._public(self._get(application_id))

    def page(self, *, limit: int = 50, skip: int = 0, **filters) -> dict:
        return {"total": self.apps.count(**filters), "items": self.list_applications(limit=limit, skip=skip, **filters)}

    def list_applications(self, **filters) -> list[dict]:
        return [{k: r.get(k) for k in ("application_id", "job_id", "job_title", "company_name", "source", "provider", "mode", "state", "eligibility", "match_score",
                                       "created_at", "updated_at", "submitted_at", "verified_at", "apply_url", "user_notes", "dismissed_at")}
                | {"reference": (r.get("submission") or {}).get("reference"), "verified_by": (r.get("submission") or {}).get("verified_by")}
                for r in self.apps.find_all(**filters)]

    def events(self, application_id: str) -> list[dict]:
        self._get(application_id)
        return self.apps.list_events(application_id)

    def screenshot(self, application_id: str, sid: str) -> bytes | None:
        rec = self._get(application_id)
        return self.screenshots.read(sid) if sid in (rec.get("screenshots") or []) else None
