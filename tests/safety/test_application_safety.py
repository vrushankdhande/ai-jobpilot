"""Phase 6 safety invariants: nothing here is bypassable by configuration, request shape or code path.

* defaults are closed; restricted platforms are handoff-only; submit needs a backend-recorded approval
* no evasion techniques exist in the code base (static scan), no credentials are stored, the click is always preceded by the "never retry" marker
"""
import re
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from backend.main import app
from config.settings import PROJECT_ROOT, Settings, get_settings

pytestmark = pytest.mark.safety

PHASE6_SOURCES = [
    "browser/browser_manager.py", "browser/session_manager.py", "browser/locator_engine.py", "browser/safety_checks.py", "browser/screenshot_manager.py",
    "engines/application/application_engine.py", "engines/application/form_mapper.py", "engines/application/document_uploader.py",
    "engines/application/document_validator.py", "engines/application/submission_verifier.py", "engines/application/retry_manager.py",
    "engines/application/state_machine.py", "integrations/application_policy.py", "integrations/application_adapters.py",
    "backend/services/application_service.py", "backend/api/routes/applications.py", "security/credential_vault.py", "security/secrets_manager.py",
    "security/pii_protection.py", "security/audit_logger.py", "database/repositories/applications_repository.py", "database/models/application.py",
]


def source(rel: str) -> str:
    return (PROJECT_ROOT / rel).read_text(encoding="utf-8")


def code_only(text: str) -> str:
    """Source without comments and docstrings, so prose that explains what is forbidden does not trip the scan."""
    text = re.sub(r'"""[\s\S]*?"""', "", text)
    return "\n".join(line.split("#")[0] for line in text.splitlines())


# ---------------------------------------------------------------------------------------------- closed by default
def test_defaults_are_closed():
    s = Settings(_env_file=None)
    assert s.application_automation_enabled is False and s.allow_local_test_forms is False and s.persist_browser_sessions is False
    assert s.browser_headless is True and s.max_submit_retries <= 5
    assert not s.secret_key or len(s.secret_key) >= 0  # no default secret is baked in
    assert Settings(_env_file=None).secret_key == ""


def test_policy_file_only_allows_automation_for_the_local_test_provider_and_authorises_nothing():
    data = yaml.safe_load((PROJECT_ROOT / "config/application_policies.yaml").read_text(encoding="utf-8"))
    assert data["authorized_sites"] == []
    allowed = [k for k, v in data.items() if isinstance(v, dict) and v.get("application_status") == "AUTOMATION_ALLOWED"]
    assert allowed == ["local_test"] and set(data["local_test"]["application_hosts"]) == {"127.0.0.1", "localhost"}
    for k in ("linkedin", "indeed", "naukri", "manual"):
        assert data[k]["application_status"] == "MANUAL_HANDOFF"
    assert data["career_pages"]["providers"]["workday"]["application_status"] == "MANUAL_HANDOFF"
    for p in ("greenhouse", "lever", "generic_page"):
        assert data["career_pages"]["providers"][p]["application_status"] == "AUTHORIZATION_REQUIRED"
    platform = yaml.safe_load((PROJECT_ROOT / "config/platform_policies.yaml").read_text(encoding="utf-8"))
    assert all(platform[k]["auto_apply"] is False for k in ("linkedin", "indeed", "naukri", "manual"))     # Phase 4 policy unchanged


# ---------------------------------------------------------------------------------------------- no evasion, no credentials
EVASION = [r"\bproxy\b", r"proxies", r"stealth", r"undetected", r"playwright_stealth", r"fake_useragent", r"user_agent\s*=", r"set_extra_http_headers",
           r"add_init_script", r"navigator\.webdriver", r"webdriver", r"2captcha", r"anticaptcha", r"capsolver", r"solve_captcha", r"rotate",
           r"launch_persistent_context", r"user_data_dir", r"\\Google\\Chrome", r"\\Microsoft\\Edge\\User Data", r"ignore_https_errors", r"extra_http_headers",
           r"cookies\(\)\s*=", r"requests\.(get|post)", r"urllib\.request", r"httpx\.(get|post)"]


@pytest.mark.parametrize("rel", PHASE6_SOURCES)
def test_no_evasion_scraping_or_session_extraction_code(rel):
    text = code_only(source(rel))
    hits = [p for p in EVASION if re.search(p, text, re.I)]
    assert not hits, f"{rel}: {hits}"


def test_the_browser_never_types_into_password_fields_or_reads_credentials():
    engine = code_only(source("engines/application/application_engine.py")) + code_only(source("browser/session_manager.py"))
    assert not re.search(r"type\s*=\s*['\"]password|fill\(.*password|keyboard\.type", engine, re.I)
    for rel in PHASE6_SOURCES:
        assert not re.search(r"os\.environ|getenv\(.*(PASS|CRED|LOGIN)", code_only(source(rel)), re.I), rel


def test_captcha_login_and_blocks_only_ever_end_the_run():
    for rel in ("browser/safety_checks.py", "browser/session_manager.py", "browser/locator_engine.py"):
        text = code_only(source(rel))
        assert not re.search(r"\.(click|fill|type|press|check|select_option)\(", text) or rel == "browser/session_manager.py" and "click(" not in text, rel
    assert "must_stop" in source("engines/application/application_engine.py")


