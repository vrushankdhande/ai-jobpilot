"""Static safety checks for the React dashboard (web/). Replaces the Streamlit-era scans of frontend/*.py.

The real behaviour (the token never reaches a browser, admin calls work only through the proxy) is proven end to end in tests/e2e/test_react_playwright.py and
web/server/server.test.mjs; these checks stop the obvious regressions at the source level, with no browser or Node needed.
"""
import json
import re

import pytest

from config.settings import PROJECT_ROOT

WEB = PROJECT_ROOT / "web"
SRC = sorted(p for p in (WEB / "src").rglob("*") if p.suffix in {".ts", ".tsx"} and ".test." not in p.name and "test" not in p.parts)
SERVER = (WEB / "server" / "server.mjs").read_text(encoding="utf-8")


def test_the_sources_were_found():
    assert len(SRC) > 25 and any(p.name == "ApplicationQueue.tsx" for p in SRC)


@pytest.mark.parametrize("path", SRC, ids=lambda p: p.name)
def test_the_browser_code_never_reads_sends_or_stores_the_admin_token(path):
    raw = path.read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/|(?<!:)//[^\n]*", "", raw, flags=re.S)            # comments and help text may NAME the variable ("set ADMIN_TOKEN in .env")
    # the token exists only in the Node server's environment: the bundle can never read it from the build environment either
    assert not re.search(r"(import\.meta\.env|process\.env)[^;\n]*(TOKEN|SECRET|KEY|PASSWORD)", text, re.I)
    assert not re.search(r"VITE_\w*(TOKEN|SECRET|KEY|PASSWORD)", text, re.I)
    assert "adminToken" not in text
    # the only header the browser may send is the marker; the real token header is added by the proxy
    assert not re.search(r"X-Admin-Token", text, re.I), f"{path.name} must never set X-Admin-Token"
    assert "document.cookie" not in text
    assert not re.search(r"localStorage\.setItem\([^)]*(token|secret|key|password)", text, re.I)


@pytest.mark.parametrize("path", SRC, ids=lambda p: p.name)
def test_the_browser_code_never_injects_raw_html_or_evaluates_strings(path):
    text = path.read_text(encoding="utf-8")
    assert "dangerouslySetInnerHTML" not in text and ".innerHTML" not in text
    assert not re.search(r"\beval\(|new Function\(", text)


@pytest.mark.parametrize("path", SRC, ids=lambda p: p.name)
def test_the_browser_code_talks_only_to_the_same_origin(path):
    text = path.read_text(encoding="utf-8")
    assert not re.search(r"""fetch\(\s*["'`]https?://""", text), "all calls go through the proxy (relative URLs)"
    assert not re.search(r"mongodb(\+srv)?://", text, re.I) and "pymongo" not in text


def test_every_call_goes_through_the_single_api_client():
    offenders = [p.name for p in SRC if p.name not in {"api.ts", "hooks.ts"} and re.search(r"\bfetch\(", p.read_text(encoding="utf-8"))]
    assert offenders == [], f"use src/api.ts instead of fetch in: {offenders}"


def test_only_approval_pages_can_send_the_admin_marker_for_application_actions():
    # nothing in the Automation page (the worker UI) may offer approve/submit
    text = (WEB / "src" / "pages" / "Automation.tsx").read_text(encoding="utf-8")
    assert not re.search(r"/api/applications/[^`\"']*/(approve|submit)", text)
    assert not re.search(r">\s*(Approve|Submit)", text)


def test_the_proxy_strips_browser_tokens_and_cookies_and_only_adds_the_token_for_marked_calls():
    assert 'removeHeader("x-admin-token")' in SERVER and 'removeHeader("cookie")' in SERVER
    assert 'req.headers["x-admin-action"] === "1"' in SERVER
    assert "adminEnabled" in SERVER and re.search(r"res\.json\(\{ adminEnabled: Boolean\(adminToken\), backend", SERVER)   # /web/config never returns the token itself


def test_the_package_pins_exact_versions_and_has_a_lock_file():
    pkg = json.loads((WEB / "package.json").read_text(encoding="utf-8"))
    for section in ("dependencies", "devDependencies"):
        for name, version in pkg[section].items():
            assert re.fullmatch(r"\d+\.\d+\.\d+", version), f"{name} is not pinned exactly: {version}"
    assert (WEB / "package-lock.json").exists()
    assert "streamlit" not in json.dumps(pkg).lower()


def test_no_streamlit_code_or_dependency_remains():
    assert not (PROJECT_ROOT / "frontend").exists()
    assert "streamlit" not in (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8").lower()
    assert 'name = "streamlit"' not in (PROJECT_ROOT / "uv.lock").read_text(encoding="utf-8")
