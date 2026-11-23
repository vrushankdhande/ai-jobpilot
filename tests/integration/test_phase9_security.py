"""Phase 9 security + error-handling probes. Each test states the property it proves; fixes made during Phase 9 are listed in docs/TESTING_REPORT.md.

Real throw-away MongoDB for the data paths; failure paths use fakes that raise exactly what pymongo raises. No real application is ever submitted.
"""
import io
import json
import logging
import re
import time
import zipfile

import pytest
from fastapi.testclient import TestClient
from pymongo.errors import AutoReconnect, NetworkTimeout, ServerSelectionTimeoutError

from backend.main import app, create_app
from config.settings import Settings, get_settings
from database.connection import DatabaseConnectionError, DatabaseManager, get_database_manager
from tests.integration.test_phase8_backend import FakeManager

pytestmark = [pytest.mark.integration, pytest.mark.safety]
SECRETS = {"anthropic": "sk-ant-PROBE-SECRET-123456", "admin": "probe-admin-token-0123456789", "secretkey": "probe-secret-key-0123456789ab", "dbpw": "dbPassw0rdPROBE"}


# ============================================================================================ settings never leak secrets
def test_settings_repr_str_and_logging_never_contain_secrets():
    s = Settings(anthropic_api_key=SECRETS["anthropic"], admin_token=SECRETS["admin"], secret_key=SECRETS["secretkey"], mongodb_uri=f"mongodb://user:{SECRETS['dbpw']}@db:27017/",
                 _env_file=None)
    for rendering in (repr(s), str(s), f"{s!r}", repr(s.model_copy()), str(s.model_dump(exclude={"anthropic_api_key", "admin_token", "secret_key", "mongodb_uri"}))):
        for secret in SECRETS.values():
            assert secret not in rendering, (secret, rendering[:80])


def test_a_failed_database_connection_never_logs_or_raises_the_credentials(caplog):
    caplog.set_level(logging.DEBUG)
    m = DatabaseManager()
    with pytest.raises(DatabaseConnectionError) as exc:
        m.connect(f"mongodb://probeuser:{SECRETS['dbpw']}@127.0.0.1:1/?serverSelectionTimeoutMS=200", "x", timeout_ms=300)
    assert SECRETS["dbpw"] not in str(exc.value) and SECRETS["dbpw"] not in caplog.text and "probeuser" not in caplog.text and "mongodb://" not in caplog.text


# ============================================================================================ database failure handling
class _Boom:
    """A 'database' whose every operation fails like a lost MongoDB connection (message carries a URI with a password)."""

    def __init__(self, exc):
        self.exc = exc

    def __getitem__(self, name):
        return self

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        if name == "database":                  # some repositories reach the database through a collection
            return self

        def fail(*a, **k):
            raise self.exc
        return fail


class _BoomManager:
    def __init__(self, exc):
        self.db = _Boom(exc)

    def get_database(self):
        return self.db

    def ping(self):
        return False


DB_GET = ["/api/jobs", "/api/jobs/page", "/api/applications", "/api/applications/page", "/api/interviews", "/api/dashboard/summary", "/api/analytics/overview", "/api/queue",
          "/api/matching/results", "/api/resumes", "/api/documents", "/api/scheduler/status", "/api/notifications", "/api/reports/daily", "/api/settings"]
LOST = [AutoReconnect("mongodb://u:dbPassw0rdPROBE@h:27017 connection lost"), ServerSelectionTimeoutError("mongodb://u:dbPassw0rdPROBE@h timed out"),
        NetworkTimeout("mongodb://u:dbPassw0rdPROBE@h socket timed out")]


@pytest.fixture
def broken_db():
    def install(exc):
        app.dependency_overrides[get_database_manager] = lambda: _BoomManager(exc)
        return TestClient(app, raise_server_exceptions=False)
    yield install
    app.dependency_overrides.clear()


