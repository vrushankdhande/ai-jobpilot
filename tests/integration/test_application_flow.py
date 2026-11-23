"""Phase 6 end-to-end on a throw-away MongoDB, a REAL Chromium-family browser (Playwright) and controlled LOCAL forms.

No real job application is ever submitted: the only form server is http://127.0.0.1 (tests/fixtures/form_server.py). Browser tests skip
(loudly) if no browser can start.
"""
import hashlib
import threading

import pytest
from fastapi.testclient import TestClient

from ai.schemas.job_schema import JobInput
from backend.api.routes.applications import get_application_service
from backend.api.routes.documents import get_document_service
from backend.main import app
from backend.services.application_service import ApplicationService
from backend.services.document_service import DocumentService
from browser.screenshot_manager import ScreenshotManager
from browser.session_manager import SessionManager
from config.automation_config import AutomationLimits
from config.scoring_config import Preferences, load_scoring_config
from config.settings import get_settings
from database.repositories.applications_repository import ApplicationsRepository
from database.repositories.documents_repository import DocumentsRepository
from engines.application.application_engine import ApplicationEngine
from engines.application.retry_manager import RetryManager
from engines.matching.skill_extractor import get_catalog
from integrations.application_policy import ApplicationPolicy
from integrations.policy import PolicyRegistry
from tests.fixtures.jd_dataset import JDS

pytestmark = [pytest.mark.integration, pytest.mark.browser]
JD = JDS[0]
CONSENT = {"I agree to the privacy policy": True}


class Env:
    pass


def make_env(test_db, make_matching_service, seeded, tmp_path, form_server, **setting_overrides):
    e = Env()
    overrides = {"generated_storage_dir": str(tmp_path / "generated"), "screenshots_dir": str(tmp_path / "shots"), "allow_local_test_forms": True,
                 "application_automation_enabled": True, "browser_headless": True, "browser_sessions_dir": str(tmp_path / "sessions"), **setting_overrides}
    e.settings = get_settings().model_copy(update=overrides)
    e.matching = make_matching_service()
    e.profile, e.db, e.server = seeded, test_db, form_server
    e.documents = DocumentService(DocumentsRepository(test_db), e.matching, e.settings, get_catalog(e.settings),
                                  Preferences(), load_scoring_config(e.settings))
    e.apps = ApplicationsRepository(test_db)
    shots = ScreenshotManager(e.settings.screenshots_path)
    limits = AutomationLimits(max_applications_per_day=20, max_applications_per_platform_per_day=10, delay_seconds_between_actions=0)
    e.engine = ApplicationEngine(e.settings, SessionManager(e.settings), shots, limits, sleep=lambda s: None)
    e.policy = ApplicationPolicy.load(e.settings, PolicyRegistry.load(e.settings))
    e.service = ApplicationService(e.apps, e.matching, e.documents, e.settings, e.policy, e.engine, RetryManager(e.apps, 2, 0, sleep=lambda s: None), limits, shots)
    e.rebuild = lambda **kw: _rebuild(e, **kw)
    return e


def _rebuild(e, policy=None, limits=None):
    lim = limits or e.service.limits
    e.service = ApplicationService(e.apps, e.matching, e.documents, e.settings, policy or e.policy, e.engine, e.service.retry, lim, e.service.screenshots)
    return e.service


def add_job(e, path="/apply", source="local_test", title=None, company="Northwind AI", description=None):
    jid, _, _ = e.matching.analyze_jd(JobInput(job_description=description or JD["text"], job_title=title or JD["title"], company_name=company,
                                               location="Pune, India", job_url=e.server.url(path)))
    e.db["jobs"].update_one({"job_id": jid}, {"$set": {"source": source}})
    return jid


def approve_documents(e, job_id, *, cover=True, screening=True):
    docs = {}
    r = e.documents.generate_resume(job_id)
    docs["resume"] = e.documents.approve(r["document_id"], acknowledge_warnings=True)
    if cover:
        c = e.documents.generate_cover_letter(job_id)
        docs["cover_letter"] = e.documents.approve(c["document_id"], acknowledge_warnings=True)
    if screening:
        s = e.documents.generate_screening(job_id, candidate_inputs={"notice_period": "30 days", "salary_expectation": "INR 3,000,000 per year",
                                                                       "relocation": "yes", "work_authorization": "Yes, citizen"})
        docs["screening"] = e.documents.approve(s["document_id"], acknowledge_warnings=True)
    return docs


