"""Verify the local environment: Python, imports, settings, real MongoDB ping.

Run: uv run python scripts/check_environment.py
"""
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MODULES = ["fastapi", "uvicorn", "pymongo", "pydantic", "pydantic_settings", "httpx", "pytest"]


def main() -> int:
    ok = True
    print(f"Python {sys.version.split()[0]}")
    if sys.version_info[:2] != (3, 11):
        print("  [FAIL] Python 3.11 required")
        ok = False

    for name in MODULES:
        try:
            importlib.import_module(name)
            print(f"  [ OK ] import {name}")
        except ImportError as exc:
            print(f"  [FAIL] import {name}: {exc}")
            ok = False

    try:
        from backend.core.config import get_settings

        settings = get_settings()
        print(f"  [ OK ] settings loaded (env={settings.app_env}, db={settings.mongodb_database})")
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] settings: {type(exc).__name__}")
        return 1

    from backend.core.database import DatabaseConnectionError, DatabaseManager

    manager = DatabaseManager()
    try:
        manager.connect(settings.mongodb_uri, settings.mongodb_database)
        print("  [ OK ] MongoDB ping succeeded")
    except DatabaseConnectionError:
        print("  [FAIL] MongoDB ping failed (is MongoDB running? check MONGODB_URI)")
        ok = False
    finally:
        manager.close()

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
