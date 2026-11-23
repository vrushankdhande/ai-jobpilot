"""Keep a screenshot of the page whenever a browser test fails (written next to the test run so a failure can be looked at, not guessed)."""
import os
from pathlib import Path

import pytest


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)


@pytest.fixture
def failure_dir() -> Path:
    d = Path(os.environ.get("E2E_FAILURE_DIR") or Path(__file__).resolve().parent / "_failures")
    d.mkdir(parents=True, exist_ok=True)
    return d
