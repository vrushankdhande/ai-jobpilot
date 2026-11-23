"""Config + DatabaseManager tests.

Everything except the ``integration`` test uses a mocked MongoClient and is NOT
evidence of a real MongoDB connection.
"""
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError
from pymongo.errors import ServerSelectionTimeoutError

from backend.core.config import Settings, get_settings
from backend.core.database import DatabaseConnectionError, DatabaseManager


# ---- configuration -------------------------------------------------------
def test_settings_defaults():
    s = Settings(_env_file=None)
    assert s.app_name == "AI JobPilot"
    assert s.api_port == 8000
    assert s.mongodb_database == "ai_jobpilot"
    assert s.log_path.name == "app.log"


def test_settings_env_override(monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv("BACKEND_BASE_URL", "http://example.test:9000/")
    s = Settings(_env_file=None)
    assert s.app_env == "staging"
    assert s.log_level == "DEBUG"
    assert s.backend_base_url == "http://example.test:9000"


@pytest.mark.parametrize(
    "var,value",
    [("MONGODB_URI", "http://wrong"), ("LOG_LEVEL", "LOUD"), ("API_PORT", "0")],
)
def test_settings_validation(monkeypatch, var, value):
    monkeypatch.setenv(var, value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_get_settings_cached():
    assert get_settings() is get_settings()


# ---- DatabaseManager (mocked client) -------------------------------------
def test_connect_success_and_close():
    with patch("database.connection.MongoClient") as client_cls:
        manager = DatabaseManager()
        manager.connect("mongodb://localhost:27017/", "testdb")
        client_cls.return_value.admin.command.assert_called_with("ping")
        assert manager.is_connected
        assert manager.ping() is True
        manager.get_database()
        client_cls.return_value.__getitem__.assert_called_with("testdb")
        manager.close()
        client_cls.return_value.close.assert_called_once()
        assert not manager.is_connected


def test_connect_reuses_single_client():
    with patch("database.connection.MongoClient") as client_cls:
        manager = DatabaseManager()
        manager.connect("mongodb://localhost:27017/", "db")
        manager.connect("mongodb://localhost:27017/", "db")
        assert client_cls.call_count == 1


def test_connect_failure_is_sanitized():
    with patch("database.connection.MongoClient") as client_cls:
        client_cls.return_value.admin.command.side_effect = ServerSelectionTimeoutError(
            "secret-host:27017 timed out"
        )
        manager = DatabaseManager()
        with pytest.raises(DatabaseConnectionError) as info:
            manager.connect("mongodb://user:pw@secret-host:27017/", "db")
        assert "pw" not in str(info.value)
        assert "secret-host" not in str(info.value)
        assert not manager.is_connected
        client_cls.return_value.close.assert_called_once()


def test_ping_false_when_server_fails():
    manager = DatabaseManager()
    manager._client = MagicMock()
    manager._client.admin.command.side_effect = ServerSelectionTimeoutError("down")
    assert manager.ping() is False


def test_ping_false_when_not_connected_and_get_database_raises():
    manager = DatabaseManager()
    assert manager.ping() is False
    with pytest.raises(DatabaseConnectionError):
        manager.get_database()


# ---- real MongoDB (skipped if unreachable) -------------------------------
@pytest.mark.integration
def test_real_mongodb_ping():
    settings = Settings()
    manager = DatabaseManager()
    try:
        manager.connect(settings.mongodb_uri, settings.mongodb_database, timeout_ms=1500)
    except DatabaseConnectionError:
        pytest.skip("No reachable MongoDB at configured MONGODB_URI")
    try:
        assert manager.ping() is True
    finally:
        manager.close()