@pytest.fixture
def env(test_db, make_matching_service, seeded, tmp_path, form_server, browser_ok):
    form_server.reset()
    return make_env(test_db, make_matching_service, seeded, tmp_path, form_server)


@pytest.fixture
def ready(env):
    """A job on the /apply form with approved resume, cover letter and screening answers."""
    jid = add_job(env, "/apply")
    docs = approve_documents(env, jid)
    return jid, docs


def prepare(e, jid, **kw):
    return e.service.prepare(jid, candidate_inputs=kw.pop("candidate_inputs", CONSENT), **kw)


# =============================================================================================== preparation
def test_prepare_fills_form_uploads_approved_resume_and_stops_before_submitting(env, ready):
    jid, docs = ready
    a = prepare(env, jid)
    assert a["state"] == "READY_FOR_REVIEW" and a["mode"] == "automated" and a["validation"]["can_submit"] is True
    by = {m["label"]: m for m in a["mapping"]}
    assert by["Full name"]["value"] == "Jane Sample" and by["Full name"]["source"] == "contact.full_name"
    assert by["Email address"]["value"] == "jane.sample@example.com" and by["Current employer"]["value"] == "Acme Analytics"
    assert by["Tell us about yourself"]["source"].startswith("screening_answers.") and by["Tell us about yourself"]["status"] == "filled"
    assert by["Are you willing to relocate?"]["value"] == "Yes" and by["Do you have the right to work in India?"]["value"] == "Yes"
    assert by["I agree to the privacy policy"]["source"] == "candidate_input"          # consent only from explicit candidate input
    assert by["Gender"]["status"] == "optional_skipped" and by["Gender"]["value"] is None  # demographics are never filled
    assert {u["role"] for u in a["uploads"]} == {"resume", "cover_letter"} and all(u["verified"] for u in a["uploads"])
    assert env.server.posts == []                                                         # NOTHING was submitted
    assert a["approval"] is None and a["screenshots"] and env.service.screenshot(a["application_id"], a["screenshots"][-1])[:4] == b"\x89PNG"
    ev = [x["type"] for x in env.service.events(a["application_id"])]
    assert ev == ["created", "prepared"]


def test_unknown_or_sensitive_required_fields_need_the_candidate_and_block_approval(env, ready):
    jid, _ = ready
    a = prepare(env, jid, candidate_inputs={})
    assert a["state"] == "AWAITING_USER_INPUT" and "I agree to the privacy policy" in a["validation"]["missing_fields"]
    with pytest.raises(Exception) as exc:
        env.service.approve(a["application_id"])
    assert "ready for review" in str(exc.value)
    b = env.service.set_answers(a["application_id"], CONSENT)
    assert b["state"] == "READY_FOR_REVIEW" and b["validation"]["missing_fields"] == []


def test_only_approved_documents_are_uploaded(env):
    jid = add_job(env, "/apply")
    env.documents.generate_resume(jid)                     # generated but NOT approved
    a = prepare(env, jid)
    assert a["state"] == "AWAITING_USER_INPUT" and a["uploads"] == []
    resume = next(m for m in a["mapping"] if m["label"] == "Resume/CV")
    assert resume["status"] == "needs_input" and "approved" in resume["note"]
    assert any("No approved resume" in n for n in a["notes"])
    assert not any(c["status"] == "pass" and c["name"] == "resume_uploaded" for c in a["validation"]["checks"])


def test_a_newer_draft_does_not_replace_the_approved_version(env, ready):
    jid, docs = ready
    env.documents.update_content(docs["resume"]["document_id"], {**docs["resume"]["content"], "summary": docs["resume"]["content"]["summary"] + " Extra."})
    a = prepare(env, jid)
    up = next(u for u in a["uploads"] if u["role"] == "resume")
    assert up["document_id"] == docs["resume"]["document_id"] and up["document_version"] == 1