@pytest.mark.parametrize("path", DB_GET)
@pytest.mark.parametrize("exc", LOST, ids=lambda e: type(e).__name__)
def test_a_lost_database_connection_is_a_503_without_internals(broken_db, path, exc):
    r = broken_db(exc).get(path)
    assert r.status_code == 503, (path, r.status_code, r.text[:120])
    body = r.text
    assert SECRETS["dbpw"] not in body and "mongodb://" not in body and "Traceback" not in body and "pymongo" not in body.lower()


def test_unexpected_server_errors_are_a_generic_500_that_is_logged_without_the_message(broken_db, caplog):
    caplog.set_level(logging.ERROR)
    c = broken_db(RuntimeError(f"boom with {SECRETS['dbpw']}"))
    r = c.get("/api/jobs")
    assert r.status_code == 500 and r.json() == {"detail": "Internal server error"} and SECRETS["dbpw"] not in r.text
    assert "Unhandled error" in caplog.text


def test_health_database_reports_unavailable_with_503(broken_db):
    r = broken_db(AutoReconnect("x")).get("/health/database")
    assert r.status_code == 503 and r.json()["database"] == "disconnected"


def test_the_manager_reconnects_by_itself_after_the_database_comes_back(monkeypatch):
    calls = {"n": 0}

    class FakeClient:
        def __init__(self, *a, **k):
            calls["n"] += 1
            self.admin = self
            self.up = calls["n"] >= 2

        def command(self, *_):
            if not self.up:
                raise ServerSelectionTimeoutError("down")

        def __getitem__(self, n):
            return {"db": n}

        def close(self):
            pass
    monkeypatch.setattr("database.connection.MongoClient", FakeClient)
    t = [0.0]
    m = DatabaseManager(clock=lambda: t[0])
    with pytest.raises(DatabaseConnectionError):
        m.connect("mongodb://x", "jobpilot")
    with pytest.raises(DatabaseConnectionError):
        m.get_database()                                          # throttled: no immediate second attempt storm
    assert calls["n"] == 1
    t[0] += 10
    assert m.get_database() == {"db": "jobpilot"} and calls["n"] == 2                       # recovered without restarting the process


# ============================================================================================ HTTP hardening
def test_security_headers_and_no_store_on_api_responses():
    c = TestClient(app)
    for path in ("/health", "/health/database"):
        h = c.get(path).headers
        assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY" and h["referrer-policy"] == "no-referrer"
    assert "no-store" in c.get("/health").headers.get("cache-control", "")


def test_oversized_request_bodies_are_refused_early():
    c = TestClient(app)
    big = "x" * (30 * 1024 * 1024)
    r = c.post("/api/matching/analyze-jd", content=json.dumps({"job_description": big}), headers={"content-type": "application/json"})
    assert r.status_code == 413 and "too large" in r.json()["detail"].lower()


def test_api_docs_are_not_exposed_in_production(monkeypatch):
    prod = get_settings().model_copy(update={"app_env": "production"})
    monkeypatch.setattr("backend.main.get_settings", lambda: prod)
    p = TestClient(create_app())
    assert p.get("/docs").status_code == 404 and p.get("/redoc").status_code == 404 and p.get("/openapi.json").status_code == 404
    assert TestClient(app).get("/docs").status_code == 200                                   # development keeps them


def test_production_refuses_to_start_without_an_admin_token_and_secret_key(monkeypatch):
    from config.production_checks import production_problems
    base = get_settings().model_copy(update={"app_env": "production", "admin_token": "", "secret_key": "", "application_automation_enabled": False, "debug": False, "allow_local_test_forms": False})
    probs = production_problems(base)
    assert any("ADMIN_TOKEN" in p for p in probs) and any("SECRET_KEY" in p for p in probs)
    ok = base.model_copy(update={"admin_token": "a" * 32, "secret_key": "b" * 32})
    assert production_problems(ok) == []
    assert production_problems(base.model_copy(update={"app_env": "development"})) == []
    weak = ok.model_copy(update={"admin_token": "short"})
    assert any("ADMIN_TOKEN" in p for p in production_problems(weak))
    monkeypatch.setattr("backend.main.get_settings", lambda: base)
    with pytest.raises(RuntimeError, match="production"):
        with TestClient(create_app()):
            pass