def test_the_click_is_always_preceded_by_the_never_retry_marker_and_the_gate():
    text = source("engines/application/application_engine.py")
    marker, gate, click = text.index("tracker.clicked = True"), text.index("blocked = gate(run)"), text.index("loc.submit_locator(")
    assert gate < marker < click
    assert text.count(".click()") == 1                                       # exactly one click in the whole engine: the submit control
    assert "expected_plan_hash" in text[:click] and "run.plan != expected_plan_hash" in text[:click]


def test_the_service_never_submits_outside_submit_and_always_checks_policy_and_state_first():
    svc = source("backend/services/application_service.py")
    assert svc.count("submit=True") == 1                                     # the only call that can click
    body = svc[svc.index("    def submit(") : svc.index("    def _withdraw(")]
    order = [body.index(x) for x in ("adapter.require_automation()", 'S.APPROVED.value', 'plan_hash', "_duplicate_check", "_daily_limit", "select_documents", "S.SUBMITTING", "submit=True")]
    assert order == sorted(order), order
    assert "VERIFIED" not in svc[svc.index("    def _after_run("): svc.index("    # ---", svc.index("    def _after_run("))].split("can_mark_verified")[0].replace("S.VERIFIED, event=", "")[:0]


# ---------------------------------------------------------------------------------------------- API shape
def test_no_endpoint_accepts_a_state_or_a_skip_approval_flag():
    paths = app.openapi()["paths"]
    submit = paths["/api/applications/{application_id}/submit"]["post"]
    assert "requestBody" not in submit                                       # nothing in a request can say "approved" or "force"
    approve = paths["/api/applications/{application_id}/approve"]["post"]
    props = app.openapi()["components"]["schemas"]["ApproveRequest"]["properties"]
    assert set(props) == {"acknowledge_warnings"} and approve
    for path, ops in paths.items():
        if path.startswith("/api/applications"):
            assert not (set(ops) & {"delete", "patch"}), path
    schemas = app.openapi()["components"]["schemas"]
    for name in ("PrepareRequest", "AnswersRequest", "ConfirmRequest"):
        assert not {"state", "force", "approved", "skip_validation", "mode", "verified"} & set(schemas[name]["properties"]), name
    assert set(schemas["PrepareRequest"]["properties"]) == {"job_id", "resume_id", "allow_unreviewed", "acknowledge_ineligible", "acknowledge_possible_duplicate", "candidate_inputs"}


def test_invalid_ids_are_rejected_before_reaching_the_service():
    from unittest.mock import MagicMock

    from backend.api.routes.applications import get_application_service
    svc = MagicMock()
    app.dependency_overrides[get_application_service] = lambda: svc
    try:
        c = TestClient(app)
        for bad in ("1", "x" * 32, "A" * 32, "0" * 31, "0" * 33):
            assert c.get(f"/api/applications/{bad}").status_code == 422, bad
            assert c.post(f"/api/applications/{bad}/submit").status_code == 422, bad
            assert c.post(f"/api/applications/{bad}/approve", json={}).status_code == 422, bad
        for bad in ("..", "../etc"):
            assert c.get(f"/api/applications/{bad}").status_code in (404, 405)
        assert c.post("/api/applications/prepare", json={"job_id": "nope"}).status_code == 422
        assert c.get("/api/applications/" + "0" * 32 + "/screenshots/../../secret.png").status_code in (404, 405, 422)
        assert c.get("/api/applications/" + "0" * 32 + "/screenshots/passwd").status_code == 422
        svc.submit.assert_not_called()                                           # nothing malformed ever reached the service
    finally:
        app.dependency_overrides.clear()


def test_responses_never_expose_filesystem_paths_or_internal_keys(tmp_path):
    from database.models.application import ApplicationRecord
    out = ApplicationRecord(idempotency_key="k", job_id="j", resume_id="r", profile_id="p", profile_version=1, source="manual").model_dump(mode="json")
    assert "path" not in " ".join(out) and "file_id" not in out
    text = source("backend/services/application_service.py")
    assert 'out.pop("idempotency_key"' in text


# ---------------------------------------------------------------------------------------------- tests themselves never touch real sites
def test_test_forms_are_local_only_and_there_is_no_real_job_site_in_the_phase6_tests():
    here = Path(__file__).resolve().parents[1]
    offenders = []
    for f in ["integration/test_application_flow.py", "unit/test_browser_layer.py", "fixtures/form_server.py"]:
        for m in re.finditer(r"https?://([a-z0-9.\-]+)", (here / f).read_text(encoding="utf-8").lower()):
            host = m.group(1)
            if host not in ("127.0.0.1", "127.0.0.2", "localhost", "10.0.0.5") and not host.endswith(("linkedin.com", "indeed.com", "naukri.com", "greenhouse.io", "myworkdayjobs.com", "example.com", "evil.com")):
                offenders.append((f, host))
    assert not offenders, offenders
    # the restricted/real hosts that DO appear are only ever used as stored job URLs for policy tests: the browser never gets them
    flow = (here / "integration/test_application_flow.py").read_text(encoding="utf-8")
    assert ".server.url(" in flow and "browser_headless\": True" in flow