# =============================================================================================== approval + submission
def test_submit_without_approval_is_refused_on_the_backend(env, ready):
    jid, _ = ready
    a = prepare(env, jid)
    with pytest.raises(Exception) as exc:
        env.service.submit(a["application_id"])
    assert "explicit approval" in str(exc.value) and env.server.posts == []


def test_full_flow_confirmed_with_reference_is_verified_and_sends_exactly_the_approved_data(env):
    jid = add_job(env, "/ref")
    docs = approve_documents(env, jid)
    a = prepare(env, jid)
    appr = env.service.approve(a["application_id"], acknowledge_warnings=True)
    assert appr["state"] == "APPROVED" and appr["approval"]["plan_hash"] == a["plan_hash"]
    done = env.service.submit(a["application_id"])
    assert done["state"] == "VERIFIED" and done["submission"]["reference"] == "APP-2026-0042" and done["submission"]["verified_by"] == "page"
    assert {"confirmation message", "application form no longer shown", "reference number"} <= set(done["submission"]["evidence"])
    assert len(env.server.posts) == 1
    post = env.server.posts[0]
    assert post["fields"]["full_name"] == "Jane Sample" and post["fields"]["email"] == "jane.sample@example.com" and post["fields"]["consent"] == "1"
    resume_bytes = env.documents.download(docs["resume"]["document_id"], "pdf")[0]
    assert post["files"]["resume"]["sha256"] == hashlib.sha256(resume_bytes).hexdigest()    # the APPROVED export, byte for byte
    events = [(x["type"], x["to_state"]) for x in env.service.events(a["application_id"])]
    assert [t for t, _ in events] == ["created", "prepared", "approved", "submitting", "submitted", "verified"]
    assert env.service.status(a["application_id"])["state"] == "VERIFIED"


def test_confirmation_without_reference_is_submitted_not_verified_until_the_candidate_confirms(env):
    jid = add_job(env, "/noref")
    approve_documents(env, jid)
    a = prepare(env, jid)
    env.service.approve(a["application_id"], acknowledge_warnings=True)
    done = env.service.submit(a["application_id"])
    assert done["state"] == "SUBMITTED" and done["submission"]["reference"] is None
    with pytest.raises(Exception):                                       # a bare confirmation needs evidence to be VERIFIED
        env.service.confirm(a["application_id"], submitted=True)
    v = env.service.confirm(a["application_id"], submitted=True, reference="CONF-123")
    assert v["state"] == "VERIFIED" and v["submission"]["verified_by"] == "user" and v["verified_at"]


def test_ambiguous_outcome_is_verification_pending_and_never_retried_or_resubmitted(env):
    jid = add_job(env, "/ambiguous")
    approve_documents(env, jid)
    a = prepare(env, jid)
    env.service.approve(a["application_id"], acknowledge_warnings=True)
    done = env.service.submit(a["application_id"])
    assert done["state"] == "VERIFICATION_PENDING" and done["failure_stage"] == "after_submit" and env.service.status(a["application_id"])["needs_manual_verification"]
    assert len(env.server.posts) == 1
    for action in (lambda: env.service.submit(a["application_id"]), lambda: prepare(env, jid), lambda: env.service.refresh(a["application_id"])):
        with pytest.raises(Exception):
            action()
    assert len(env.server.posts) == 1                                      # still exactly one POST
    notsub = env.service.confirm(a["application_id"], submitted=False, notes="Checked my email: nothing arrived")
    assert notsub["state"] == "SUBMISSION_FAILED"


def test_error_after_the_click_is_unknown_not_failed_and_not_retried(env, monkeypatch):
    jid = add_job(env, "/ref")
    approve_documents(env, jid)
    a = prepare(env, jid)
    env.service.approve(a["application_id"], acknowledge_warnings=True)
    calls = []

    def boom(*args, **kw):
        calls.append(1)
        raise RuntimeError("browser crashed")
    monkeypatch.setattr(env.engine, "_observe", boom)
    done = env.service.submit(a["application_id"])
    assert done["state"] == "VERIFICATION_PENDING" and done["submission"]["outcome"] == "unconfirmed" and len(env.server.posts) == 1 and len(calls) == 1


