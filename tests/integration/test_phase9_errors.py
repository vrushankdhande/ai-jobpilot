"""Phase 9 error handling for external dependencies: AI failures/garbage, source rate limits and timeouts, browser failures, file-processing errors.

Every failure must end as a meaningful status code / safe fallback - never a stack trace, never a half-written state, never a retry that could duplicate an application.
"""
import httpx
import pytest
from fastapi.testclient import TestClient

from backend.api.routes.jobs import get_discovery_service
from backend.api.routes.matching import get_matching_service
from backend.main import app
from backend.services.discovery_service import DiscoveryService
from browser.browser_manager import BrowserUnavailable
from config.scoring_config import load_preferences, load_scoring_config
from config.settings import get_settings
from database.connection import get_database_manager
from database.repositories.discovery_runs_repository import DiscoveryRunsRepository
from database.repositories.jobs_repository import JobsRepository
from engines.discovery.discovery_engine import DiscoveryEngine
from engines.matching.skill_extractor import get_catalog
from integrations.policy import PolicyRegistry
from integrations.registry import build_adapters
from tests.fixtures.fake_ai import make_client
from tests.fixtures.jd_dataset import JDS
from tests.integration.test_application_flow import add_job, approve_documents, env, prepare  # noqa: F401
from tests.integration.test_phase8_backend import FakeManager

pytestmark = pytest.mark.integration


def discovery_client(test_db, handler):
    s = get_settings()
    reg = PolicyRegistry.load(s)
    http = httpx.Client(transport=httpx.MockTransport(handler))
    adapters = build_adapters(s, reg, client=http, sleep=lambda x: None)
    jobs, runs = JobsRepository(test_db), DiscoveryRunsRepository(test_db)
    svc = DiscoveryService(jobs, runs, reg, adapters, DiscoveryEngine(adapters, jobs, runs, reg, get_catalog(s), load_scoring_config(s).seniority.bands), load_preferences(s))
    app.dependency_overrides[get_discovery_service] = lambda: svc
    app.dependency_overrides[get_database_manager] = lambda: FakeManager(test_db)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _clean():
    yield
    app.dependency_overrides.clear()


# ============================================================================================ source rate limits, blocks and timeouts
@pytest.mark.parametrize("status", [401, 403, 429])
def test_a_source_that_refuses_us_is_reported_and_never_retried_or_bypassed(test_db, status):
    calls = []

    def handler(req):
        calls.append(str(req.url.host))
        return httpx.Response(status, text="<html>captcha required</html>")
    c = discovery_client(test_db, handler)
    r = c.post("/api/jobs/import", json={"url": "https://boards.greenhouse.io/acme/jobs/1234"})
    assert r.status_code in (403, 429, 502, 503) and "Traceback" not in r.text and "captcha" not in r.text.lower()
    assert len(calls) == 1 and set(calls) <= {"boards-api.greenhouse.io"}                       # one request to the documented API host; no retry, no other host
    assert test_db["jobs"].count_documents({}) == 0


def test_a_source_timeout_is_a_clean_error_not_a_crash(test_db):
    def handler(req):
        raise httpx.ReadTimeout("slow")
    r = discovery_client(test_db, handler).post("/api/jobs/import", json={"url": "https://boards.greenhouse.io/acme/jobs/1234"})
    assert r.status_code in (502, 503, 504, 422) and "Traceback" not in r.text and "slow" not in r.text
    assert test_db["jobs"].count_documents({}) == 0


def test_a_server_error_from_the_source_does_not_store_a_half_job(test_db):
    r = discovery_client(test_db, lambda req: httpx.Response(503)).post("/api/jobs/import", json={"url": "https://boards.greenhouse.io/acme/jobs/1234"})
    assert r.status_code >= 400 and r.status_code != 500 and test_db["jobs"].count_documents({}) == 0


def test_a_private_or_unlisted_url_is_never_fetched(test_db):
    calls = []
    c = discovery_client(test_db, lambda req: calls.append(str(req.url)) or httpx.Response(200, json={}))
    for url in ("http://169.254.169.254/latest/meta-data/", "http://127.0.0.1:8000/api/settings", "file:///etc/passwd", "gopher://x", "https://user:pw@evil.example.com/x"):
        r = c.post("/api/jobs/import", json={"url": url})
        assert r.status_code in (403, 422), (url, r.status_code)                                 # private / non-http(s) / credentialed URLs are refused outright
    r = c.post("/api/jobs/import", json={"url": "https://evil.example.com/jobs/1", "job": {"job_title": "T", "company_name": "C", "job_description": "d" * 80}})
    assert r.status_code == 201                                                                  # an unlisted public host is only a manual import: the URL is stored, never requested
    assert calls == []                                                                           # nothing outside the allow-list was contacted


