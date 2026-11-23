"""Endpoint tests (database is mocked via dependency override)."""


def test_root(client_db_up):
    resp = client_db_up.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert "Welcome" in body["message"]
    assert body["docs"] == "/docs"


def test_health(client_db_up):
    resp = client_db_up.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert {"app", "version", "environment", "timestamp"} <= body.keys()


def test_database_health_ok(client_db_up):
    resp = client_db_up.get("/health/database")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_database_health_down_hides_details(client_db_down):
    resp = client_db_down.get("/health/database")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unavailable"
    assert "mongodb://" not in resp.text


def test_swagger_available(client_db_up):
    assert client_db_up.get("/docs").status_code == 200