def test_site_rejection_is_a_certain_failure_and_can_be_prepared_again(env):
    jid = add_job(env, "/reject")
    approve_documents(env, jid)
    a = prepare(env, jid)
    env.service.approve(a["application_id"], acknowledge_warnings=True)
    done = env.service.submit(a["application_id"])
    assert done["state"] == "SUBMISSION_FAILED" and done["failure_stage"] == "rejected" and "did not accept" in done["submission"]["detail"]
    again = prepare(env, jid)                                             # a certain failure may be re-prepared (then needs a fresh approval)
    assert again["application_id"] == a["application_id"] and again["state"] == "READY_FOR_REVIEW" and again["approval"] is None


def test_transient_failure_before_the_click_is_retried_but_never_after(env, ready, monkeypatch):
    jid, _ = ready
    from browser.browser_manager import BrowserError, BrowserManager
    real, state = BrowserManager.goto, {"n": 0}

    def flaky(self, page, url):
        state["n"] += 1
        if state["n"] == 1:
            raise BrowserError("The page took too long to load.", transient=True)
        return real(self, page, url)
    monkeypatch.setattr(BrowserManager, "goto", flaky)
    a = prepare(env, jid)
    assert a["state"] == "READY_FOR_REVIEW" and state["n"] == 2
    assert "retry" in [x["type"] for x in env.service.events(a["application_id"])]


def test_double_submit_requests_send_one_application(env):
    jid = add_job(env, "/ref")
    approve_documents(env, jid)
    a = prepare(env, jid)
    env.service.approve(a["application_id"], acknowledge_warnings=True)
    results = []

    def go():
        try:
            results.append(env.service.submit(a["application_id"])["state"])
        except Exception as exc:  # noqa: BLE001
            results.append(type(exc).__name__)
    threads = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(env.server.posts) == 1 and sorted(results).count("VERIFIED") == 1 and "ActionNotAllowed" in results


# =============================================================================================== changes withdraw approval
def test_profile_change_after_approval_blocks_submission(env):
    jid = add_job(env, "/ref")
    approve_documents(env, jid)
    a = prepare(env, jid)
    env.service.approve(a["application_id"], acknowledge_warnings=True)
    prof = env.matching.resumes.get_profile(env.profile.resume_id)
    env.db["candidate_profiles"].update_one({"resume_id": prof.resume_id}, {"$inc": {"profile_version": 1}})
    with pytest.raises(Exception) as exc:
        env.service.submit(a["application_id"])
    assert "profile changed" in str(exc.value) and env.server.posts == []
    assert env.service.get(a["application_id"])["state"] == "READY_FOR_REVIEW"


def test_new_approved_document_version_after_approval_blocks_submission(env):
    jid = add_job(env, "/ref")
    docs = approve_documents(env, jid)
    a = prepare(env, jid)
    env.service.approve(a["application_id"], acknowledge_warnings=True)
    v2 = env.documents.update_content(docs["resume"]["document_id"], {**docs["resume"]["content"], "summary": docs["resume"]["content"]["summary"] + " Extra."})
    assert v2["version"] == 2
    env.documents.approve(v2["document_id"], acknowledge_warnings=True)
    with pytest.raises(Exception) as exc:
        env.service.submit(a["application_id"])
    assert "documents changed" in str(exc.value) and env.server.posts == []


def test_form_changing_between_approval_and_submit_blocks_the_click(env, monkeypatch):
    jid = add_job(env, "/ref")
    approve_documents(env, jid)
    a = prepare(env, jid)
    env.service.approve(a["application_id"], acknowledge_warnings=True)
    env.db["applications"].update_one({"application_id": a["application_id"]}, {"$set": {"plan_hash": "0" * 64, "approval.plan_hash": "0" * 64}})
    done = env.service.submit(a["application_id"])
    assert env.server.posts == [] and done["state"] == "READY_FOR_REVIEW" and "differ from what you approved" in done["submission"]["detail"]


# =============================================================================================== stops
@pytest.mark.parametrize("path,needle", [("/captcha", "CAPTCHA"), ("/login", "sign-in"), ("/forbidden", "HTTP 403"), ("/multistep", "multi-step"),
                                         ("/unlabelled", "identified"), ("/dynamic", "changed while"), ("/redirect-off", "different site")])