def test_the_irreversible_application_actions_need_the_admin_token_when_one_is_configured(monkeypatch):
    from backend.api.routes.applications import get_application_service
    from unittest.mock import MagicMock
    s = get_settings().model_copy(update={"admin_token": SECRETS["admin"]})
    svc = MagicMock()
    app.dependency_overrides[get_settings] = lambda: s
    app.dependency_overrides[get_application_service] = lambda: svc
    try:
        c = TestClient(app)
        aid = "a" * 32
        for path in (f"/api/applications/{aid}/submit", f"/api/applications/{aid}/approve"):
            assert c.post(path, json={}).status_code == 401 and c.post(path, json={}, headers={"X-Admin-Token": "nope"}).status_code == 401
        svc.submit.assert_not_called()
        svc.approve.assert_not_called()
        assert c.post(f"/api/applications/{aid}/submit", headers={"X-Admin-Token": SECRETS["admin"]}).status_code != 401
    finally:
        app.dependency_overrides.clear()


# ============================================================================================ input validation
@pytest.fixture
def api(test_db):
    app.dependency_overrides[get_database_manager] = lambda: FakeManager(test_db)
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


def test_invalid_requests_return_422_never_500(api):
    cases = [("post", "/api/interviews", {"json": {}}), ("post", "/api/interviews", {"content": b"{not json", "headers": {"content-type": "application/json"}}),
             ("post", "/api/interviews", {"json": {"company_name": {"$ne": ""}, "job_title": "T", "scheduled_at": "2026-11-20T10:00:00"}}),
             ("post", "/api/interviews", {"json": {"company_name": ["a"], "job_title": "T", "scheduled_at": "2026-11-20T10:00:00"}}),
             ("post", "/api/matching/analyze-jd", {"json": {"job_description": None}}), ("post", "/api/matching/analyze-jd", {"json": {"job_description": 123}}),
             ("post", "/api/applications/prepare", {"json": {"job_id": {"$gt": ""}}}), ("get", "/api/jobs", {"params": {"limit": "abc"}}), ("get", "/api/jobs", {"params": {"limit": -1}}),
             ("get", "/api/jobs/page", {"params": {"min_match_score": "x"}}), ("get", "/api/interviews", {"params": {"date_from": "yesterday"}}),
             ("get", "/api/analytics/overview", {"params": {"date_from": "2026-13-45"}}), ("put", "/api/settings/preferences", {"json": {"values": "no"}})]
    for method, path, kw in cases:
        r = getattr(api, method)(path, **kw)
        assert r.status_code in (401, 403, 422), (method, path, r.status_code, r.text[:100])
        assert "Traceback" not in r.text and "pymongo" not in r.text.lower()


def test_nosql_and_regex_injection_in_filters_is_inert(api, test_db):
    from tests.integration.test_phase8_backend import add_job
    add_job(test_db, 1, title="ML Engineer")
    for payload in ("(", "[", ".*", "^$|^", "{$ne:1}", "\\", "a" * 90, "' OR 1=1 --", "$where: 1"):
        for path, key in (("/api/jobs", "title"), ("/api/jobs/page", "company"), ("/api/applications/page", "q"), ("/api/interviews", "q")):
            r = api.get(path, params={key: payload})
            assert r.status_code == 200, (path, payload, r.status_code)
    assert api.get("/api/jobs/page", params={"title": ".*"}).json()["total"] == 0                    # a regex is matched literally, not interpreted


def test_oversized_job_descriptions_are_rejected_cleanly(api):
    r = api.post("/api/matching/analyze-jd", json={"job_description": "x" * 120_000, "job_title": "T"})
    assert r.status_code in (413, 422) and "Traceback" not in r.text


