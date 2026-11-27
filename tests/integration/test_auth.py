"""Sign-in and separate data per person, on throw-away databases (the owner's test database and each added person's own one)."""
import shutil
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from backend.api.routes import scheduler as sched_routes
from backend.main import app
from backend.services import auth_service
from backend.services.auth_service import AuthError, AuthService, hash_password, verify_password
from config.settings import PROJECT_ROOT, get_settings
from config.tenant import Tenant, current_tenant, database_for
from database.connection import DatabaseManager, get_database_manager

pytestmark = pytest.mark.integration
PW = "correct horse 42"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    get_settings.cache_clear()
    base = f"ai_jobpilot_test_{uuid4().hex[:8]}"
    monkeypatch.setenv("MONGODB_DATABASE", base)
    get_settings.cache_clear()
    settings = get_settings()
    probe = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=1500)
    try:
        probe.admin.command("ping")
    except PyMongoError:
        pytest.skip("No reachable MongoDB for integration tests")
    manager = DatabaseManager()
    manager.connect(settings.mongodb_uri, base)
    from database.indexes import ensure_indexes
    manager.on_connect = ensure_indexes
    app.dependency_overrides[get_database_manager] = lambda: manager
    auth_service.throttle.fails.clear()
    made: list[str] = []
    yield manager, made
    app.dependency_overrides.clear()
    sched_routes.reset_runtime()
    for name in probe.list_database_names():
        if name.startswith(base):
            probe.drop_database(name)
    for uid in made:
        shutil.rmtree(PROJECT_ROOT / "storage" / "users" / uid, ignore_errors=True)
    manager.close()
    probe.close()
    monkeypatch.delenv("MONGODB_DATABASE")
    monkeypatch.setenv("AUTH_ENABLED", "false")
    get_settings.cache_clear()


def _owner(made) -> TestClient:
    c = TestClient(app)
    r = c.post("/api/auth/setup", json={"name": "Vrushank", "email": "owner@example.com", "password": PW})
    assert r.status_code == 201, r.text
    made.append(r.json()["user"]["user_id"])
    return c


def _add(owner: TestClient, made, name, email) -> TestClient:
    r = owner.post("/api/auth/users", json={"name": name, "email": email, "password": PW})
    assert r.status_code == 201, r.text
    made.append(r.json()["user_id"])
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"email": email, "password": PW}).status_code == 200
    return c


def test_password_hashing():
    h = hash_password("s3cret-pass")
    assert "s3cret" not in h and verify_password("s3cret-pass", h) and not verify_password("wrong", h) and h != hash_password("s3cret-pass")


def test_nothing_without_signing_in_and_first_start_creates_the_owner(env):
    manager, made = env
    c = TestClient(app)
    assert c.get("/api/interviews").status_code == 401
    assert c.get("/api/auth/status").json() == {"auth_enabled": True, "setup_needed": True, "user": None}
    owner = _owner(made)
    cookie = owner.cookies.get("jp_session")
    assert cookie and len(cookie) >= 40
    st = owner.get("/api/auth/status").json()
    assert st["setup_needed"] is False and st["user"]["name"] == "Vrushank" and st["user"]["role"] == "owner"
    assert c.post("/api/auth/setup", json={"name": "X", "email": "x@example.com", "password": PW}).status_code == 409   # only once
    assert owner.get("/api/interviews").status_code == 200
    row = manager.base_database()["account_sessions"].find_one({})
    assert cookie not in str(row) and "password" not in str(manager.base_database()["accounts"].find_one({}, {"password_hash": 0}))