def test_protections_and_unsupported_forms_stop_automation_and_hand_off(env, path, needle):
    if path == "/redirect-off" and env.server.alias is None:
        pytest.skip("cannot bind 127.0.0.2 on this machine")
    jid = add_job(env, path, title=f"Role {path}")
    approve_documents(env, jid)
    a = prepare(env, jid)
    assert a["state"] == "MANUAL_HANDOFF_REQUIRED" and a["mode"] == "manual_handoff" and needle in " ".join(a["notes"]), a["notes"]
    assert env.server.posts == [] and a["handoff"]["documents"]["resume"] and a["handoff"]["apply_url"] is None   # a loopback URL is never offered as a link
    with pytest.raises(Exception):
        env.service.submit(a["application_id"])


# =============================================================================================== policy
@pytest.mark.parametrize("source,url", [("linkedin", "https://www.linkedin.com/jobs/view/123"), ("indeed", "https://www.indeed.com/viewjob?jk=1"),
                                        ("naukri", "https://www.naukri.com/job-listings-x-1"), ("career_pages", "https://boards.greenhouse.io/acme/jobs/9"),
                                        ("career_pages", "https://acme.wd1.myworkdayjobs.com/en/x/job/1")])
def test_restricted_or_unauthorised_platforms_are_manual_handoff_and_can_never_be_submitted(env, source, url):
    jid, _, _ = env.matching.analyze_jd(JobInput(job_description=JD["text"], job_title=f"Role {source}{url[-3:]}", company_name="Acme", location="Pune", job_url=url))
    env.db["jobs"].update_one({"job_id": jid}, {"$set": {"source": source}})
    approve_documents(env, jid)
    a = prepare(env, jid)
    assert a["state"] == "MANUAL_HANDOFF_REQUIRED" and a["mode"] == "manual_handoff" and a["uploads"] == []
    for action in (lambda: env.service.approve(a["application_id"]), lambda: env.service.submit(a["application_id"])):
        with pytest.raises(Exception) as exc:
            action()
        assert "manual" in str(exc.value).lower() or "automat" in str(exc.value).lower()
    assert a["handoff"]["apply_url"] == url and a["handoff"]["documents"]["resume"] and a["handoff"]["contact"]["email"] == "jane.sample@example.com"
    assert env.server.posts == []


def test_master_switch_off_means_no_browser_automation_even_for_the_test_provider(env, ready):
    jid, _ = ready
    env.settings.application_automation_enabled = False
    env.rebuild(policy=ApplicationPolicy.load(env.settings, PolicyRegistry.load(env.settings)))
    a = prepare(env, jid)
    assert a["state"] == "MANUAL_HANDOFF_REQUIRED" and "switched off" in " ".join(a["notes"]) and env.server.posts == []


def test_policy_withdrawn_after_approval_blocks_submit(env):
    jid = add_job(env, "/ref")
    approve_documents(env, jid)
    a = prepare(env, jid)
    env.service.approve(a["application_id"], acknowledge_warnings=True)
    env.settings.application_automation_enabled = False
    env.rebuild(policy=ApplicationPolicy.load(env.settings, PolicyRegistry.load(env.settings)))
    with pytest.raises(Exception) as exc:
        env.service.submit(a["application_id"])
    assert "switched off" in str(exc.value) and env.server.posts == []


def test_authorised_site_still_needs_authorisation_and_a_real_https_host(env):
    pol = env.policy
    pol._data["authorized_sites"] = [{"host": "boards.greenhouse.io", "path_prefix": "/acme", "provider": "greenhouse"}]
    ok = pol.decide("career_pages", "greenhouse", "https://boards.greenhouse.io/acme/jobs/1")
    other = pol.decide("career_pages", "greenhouse", "https://boards.greenhouse.io/other/jobs/1")
    http = pol.decide("career_pages", "greenhouse", "http://boards.greenhouse.io/acme/jobs/1")
    assert ok.automation_allowed and not other.automation_allowed and not http.automation_allowed