# ============================================================================================ upload restrictions
@pytest.fixture
def uploader(test_db, tmp_path):
    from backend.api.routes.resumes import get_resume_service
    from backend.services.resume_service import ResumeService
    from database.repositories.resumes_repository import ResumesRepository
    from database.repositories.users_repository import UsersRepository
    s = get_settings().model_copy(update={"resume_storage_dir": str(tmp_path / "resumes")})
    svc = ResumeService(ResumesRepository(test_db), UsersRepository(test_db), s, storage_dir=tmp_path / "resumes")
    app.dependency_overrides[get_resume_service] = lambda: svc
    c = TestClient(app, raise_server_exceptions=False)

    def up(name, data, ctype="application/octet-stream"):
        return c.post("/api/resumes/upload", files={"file": (name, data, ctype)})
    up.dir, up.db = tmp_path / "resumes", test_db
    yield up
    app.dependency_overrides.clear()


def docx_bomb(mb: int) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        with z.open("word/document.xml", "w") as f:
            chunk = b"<w:p>" + b"A" * 1_000_000 + b"</w:p>"
            for _ in range(mb):
                f.write(chunk)
    return buf.getvalue()


@pytest.mark.parametrize("name,data,expected", [
    ("resume.exe", b"MZ\x90\x00" + b"\x00" * 200, 415), ("resume.pdf", b"MZ\x90\x00 this is an executable", 415), ("resume.docx", b"not a zip at all", 415),
    ("resume.docx", b"PK\x03\x04garbage-not-really-a-zip", 415), ("resume.pdf.exe", b"%PDF-1.4 x", 415), ("resume", b"%PDF-1.4 x", 415), ("resume.pdf", b"", 400),
    ("resume.html", b"<script>alert(1)</script>", 415), ("resume.svg", b"<svg onload=alert(1)>", 415), ("resume.docm", b"PK\x03\x04", 415), ("..\\..\\evil.pdf.php", b"%PDF-1.4", 415),
])
def test_malicious_or_wrong_uploads_are_rejected_and_nothing_is_stored(uploader, name, data, expected):
    r = uploader(name, data)
    assert r.status_code == expected, (name, r.status_code, r.text[:100])
    assert not list(uploader.dir.glob("*")) and uploader.db["resumes"].count_documents({}) == 0


def test_an_oversized_upload_is_refused_with_413(uploader):
    r = uploader("big.pdf", b"%PDF-1.4\n" + b"0" * (11 * 1024 * 1024))
    assert r.status_code == 413 and not list(uploader.dir.glob("*"))


def test_a_decompression_bomb_docx_is_rejected_quickly(uploader):
    bomb = docx_bomb(60)                                                    # ~60 MB of XML in a ~60 KB file
    assert len(bomb) < 500_000
    t = time.perf_counter()
    r = uploader("bomb.docx", bomb)
    assert r.status_code == 413 and time.perf_counter() - t < 5 and not list(uploader.dir.glob("*"))


def test_path_traversal_and_odd_filenames_never_reach_the_filesystem_path(uploader, sample_pdf):
    data = sample_pdf.read_bytes()
    for name in ("../../../evil.pdf", "..\\..\\evil.pdf", "C:\\Windows\\evil.pdf", "re\x00sume.pdf", "résumé‮fdp.pdf", "a" * 400 + ".pdf", "con.pdf", "<img src=x>.pdf"):
        r = uploader(name, data)
        assert r.status_code in (200, 201), (name, r.text[:100])
    stored = [p.name for p in uploader.dir.rglob("*") if p.is_file()]
    assert stored and all(re.fullmatch(r"[0-9a-f]{32}\.(pdf|docx)", n) or re.fullmatch(r"[0-9a-f-]{20,}\.pdf", n) for n in stored), stored
    assert not (uploader.dir.parent / "evil.pdf").exists()
    shown = {d["original_filename"] for d in uploader.db["resumes"].find({}, {"original_filename": 1})}
    assert all("/" not in n and "\\" not in n and "\x00" not in n and len(n) <= 120 for n in shown)


