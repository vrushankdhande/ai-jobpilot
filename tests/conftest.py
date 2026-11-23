"""Shared fixtures.

* Phase 1 tests fake the database via dependency overrides.
* Phase 2 integration tests use a throwaway database (random name, dropped
  afterwards) on the configured MongoDB and are skipped if it is unreachable.
"""
import os
from unittest.mock import MagicMock
from uuid import uuid4

# Tests must never reach a real AI provider or depend on the developer's own .env: real environment variables win over
# .env, so pin the AI settings and ADMIN_TOKEN (tests assume "no token = read-only") BEFORE any settings object is created.
# (AI paths are tested with scripted fake clients.)
for _name, _value in (("AI_PROVIDER", "anthropic"), ("ANTHROPIC_API_KEY", ""), ("GEMINI_API_KEY", ""),
                      ("AI_CONSENT_EXTERNAL_PROCESSING", "false"), ("AI_REFINE_JD", "true"), ("ADMIN_TOKEN", ""), ("AI_DOCUMENTS_CONSENT", "false"), ("AI_EXTRACTION_ENABLED", "false")):
    os.environ[_name] = _value

import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from backend.api.routes.resumes import get_resume_service
from backend.core.database import DatabaseManager, get_database_manager
from backend.main import app
from backend.services.resume_service import ResumeService
from config.settings import get_settings
from database.indexes import ensure_indexes
from database.repositories.resumes_repository import ResumesRepository
from database.repositories.users_repository import UsersRepository
from backend.api.routes.matching import get_matching_service
from backend.services.matching_service import MatchingService
from config.scoring_config import load_scoring_config
from database.repositories.jobs_repository import JobsRepository
from database.repositories.matches_repository import MatchesRepository
from engines.matching.skill_extractor import get_catalog
from tests.fixtures.candidate import synthetic_profile
from tests.fixtures.preferences import evaluation_preferences
from tests.fixtures.sample_data import ensure_fixtures


@pytest.fixture(autouse=True)
def _reset_dependency_overrides():
    """Every test starts and ends with no FastAPI dependency overrides, so a test that forgets to clear one cannot hand a later
    test a service bound to an already-closed throw-away database ("Cannot use MongoClient after close")."""
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()



def make_manager(ping_result: bool) -> MagicMock:
    manager = MagicMock(spec=DatabaseManager)
    manager.ping.return_value = ping_result
    return manager


@pytest.fixture
def client_db_up():
    app.dependency_overrides[get_database_manager] = lambda: make_manager(True)
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def client_db_down():
    app.dependency_overrides[get_database_manager] = lambda: make_manager(False)
    yield TestClient(app)
    app.dependency_overrides.clear()


# ---- Phase 2 ----------------------------------------------------------------
@pytest.fixture(scope="session")
def sample_files():
    """(pdf_path, docx_path) of the synthetic sample resume."""
    return ensure_fixtures()


@pytest.fixture
def sample_pdf(sample_files):
    return sample_files[0]


@pytest.fixture
def sample_docx(sample_files):
    return sample_files[1]


@pytest.fixture
def test_db():
    settings = get_settings()
    client = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=1500, tz_aware=True)
    try:
        client.admin.command("ping")
    except PyMongoError:
        client.close()
        pytest.skip("No reachable MongoDB for integration tests")
    name = f"ai_jobpilot_test_{uuid4().hex[:8]}"
    db = client[name]
    ensure_indexes(db)
    yield db
    client.drop_database(name)
    client.close()


@pytest.fixture
def make_service(test_db, tmp_path):
    def _make(**overrides):
        settings = get_settings().model_copy(update=overrides)
        return ResumeService(
            ResumesRepository(test_db), UsersRepository(test_db), settings, storage_dir=tmp_path / "store"
        )

    return _make


@pytest.fixture
def service(make_service):
    return make_service()


# ---- Phase 4: no real network ---------------------------------------------------------------------------
@pytest.fixture
def no_network(monkeypatch):
    """Fail loudly if a test tries to reach anything except localhost (MongoDB). Proves tests never scrape."""
    import socket

    real_getaddrinfo, real_connect = socket.getaddrinfo, socket.socket.connect
    allowed = {"localhost", "127.0.0.1", "::1", "0.0.0.0", None}

    def guarded_getaddrinfo(host, *a, **k):
        if host not in allowed:
            raise AssertionError(f"test attempted a real network lookup: {host}")
        return real_getaddrinfo(host, *a, **k)

    def guarded_connect(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in allowed and not str(host).startswith("127."):
            raise AssertionError(f"test attempted a real network connection: {host}")
        return real_connect(self, address)

    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)


# ---- Phase 6: controlled local application forms + a real browser ----------------------------------------
@pytest.fixture(scope="session")
def form_server():
    """Local HTML application forms (127.0.0.1). No third-party site is ever contacted."""
    from tests.fixtures.form_server import FormServer

    srv = FormServer().start()
    yield srv
    srv.stop()


@pytest.fixture(scope="session")
def browser_ok():
    """Skips browser tests when no Chromium-family browser can be started (reported, never silently passed)."""
    from browser.browser_manager import BrowserManager, BrowserUnavailable

    try:
        with BrowserManager(get_settings().model_copy(update={"allow_local_test_forms": True})):
            pass
    except BrowserUnavailable as exc:
        pytest.skip(f"No browser available for Playwright tests: {exc}")
    return True


# ---- Phase 3: matching ----------------------------------------------------------------------------
@pytest.fixture(scope="session")
def catalog():
    return get_catalog()


@pytest.fixture
def scoring_cfg():
    return load_scoring_config()


@pytest.fixture
def prefs():
    return evaluation_preferences()


@pytest.fixture
def profile():
    return synthetic_profile()


@pytest.fixture
def seeded(test_db):
    """The synthetic reviewed profile saved for the (single) user and made the active resume."""
    p = synthetic_profile()
    ResumesRepository(test_db).save_profile(p)
    UsersRepository(test_db).set_active_resume(p.resume_id)
    return p


@pytest.fixture
def make_matching_service(test_db):
    def _make(ai_client=None, prefs=None, cfg=None):
        settings = get_settings()
        return MatchingService(
            JobsRepository(test_db), MatchesRepository(test_db), ResumesRepository(test_db), UsersRepository(test_db),
            settings, cfg or load_scoring_config(settings), prefs or evaluation_preferences(), get_catalog(settings), ai_client,
        )

    return _make


@pytest.fixture
def matching_service(make_matching_service):
    return make_matching_service()


@pytest.fixture
def matching_api(matching_service, seeded):
    app.dependency_overrides[get_matching_service] = lambda: matching_service
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def api(service):
    app.dependency_overrides[get_resume_service] = lambda: service
    yield TestClient(app)
    app.dependency_overrides.clear()