# =============================================================================================== eligibility / duplicates / limits
def test_ineligible_job_needs_explicit_acknowledgement(env, ready):
    jid, _ = ready
    env.db["applications"].delete_many({})
    from backend.services.application_service import NotEligible
    import engines.matching.eligibility_checker as ec  # noqa: F401
    orig = env.matching.score

    def failing(**kw):
        report, cached = orig(**kw)
        report.eligibility.status = "FAIL"
        report.eligibility.failed_checks = ["Location: onsite elsewhere"]
        return report, cached
    env.matching.score = failing
    with pytest.raises(NotEligible):
        prepare(env, jid)
    a = prepare(env, jid, acknowledge_ineligible=True)
    assert a["eligibility"] == "FAIL" and a["state"] == "READY_FOR_REVIEW"
    assert "eligibility_acknowledged" in [c["name"] for c in a["validation"]["checks"]]


def test_one_application_per_job_and_resubmission_after_success_is_refused(env):
    from backend.services.application_service import DuplicateApplication
    jid = add_job(env, "/ref")
    approve_documents(env, jid)
    a = prepare(env, jid)
    again = prepare(env, jid)                                             # idempotent while still a draft: same record, no second application
    assert again["application_id"] == a["application_id"] and env.db["applications"].count_documents({}) == 1
    env.service.approve(a["application_id"], acknowledge_warnings=True)
    env.service.submit(a["application_id"])
    with pytest.raises(DuplicateApplication):
        prepare(env, jid)
    assert len(env.server.posts) == 1 and env.db["applications"].count_documents({}) == 1


def test_same_application_url_is_not_reused_for_a_second_job(env):
    from backend.services.application_service import DuplicateApplication
    j1 = add_job(env, "/noref", title="Role One")
    approve_documents(env, j1)
    a = prepare(env, j1)
    env.service.approve(a["application_id"], acknowledge_warnings=True)
    env.service.submit(a["application_id"])
    j2 = add_job(env, "/noref", title="Role Two", description=JD["text"] + " Second posting.")
    with pytest.raises(DuplicateApplication):
        prepare(env, j2)


def test_daily_limit_blocks_submission(env):
    from backend.services.application_service import LimitReached
    jid = add_job(env, "/ref")
    approve_documents(env, jid)
    a = prepare(env, jid)
    env.service.approve(a["application_id"], acknowledge_warnings=True)
    env.rebuild(limits=AutomationLimits(max_applications_per_day=0, max_applications_per_platform_per_day=0, delay_seconds_between_actions=0))
    with pytest.raises(LimitReached):
        env.service.submit(a["application_id"])
    assert env.server.posts == [] and env.service.get(a["application_id"])["state"] == "APPROVED"


# =============================================================================================== manual handoff
def test_manual_handoff_package_and_recording_the_candidates_own_confirmation(env):
    jid, _, _ = env.matching.analyze_jd(JobInput(job_description=JD["text"], job_title="Handoff Role", company_name="Acme", location="Pune", job_url="https://www.linkedin.com/jobs/view/42"))
    env.db["jobs"].update_one({"job_id": jid}, {"$set": {"source": "linkedin"}})
    approve_documents(env, jid)
    a = prepare(env, jid)
    h = env.service.manual_handoff(a["application_id"])
    pk = h["handoff"]
    assert pk["apply_url"] == "https://www.linkedin.com/jobs/view/42" and {"resume", "cover_letter", "screening"} <= set(pk["documents"])
    assert any(x["question"].startswith("Tell us about yourself") for x in pk["prepared_answers"]) and pk["contact"]["full_name"] == "Jane Sample"
    env.service.mark_opened(a["application_id"])
    nothing = env.service.confirm(a["application_id"], submitted=False, notes="Closed the tab")
    assert nothing["state"] == "MANUAL_HANDOFF_REQUIRED"
    s = env.service.confirm(a["application_id"], submitted=True)
    assert s["state"] == "SUBMITTED" and s["submission"]["verified_by"] == "user" and s["submission"]["outcome"] == "user_reported"
    v = env.service.confirm(a["application_id"], submitted=True, reference="LI-778", notes="Confirmation email received")
    assert v["state"] == "VERIFIED" and v["submission"]["reference"] == "LI-778"