def test_a_corrupt_or_encrypted_pdf_is_handled_without_a_500(uploader):
    for data in (b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF", b"%PDF-1.7\n" + b"\x00" * 500, b"%PDF-1.4\n/Encrypt <</Filter /Standard>>\n%%EOF"):
        r = uploader("odd.pdf", data)
        assert r.status_code < 500, (r.status_code, r.text[:120])


def test_a_pdf_with_javascript_is_only_read_as_text(uploader, tmp_path):
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Jane Tester\njane.tester@example.com\nPython developer with five years of experience building APIs.\n" * 6)
    pdf = doc.tobytes()
    doc.close()
    pdf = pdf.replace(b"%%EOF", b"1000 0 obj<</S/JavaScript/JS(app.alert('x'))>>endobj\n%%EOF")
    r = uploader("js.pdf", pdf)
    assert r.status_code < 500
    assert "alert" not in json.dumps(uploader.db["candidate_profiles"].find_one({}, {"_id": 0}) or {}, default=str)


# ============================================================================================ logging hygiene across a real flow
def test_a_full_flow_logs_no_personal_data_and_no_secret(api, test_db, sample_pdf, tmp_path):
    """Runs the real flow with the PRODUCTION logging setup at DEBUG level and inspects the log FILE."""
    from backend.core.logging_config import setup_logging
    log = tmp_path / "app.log"
    s = get_settings().model_copy(update={"resume_storage_dir": str(tmp_path / "r"), "generated_storage_dir": str(tmp_path / "g"), "screenshots_dir": str(tmp_path / "s"),
                                           "admin_token": SECRETS["admin"], "anthropic_api_key": SECRETS["anthropic"], "secret_key": SECRETS["secretkey"], "log_file": str(log), "log_level": "DEBUG"})
    setup_logging(s)
    app.dependency_overrides[get_settings] = lambda: s
    try:
        up = api.post("/api/resumes/upload", files={"file": ("sample_resume.pdf", sample_pdf.read_bytes(), "application/pdf")})
        assert up.status_code in (200, 201)
        rid = up.json()["resume"]["resume_id"]
        prof = api.get(f"/api/resumes/{rid}/profile").json()
        prof["review_status"] = "reviewed"
        api.put(f"/api/resumes/{rid}/profile", json=prof)
        from tests.fixtures.jd_dataset import JDS
        jid = api.post("/api/matching/analyze-jd", json={"job_description": JDS[0]["text"], "job_title": JDS[0]["title"], "company_name": "Northwind AI"}).json()["job_id"]
        api.post("/api/matching/score", json={"job_id": jid})
        api.post("/api/documents/resume/generate", json={"job_id": jid})
        api.post("/api/applications/prepare", json={"job_id": jid, "acknowledge_ineligible": True})
        api.put("/api/settings/preferences", json={"values": {}}, headers={"X-Admin-Token": SECRETS["admin"]})
        api.get("/api/scheduler/status")
        logging.getLogger("probe").error("db failure %s key=%s mail jane.sample@example.com", f"mongodb://u:{SECRETS['dbpw']}@h/", SECRETS["anthropic"])
        for h in logging.getLogger().handlers:
            h.flush()
        text = log.read_text(encoding="utf-8")
    finally:
        app.dependency_overrides.pop(get_settings, None)
        for h in list(logging.getLogger().handlers):
            if getattr(h, "_jobpilot_handler", False):
                logging.getLogger().removeHandler(h)
                h.close()
    pii = [prof["contact"].get("email"), prof["contact"].get("phone"), prof["contact"].get("full_name"), prof["contact"].get("linkedin_url")]
    assert all(p for p in pii[:3]) and len(text) > 500                                                    # the flow really logged something
    for p in pii:
        assert not p or p not in text, f"PII in logs: {p}"
    for secret in SECRETS.values():
        assert secret not in text
    assert "[email]" in text and "mongodb://***@" in text and "sk-***" in text                           # the deliberate leak above was masked
    assert "Authorization" not in text and not re.search(r"password\s*[=:]\s*[^*\s]", text, re.I)