def test_each_person_sees_only_their_own_data(env):
    manager, made = env
    owner = _owner(made)
    asha = _add(owner, made, "Asha", "asha@example.com")
    assert owner.post("/api/interviews", json={"company_name": "Owner Co", "job_title": "ML Engineer", "scheduled_at": "2026-12-01T10:00:00"}).status_code in (200, 201)
    assert asha.post("/api/interviews", json={"company_name": "Asha Co", "job_title": "Data Analyst", "scheduled_at": "2026-12-02T10:00:00"}).status_code in (200, 201)
    def companies(c):
        body = c.get("/api/interviews").json()
        items = body["items"] if isinstance(body, dict) else body
        return [i["company_name"] for i in items]
    assert companies(owner) == ["Owner Co"] and companies(asha) == ["Asha Co"]
    asha_id = made[1]
    # her data is in her own database; the owner's stays where it always was
    names = manager._client.list_database_names()
    assert database_for(manager.database_name, Tenant(asha_id, "Asha", "user")) in names
    assert manager.base_database()["interviews"].count_documents({"company_name": "Asha Co"}) == 0
    # her own job preferences file, started from the empty template
    assert (PROJECT_ROOT / "storage" / "users" / asha_id / "job_preferences.yaml").exists()
    prefs = asha.get("/api/settings").json()
    assert "Pune" not in str(prefs.get("preferences", prefs))


def test_owner_only_actions_and_user_management(env):
    manager, made = env
    owner = _owner(made)
    asha = _add(owner, made, "Asha", "asha@example.com")
    assert asha.get("/api/auth/users").status_code == 403
    assert asha.post("/api/auth/users", json={"name": "Z", "email": "z@example.com", "password": PW}).status_code == 403
    assert asha.put("/api/settings/scoring", json={"weights": {}}).status_code == 403
    assert [u["name"] for u in owner.get("/api/auth/users").json()["items"]] == ["Vrushank", "Asha"]
    assert owner.post("/api/auth/users", json={"name": "Dup", "email": "ASHA@example.com", "password": PW}).status_code == 400   # same e-mail
    assert owner.post("/api/auth/users", json={"name": "Short", "email": "s@example.com", "password": "123"}).status_code == 400
    # her own scheduler status works and is hers alone
    assert asha.get("/api/scheduler/status").status_code == 200
    assert set(sched_routes._holder) >= {made[1]}
    # removing her signs her out; her data is kept
    assert owner.delete(f"/api/auth/users/{made[1]}").status_code == 200
    assert asha.get("/api/interviews").status_code == 401
    assert owner.delete(f"/api/auth/users/{made[0]}").status_code == 400                 # the owner cannot be removed


def test_wrong_passwords_lock_the_account_for_a_while_and_logout_ends_the_session(env):
    manager, made = env
    owner = _owner(made)
    c = TestClient(app)
    for _ in range(5):
        assert c.post("/api/auth/login", json={"email": "owner@example.com", "password": "nope-nope"}).status_code == 401
    r = c.post("/api/auth/login", json={"email": "owner@example.com", "password": PW})
    assert r.status_code == 401 and "Too many" in r.text
    assert c.post("/api/auth/login", json={"email": "nobody@example.com", "password": PW}).json()["detail"] == "Wrong e-mail or password."
    assert owner.post("/api/auth/logout").status_code == 200
    assert owner.get("/api/interviews").status_code == 401


def test_change_password_signs_out_everywhere(env):
    manager, made = env
    owner = _owner(made)
    assert owner.post("/api/auth/password", json={"current_password": "wrong-one", "new_password": "new password 1"}).status_code == 400
    assert owner.post("/api/auth/password", json={"current_password": PW, "new_password": "new password 1"}).status_code == 200
    assert owner.get("/api/interviews").status_code == 401
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"email": "owner@example.com", "password": "new password 1"}).status_code == 200


def test_expired_session_is_refused(env):
    manager, made = env
    owner = _owner(made)
    manager.base_database()["account_sessions"].update_many({}, {"$set": {"expires_at": __import__("datetime").datetime(2000, 1, 1)}})
    assert owner.get("/api/interviews").status_code == 401


def test_without_a_signed_in_person_code_works_on_the_owner_data():
    assert current_tenant.get() is None and database_for("db", None) == "db" and database_for("db", Tenant("a" * 32, "A", "owner")) == "db"
    assert database_for("db", Tenant("b" * 32, "B", "user")) == "db_u_" + "b" * 16
    with pytest.raises(ValueError):
        database_for("db", Tenant("../../x", "B", "user"))
    with pytest.raises(AuthError):
        auth_service.check_password_rules("short")