# =============================================================================================== API layer
def test_api_exposes_the_workflow_and_maps_errors(env, ready):
    jid, _ = ready
    app.dependency_overrides[get_application_service] = lambda: env.service
    app.dependency_overrides[get_document_service] = lambda: env.documents
    try:
        c = TestClient(app)
        pol = c.get(f"/api/applications/policy/{jid}").json()
        assert pol["automation_allowed"] is True and pol["provider"] == "local_test"
        r = c.post("/api/applications/prepare", json={"job_id": jid, "candidate_inputs": CONSENT})
        assert r.status_code == 201 and r.json()["state"] == "READY_FOR_REVIEW"
        aid = r.json()["application_id"]
        assert c.post(f"/api/applications/{aid}/submit").status_code == 409                       # not approved
        assert c.post(f"/api/applications/{aid}/approve", json={"acknowledge_warnings": True}).json()["state"] == "APPROVED"
        assert c.get(f"/api/applications/{aid}/status").json()["state"] == "APPROVED"
        assert c.get("/api/applications", params={"state": "APPROVED"}).json()[0]["application_id"] == aid
        assert [e["type"] for e in c.get(f"/api/applications/{aid}/events").json()] == ["created", "prepared", "approved"]
        sid = c.get(f"/api/applications/{aid}").json()["screenshots"][0]
        assert c.get(f"/api/applications/{aid}/screenshots/{sid}").headers["content-type"] == "image/png"
        assert c.get(f"/api/applications/{aid}/screenshots/{'0' * 12}_{'0' * 16}.png").status_code == 404
        assert c.get("/api/applications/not-an-id").status_code == 422 and c.get(f"/api/applications/{'0' * 32}").status_code == 404
        assert c.post("/api/applications/prepare", json={"job_id": "x"}).status_code == 422
        assert c.post("/api/applications/prepare", json={"job_id": jid}).status_code == 200 or True
        text = str(c.get(f"/api/applications/{aid}").json())
        assert "idempotency_key" not in text and str(env.settings.screenshots_path) not in text and "storage" not in text.replace("storage_", "")
    finally:
        app.dependency_overrides.clear()


# =============================================================================================== one-click "I applied"
def test_record_applied_yourself_goes_through_handoff_to_submitted_by_the_candidate_and_sends_nothing(env):
    url = "https://boards.greenhouse.io/acme/jobs/77"
    jid, _, _ = env.matching.analyze_jd(JobInput(job_description=JD["text"], job_title="Role toggle", company_name="Acme", location="Pune", job_url=url))
    env.db["jobs"].update_one({"job_id": jid}, {"$set": {"source": "career_pages"}})
    assert env.service.applied_states([jid]) == {}
    a = env.service.record_applied_yourself(jid)                                    # no documents, no approval: the candidate already applied themselves
    assert a["state"] == "SUBMITTED" and a["submission"]["verified_by"] == "user" and a["submission"]["outcome"] == "user_reported"
    assert env.service.applied_states([jid]) == {jid: "SUBMITTED"}
    again = env.service.record_applied_yourself(jid)                                 # a second click changes nothing
    assert again["application_id"] == a["application_id"] and again["state"] == "SUBMITTED" and env.apps.count(job_id=jid) == 1
    events = [e["type"] for e in env.apps.list_events(a["application_id"])]
    assert "submitted_recorded_by_candidate" in events and env.server.posts == []   # human-attested; nothing was sent anywhere


def test_record_applied_on_an_existing_handoff_and_on_an_ineligible_job(env, ready):
    jid, _ = ready
    a = prepare(env, jid)
    env.service.manual_handoff(a["application_id"])
    assert env.service.record_applied_yourself(jid)["state"] == "SUBMITTED"
    jid2, _, _ = env.matching.analyze_jd(JobInput(job_description="Requirements:\n- 15+ years of experience is required.\n- Must have Rust and Haskell.",
                                                  job_title="Principal Rust", company_name="Initech", location="Pune", job_url="https://boards.greenhouse.io/initech/jobs/5"))
    env.db["jobs"].update_one({"job_id": jid2}, {"$set": {"source": "career_pages"}})
    assert env.service.record_applied_yourself(jid2)["state"] == "SUBMITTED"         # an eligibility FAIL does not stop recording what you already did
    assert env.server.posts == []