# ============================================================================================ AI provider failures
@pytest.fixture
def ai_api(matching_service, seeded):
    def install(script):
        matching_service.ai = make_client(script)
        app.dependency_overrides[get_matching_service] = lambda: matching_service
        return TestClient(app, raise_server_exceptions=False)
    yield install


@pytest.mark.parametrize("script", [
    ["this is not json at all"], ['{"skills": "wrong shape"}'], [""], ['{"skills": [{"name": "Totally Invented Skill", "classification": "mandatory"}]}'],
    [TimeoutError("provider timed out")], [ConnectionError("network down")], [RuntimeError("500 from provider")],
], ids=["not-json", "wrong-shape", "empty", "invented-skill", "timeout", "network", "provider-error"])
def test_invalid_or_failing_ai_output_falls_back_to_the_deterministic_result(ai_api, script):
    c = ai_api(script)
    r = c.post("/api/matching/score", json={"job": {"job_description": JDS[0]["text"], "job_title": JDS[0]["title"], "company_name": "Co"}, "use_ai": True})
    assert r.status_code == 200, r.text[:200]
    rep = r.json()["report"]
    det = ai_api(["{}"]).post("/api/matching/score", json={"job": {"job_description": JDS[0]["text"], "job_title": JDS[0]["title"], "company_name": "Co"}, "use_ai": False}).json()["report"]
    assert rep["match_score"] == det["match_score"] and rep["eligibility"]["status"] == det["eligibility"]["status"]      # the score is the deterministic engine's, whatever the AI said
    assert "Totally Invented Skill" not in str(rep)


# ============================================================================================ browser automation failures
def test_an_unavailable_browser_degrades_to_manual_handoff_without_an_error(env):
    jid = add_job(env, "/apply", title="No Browser")
    approve_documents(env, jid)

    def no_browser(settings, hosts):
        raise BrowserUnavailable("No Chromium-based browser could be started")
    env.engine._factory = no_browser
    a = prepare(env, jid)
    assert a["state"] == "MANUAL_HANDOFF_REQUIRED" and a["mode"] == "manual_handoff" and any("unavailable" in n for n in a["notes"])
    assert env.server.posts == [] and "Traceback" not in str(a)


def test_a_page_load_timeout_is_retried_only_before_submitting_and_then_hands_off(env, monkeypatch):
    from browser.browser_manager import BrowserError, BrowserManager
    jid = add_job(env, "/apply", title="Always Slow")
    approve_documents(env, jid)
    attempts = {"n": 0}

    def slow(self, page, url):
        attempts["n"] += 1
        raise BrowserError("The page took too long to load.", transient=True)
    monkeypatch.setattr(BrowserManager, "goto", slow)
    a = prepare(env, jid)
    assert a["state"] == "MANUAL_HANDOFF_REQUIRED" and 1 < attempts["n"] <= env.settings.max_submit_retries + 2          # bounded retries, then a human takes over
    assert env.server.posts == []


# ============================================================================================ file processing errors through the API
def test_every_resume_parse_failure_is_a_recorded_state_not_a_crash(test_db, tmp_path):
    from backend.api.routes.resumes import get_resume_service
    from backend.services.resume_service import ResumeService
    from database.repositories.resumes_repository import ResumesRepository
    from database.repositories.users_repository import UsersRepository
    s = get_settings().model_copy(update={"resume_storage_dir": str(tmp_path / "r")})
    svc = ResumeService(ResumesRepository(test_db), UsersRepository(test_db), s, storage_dir=tmp_path / "r")
    app.dependency_overrides[get_resume_service] = lambda: svc
    c = TestClient(app, raise_server_exceptions=False)
    pdf_without_text = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF"
    r = c.post("/api/resumes/upload", files={"file": ("blank.pdf", pdf_without_text, "application/pdf")})
    assert r.status_code < 500 and "Traceback" not in r.text
    if r.status_code in (200, 201):
        assert r.json()["resume"]["parsing_status"] in ("failed", "needs_ocr", "completed")
    assert c.get("/api/resumes/" + "0" * 32 + "/profile").status_code == 404 and c.post("/api/resumes/" + "0" * 32 + "/parse").status_code == 404
