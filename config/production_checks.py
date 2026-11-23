"""Refuse to run in production with an unsafe configuration (fail closed).

Only checks that are cheap and unambiguous: secrets present and long enough, nothing left in a test/development mode. Messages name the variable, never its value.
"""
from config.settings import Settings

MIN_TOKEN = 24
MIN_SECRET = 16


def production_problems(s: Settings) -> list[str]:
    if s.app_env != "production":
        return []
    problems = []
    if len(s.admin_token or "") < MIN_TOKEN:
        problems.append(f"ADMIN_TOKEN must be set to a random string of at least {MIN_TOKEN} characters")
    if len(s.secret_key or "") < MIN_SECRET:
        problems.append(f"SECRET_KEY must be set (at least {MIN_SECRET} characters): it encrypts saved sessions and Gmail credentials")
    if s.allow_local_test_forms:
        problems.append("ALLOW_LOCAL_TEST_FORMS must be false in production")
    if s.debug:
        problems.append("DEBUG must be false in production")
    return problems
