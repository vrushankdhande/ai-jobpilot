"""Browser/UI tests for the REACT dashboard: a REAL API + the REAL Node web server (serves web/dist and proxies /api) driven by Playwright in a real browser.

Subprocesses: uvicorn (throw-away MongoDB database and storage), `node web/server/server.mjs`, and a second node server pointed at a dead backend (error states).
The local dummy form (tests/fixtures/form_server.py) is the only automation target; nothing real is contacted or submitted.
The admin token lives ONLY in the API and the node server environment: these tests assert it never reaches the browser (pages, responses, storage, cookies).
Skipped (loudly) when no Chromium-family browser can be started or MongoDB is unreachable.
"""
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import httpx
import psutil
import pytest
from pymongo import MongoClient

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect, sync_playwright  # noqa: E402

from config.settings import PROJECT_ROOT, get_settings  # noqa: E402
from tests.fixtures.form_server import FormServer  # noqa: E402
from tests.fixtures.jd_dataset import JDS  # noqa: E402

pytestmark = [pytest.mark.e2e, pytest.mark.browser]
expect.set_options(timeout=30000)
ADMIN = "e2e-admin-token-0123456789abcdef"
SECRET = "e2e-secret-key-0123456789abcdef"
WEB = PROJECT_ROOT / "web"
NODE_DIR = r"C:\Program Files\nodejs"
NO_SUCH_APP = "0" * 32                                    # a well-formed application id that does not exist
ROUTES = ["", "dashboard", "job-discovery", "resume-manager", "job-matching", "document-generation", "application-queue", "applied-jobs", "interview-tracker", "analytics", "automation", "settings"]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def kill_tree(p: subprocess.Popen) -> None:
    try:
        parent = psutil.Process(p.pid)
        for c in parent.children(recursive=True):
            c.kill()
        parent.kill()
    except psutil.Error:
        pass


def wait_http(url: str, seconds: float = 90) -> None:
    end = time.time() + seconds
    while time.time() < end:
        try:
            if httpx.get(url, timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"{url} did not come up")


def node_env() -> dict:
    env = dict(os.environ)
    env["JOBPILOT_ENV_FILE"] = "none"          # the Node server must not read the developer's own .env (it may hold a real ADMIN_TOKEN)
    if Path(NODE_DIR).exists():
        env["PATH"] = NODE_DIR + os.pathsep + env.get("PATH", "")
    return env


def ensure_built(env: dict) -> None:
    """Build web/dist when it is missing or older than any source file (so the tests always run what is in web/src)."""
    index = WEB / "dist" / "index.html"
    newest_src = max((p.stat().st_mtime for p in (WEB / "src").rglob("*") if p.is_file()), default=0)
    if index.exists() and index.stat().st_mtime >= newest_src:
        return
    npm = shutil.which("npm.cmd", path=env["PATH"]) or shutil.which("npm", path=env["PATH"])
    if not npm:
        pytest.skip("Node/npm not available to build the React app")
    r = subprocess.run([npm, "run", "build"], cwd=str(WEB), env=env, capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, f"web build failed:\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}"


class Stack:
    pass


@pytest.fixture(scope="session")
def stack(sample_files):
    sample_pdf, _sample_docx = sample_files
    s = get_settings()
    try:
        MongoClient(s.mongodb_uri, serverSelectionTimeoutMS=1500).admin.command("ping")
    except Exception:
        pytest.skip("No reachable MongoDB for end-to-end tests")
    nenv = node_env()
    node = shutil.which("node", path=nenv["PATH"])
    if not node:
        pytest.skip("Node.js is not installed; cannot run the React web server")
    ensure_built(nenv)

    st = Stack()
    st.tmp = Path(tempfile.mkdtemp(prefix="jp_e2e_react_"))
    st.db_name = f"ai_jobpilot_e2e_{uuid4().hex[:8]}"
    for name in ("job_preferences", "scoring_weights", "automation_limits"):
        shutil.copy(PROJECT_ROOT / "config" / f"{name}.yaml", st.tmp / f"{name}.yaml")
    # the application-limits copy gets all-day windows so the tests do not depend on the time of day
    lim = (st.tmp / "automation_limits.yaml").read_text(encoding="utf-8").replace('["09:00", "21:00"]', '["00:00", "23:59"]').replace('["06:00", "22:00"]', '["00:00", "23:59"]')
    (st.tmp / "automation_limits.yaml").write_text(lim, encoding="utf-8")
    st.forms = FormServer().start()
    st.api_port, st.web_port, st.dead_port = free_port(), free_port(), free_port()
    st.api, st.web = f"http://127.0.0.1:{st.api_port}", f"http://127.0.0.1:{st.web_port}"
    st.dead = f"http://127.0.0.1:{st.dead_port}"
    env = {**os.environ, "MONGODB_DATABASE": st.db_name, "LOG_FILE": str(st.tmp / "api.log"), "RESUME_STORAGE_DIR": str(st.tmp / "resumes"), "GENERATED_STORAGE_DIR": str(st.tmp / "gen"),
           "SCREENSHOTS_DIR": str(st.tmp / "shots"), "BROWSER_SESSIONS_DIR": str(st.tmp / "sessions"), "JOB_PREFERENCES_FILE": str(st.tmp / "job_preferences.yaml"),
           "SCORING_CONFIG_FILE": str(st.tmp / "scoring_weights.yaml"), "AUTOMATION_LIMITS_FILE": str(st.tmp / "automation_limits.yaml"), "ADMIN_TOKEN": ADMIN, "SECRET_KEY": SECRET,
           "APPLICATION_AUTOMATION_ENABLED": "true", "ALLOW_LOCAL_TEST_FORMS": "true", "BROWSER_HEADLESS": "true", "SCHEDULER_AUTOSTART": "false", "APP_ENV": "development"}
    st.procs = []
    st.procs.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", str(st.api_port), "--log-level", "warning"], cwd=str(PROJECT_ROOT),
                                     env=env, stdout=open(st.tmp / "api.out", "wb"), stderr=subprocess.STDOUT))
    wait_http(st.api + "/health")

    def web(port: int, backend: str, token: str) -> subprocess.Popen:
        return subprocess.Popen([node, "server/server.mjs"], cwd=str(WEB), env={**nenv, "WEB_PORT": str(port), "WEB_HOST": "127.0.0.1", "BACKEND_BASE_URL": backend, "ADMIN_TOKEN": token},
                                stdout=open(st.tmp / f"web_{port}.out", "wb"), stderr=subprocess.STDOUT)
    st.procs.append(web(st.web_port, st.api, ADMIN))
    st.procs.append(web(st.dead_port, f"http://127.0.0.1:{free_port()}", ""))                    # a web server whose backend does not exist: error states
    wait_http(st.web + "/web/config")
    wait_http(st.dead + "/web/config")

    # ---- seed through the real API (the same path a user takes)
    c = httpx.Client(base_url=st.api, timeout=120)
    up = c.post("/api/resumes/upload", files={"file": ("sample_resume.pdf", sample_pdf.read_bytes())}).json()
    st.resume_id = up["resume"]["resume_id"]
    prof = c.get(f"/api/resumes/{st.resume_id}/profile").json()
    prof["review_status"] = "reviewed"
    c.put(f"/api/resumes/{st.resume_id}/profile", json=prof)
    st.db = MongoClient(s.mongodb_uri, tz_aware=True)[st.db_name]
    st.jobs = {}
    for i, jd in enumerate(JDS[:4]):
        j = c.post("/api/matching/analyze-jd", json={"job_description": jd["text"], "job_title": jd["title"], "company_name": jd["company"], "job_url": f"https://www.linkedin.com/jobs/view/{800 + i}"}).json()
        st.db["jobs"].update_one({"job_id": j["job_id"]}, {"$set": {"source": "linkedin"}})
        c.post("/api/matching/score", json={"job_id": j["job_id"]})
        st.jobs[i] = (j["job_id"], jd)
    # one job on the LOCAL dummy form (the only automation target); everything else is manual-only
    local = c.post("/api/matching/analyze-jd", json={"job_description": JDS[0]["text"], "job_title": "Local Form Engineer", "company_name": "Local Test Co", "job_url": st.forms.url("/ref")}).json()
    st.db["jobs"].update_one({"job_id": local["job_id"]}, {"$set": {"source": "local_test"}})
    c.post("/api/matching/score", json={"job_id": local["job_id"]})
    st.local_job = local["job_id"]
    for jid in (st.local_job, st.jobs[1][0]):
        for path, body in (("resume/generate", {}), ("cover-letter/generate", {}), ("screening/generate", {"candidate_inputs": {"notice_period": "30 days", "salary_expectation": "INR 3,000,000 per year",
                                                                                                                                 "relocation": "yes", "work_authorization": "Yes, citizen"}})):
            d = c.post(f"/api/documents/{path}", json={"job_id": jid, **body}).json()
            ok = c.post(f"/api/documents/{d['document_id']}/approve", json={"acknowledge_warnings": True})
            assert ok.status_code == 200, f"fixture: could not approve {path}: {ok.text[:200]}"
    c.post("/api/interviews", json={"company_name": "Seeded Interview Co", "job_title": "ML Engineer", "round": "Technical 1",
                                    "scheduled_at": time.strftime("%Y-%m-%dT10:00:00", time.gmtime(time.time() + 2 * 86400))})
    st.client = c
    yield st
    for p in st.procs:
        kill_tree(p)
    st.forms.stop()
    MongoClient(s.mongodb_uri).drop_database(st.db_name)
    shutil.rmtree(st.tmp, ignore_errors=True)


@pytest.fixture(scope="session")
def browser(stack):
    with sync_playwright() as pw:
        b = None
        for channel in (None, "msedge", "chrome"):
            try:
                b = pw.chromium.launch(headless=True, **({"channel": channel} if channel else {}))
                break
            except Exception:  # noqa: BLE001
                b = None
        if b is None:
            pytest.skip("No Chromium-family browser available for Playwright")
        yield b
        b.close()


@pytest.fixture
def page(browser, request, failure_dir):
    ctx = browser.new_context(viewport={"width": 1400, "height": 1000}, accept_downloads=True)
    ctx.set_default_timeout(30000)
    pg = ctx.new_page()
    pg.js_errors, pg.console_errors, pg.sent, pg.responses = [], [], [], []
    pg.on("pageerror", lambda e: pg.js_errors.append(str(e)))
    pg.on("console", lambda m: pg.console_errors.append(m.text) if m.type == "error" else None)
    pg.on("request", lambda r: pg.sent.append((r.method, r.url, dict(r.headers))))
    pg.on("response", lambda r: pg.responses.append(r))
    yield pg
    rep_call = getattr(request.node, "rep_call", None)
    if rep_call is not None and rep_call.failed:
        try:
            pg.screenshot(path=str(failure_dir / f"react_{request.node.name}.png"), full_page=True)
            (failure_dir / f"react_{request.node.name}.txt").write_text(pg.locator("body").inner_text()[:6000], encoding="utf-8")
            for f in request.getfixturevalue("stack").tmp.glob("*.out"):
                (failure_dir / f"react_{request.node.name}.{f.name}").write_bytes(f.read_bytes()[-6000:])
        except Exception:  # noqa: BLE001
            pass
    ctx.close()


# ============================================================================================ helpers
def open_page(pg, base: str, route: str = ""):
    pg.goto(f"{base}/{route}".rstrip("/") if route else base + "/")
    expect(pg.locator("h1").first).to_be_visible()
    pg.wait_for_load_state("networkidle")
    return pg


def tick(pg, text: str, optional: bool = False):
    """Tick a checkbox by its label. optional=True: do nothing if it is absent or disabled (e.g. the eligibility acknowledgement)."""
    loc = pg.get_by_label(text).first
    if optional and (pg.get_by_label(text).count() == 0 or not loc.is_enabled()):
        return
    loc.check()                                           # Playwright verifies it really ended up checked


def select_job(pg, title_fragment: str) -> str:
    sel = pg.get_by_label("Select a job")
    expect(sel).to_be_visible()
    value = sel.locator("option", has_text=title_fragment).first.get_attribute("value")
    sel.select_option(value)
    expect(sel).to_have_value(value)
    return value


def heading(pg, text: str):
    return pg.get_by_role("heading", name=re.compile(re.escape(text)))


def metric(pg, label: str):
    return pg.locator('[data-testid="metric"]', has_text=label).first


def readable_bodies(pg) -> list[tuple[str, str]]:
    out = []
    for r in pg.responses:
        try:
            if not any(t in (r.headers.get("content-type") or "") for t in ("json", "javascript", "html", "text", "css")):
                continue
            out.append((r.url, r.text()))
        except Exception:  # noqa: BLE001  (a redirect / aborted response has no body)
            continue
    return out


# ============================================================================================ startup + navigation
def test_react_app_starts_and_the_home_page_shows_system_status(stack, page):
    open_page(page, stack.web)
    expect(page.locator("h1").first).to_contain_text("AI JobPilot")
    expect(page.get_by_text("Backend: connected")).to_be_visible()
    expect(page.get_by_text("MongoDB: connected")).to_be_visible()
    expect(page.locator("aside").get_by_text("Backend connected")).to_be_visible()
    expect(page.locator("aside").get_by_text("Admin controls: enabled")).to_be_visible()


def test_all_dashboard_sections_are_reachable_and_render_without_errors(stack, page):
    open_page(page, stack.web)
    nav = page.get_by_role("navigation", name="App pages")
    expect(nav).to_be_visible()
    for needed in ("Dashboard", "Job Discovery", "Resume Manager", "Job Matching", "Document Generation", "Application Queue", "Applied Jobs", "Interview Tracker", "Analytics", "Automation", "Settings"):
        expect(nav.get_by_role("link", name=re.compile(needed))).to_be_visible()
    for route in ROUTES:
        open_page(page, stack.web, route)
        expect(page.locator("h1").first).to_be_visible()
        expect(page.locator("aside").get_by_text("Backend connected")).to_be_visible()
        assert "Traceback" not in page.locator("body").inner_text()
    # navigating through the menu (client-side routing) works too
    nav.get_by_role("link", name=re.compile("Analytics")).click()
    expect(page).to_have_url(re.compile(r"/analytics$"))
    expect(heading(page, "Analytics").first).to_be_visible()
    assert not page.js_errors, page.js_errors[:3]
    assert not page.console_errors, page.console_errors[:3]


def test_dashboard_numbers_match_the_database(stack, page):
    total = stack.db["jobs"].count_documents({})
    open_page(page, stack.web, "dashboard")
    expect(metric(page, "Jobs discovered").locator(".metric-value")).to_have_text(str(total))
    expect(metric(page, "Upcoming interviews").locator(".metric-value")).to_have_text("1")
    assert not page.js_errors


# ============================================================================================ resume upload + management
def test_resume_upload_and_profile_overview(stack, page, sample_files):
    _pdf, docx = sample_files
    before = stack.db["resumes"].count_documents({})
    open_page(page, stack.web, "resume-manager")
    expect(heading(page, "Your resumes")).to_be_visible()
    page.get_by_label("Master resume (PDF or DOCX)").set_input_files(str(docx))
    page.get_by_role("button", name="Upload & parse").click()
    msg = page.get_by_text(re.compile("Uploaded and parsed|Upload complete|already uploaded|already")).first
    expect(msg).to_be_visible(timeout=90000)
    after = stack.db["resumes"].count_documents({})
    assert after == before + (1 if "Uploaded and parsed" in msg.inner_text() else 0)
    expect(heading(page, "Profile overview")).to_be_visible()
    expect(heading(page, "Profile version history")).to_be_visible()
    assert not page.js_errors


def test_resume_upload_rejects_an_unsupported_file_in_the_browser(stack, page, tmp_path):
    bad = tmp_path / "notes.txt"
    bad.write_text("not a resume")
    open_page(page, stack.web, "resume-manager")
    before = stack.db["resumes"].count_documents({})
    page.get_by_label("Master resume (PDF or DOCX)").set_input_files(str(bad))
    expect(page.get_by_role("button", name="Upload & parse")).to_be_disabled()
    expect(page.get_by_role("alert")).to_be_visible()
    assert stack.db["resumes"].count_documents({}) == before


# ============================================================================================ job discovery + matching
def test_job_discovery_filters_update_the_visible_count(stack, page):
    open_page(page, stack.web, "job-discovery")
    shown = page.get_by_text(re.compile(r"\d+ of \d+ job\(s\) shown"))
    expect(shown).to_be_visible()
    title = page.get_by_label("Title contains")
    title.fill("zzz-no-such-title")
    expect(page.get_by_text("No jobs match")).to_be_visible()
    title.fill("")
    expect(shown).to_be_visible()
    expect(heading(page, "Discovery history")).to_be_visible()
    assert not page.js_errors


def test_job_matching_runs_an_analysis_and_shows_score_and_eligibility(stack, page):
    open_page(page, stack.web, "job-matching")
    page.get_by_label("Paste the job description").fill(JDS[2]["text"])
    page.get_by_label("Job title", exact=True).fill("UI Test Engineer")
    page.get_by_label("Company", exact=True).fill("UI Test Co")
    page.get_by_role("button", name="Analyze & match").click()
    expect(heading(page, "C. Match score")).to_be_visible(timeout=90000)
    expect(metric(page, "Eligibility")).to_be_visible()                 # score and eligibility are shown separately
    expect(metric(page, "Match score")).to_be_visible()
    expect(heading(page, "D. Score breakdown")).to_be_visible()
    expect(heading(page, "E. Skill comparison")).to_be_visible()
    assert stack.db["job_matches"].count_documents({"job_title": "UI Test Engineer"}) == 1
    assert not page.js_errors


# ============================================================================================ application approval workflow
def test_a_restricted_platform_application_is_manual_handoff_with_no_submit_path(stack, page):
    open_page(page, stack.web, "application-queue")
    job_id, jd = stack.jobs[1]
    select_job(page, jd["title"])
    expect(page.get_by_text(re.compile("Manual handoff: ")).first).to_be_visible()                          # the policy banner on selecting the job
    tick(page, "I know this job failed the eligibility check - prepare anyway", optional=True)
    page.get_by_role("button", name=re.compile("Prepare application|Prepare again")).click()
    expect(heading(page, "4. Review")).to_be_visible(timeout=120000)                                         # preparing finished
    expect(page.get_by_text(re.compile(r"You apply \(manual handoff\)")).first).to_be_visible()
    assert page.get_by_role("button", name=re.compile("Submit application")).count() == 0
    assert page.get_by_role("button", name=re.compile("Approve exactly")).count() == 0
    app = stack.client.get("/api/applications", params={"job_id": job_id}).json()
    assert app and app[0]["state"] == "MANUAL_HANDOFF_REQUIRED" and app[0]["mode"] == "manual_handoff"
    assert stack.client.post(f"/api/applications/{app[0]['application_id']}/submit", headers={"X-Admin-Token": ADMIN}).status_code == 409
    assert not page.js_errors


def test_the_full_approval_workflow_on_a_local_form_submits_exactly_once(stack, page):
    stack.forms.reset()
    open_page(page, stack.web, "application-queue")
    sel = page.get_by_label("Select a job")
    select_job(page, "Local Form Engineer")
    expect(page.get_by_text(re.compile("Automation is permitted"))).to_be_visible()
    tick(page, "I know this job failed the eligibility check - prepare anyway", optional=True)
    page.get_by_role("button", name=re.compile("Prepare application")).click()
    expect(page.get_by_text(re.compile("Preparing: checking permissions"))).to_be_visible()                  # loading indicator
    expect(heading(page, "4. Review")).to_be_visible(timeout=120000)
    assert stack.forms.posts == []                                                                          # preparing never submits
    expect(sel).to_have_value(stack.local_job)                                                              # the selection survives preparing (the Streamlit version lost it)
    expect(page).to_have_url(re.compile(f"job={stack.local_job}"))
    # the consent checkbox is a legal question: the page asks the candidate and never ticks it itself
    expect(heading(page, "Your answers")).to_be_visible()
    consent = page.get_by_label("I agree to the privacy policy")
    expect(consent).not_to_be_checked()
    consent.check()
    page.get_by_role("button", name=re.compile("Save answers and re-check")).click()
    expect(page.get_by_role("heading", name=re.compile("Ready for your review"))).to_be_visible(timeout=120000)
    expect(sel).to_have_value(stack.local_job)                                                              # ...and saving answers
    assert stack.forms.posts == []
    approve = page.get_by_role("button", name=re.compile("Approve exactly this application"))
    expect(approve).to_be_visible(timeout=120000)
    tick(page, "I have reviewed the values above", optional=True)                                           # present only when the page shows warnings
    expect(approve).to_be_enabled()
    assert page.get_by_role("button", name=re.compile("Submit application")).count() == 0                  # nothing to submit before approval
    approve.click()
    expect(page.get_by_role("heading", name=re.compile("Approved - not submitted yet"))).to_be_visible(timeout=60000)
    assert stack.forms.posts == []                                                                          # approved is still not submitted
    submit = page.get_by_role("button", name=re.compile("Submit application"))
    expect(submit).to_be_disabled()                                                                         # needs the explicit "I understand" tick
    tick(page, "I understand and want to submit this application now")
    expect(submit).to_be_enabled()
    submit.click()
    expect(page.get_by_role("heading", name=re.compile("Verified|Submitted"))).to_be_visible(timeout=120000)
    assert len(stack.forms.posts) == 1
    post = stack.forms.posts[0]
    assert post["fields"]["email"] and post["files"]["resume"]["size"] > 1000
    app = stack.client.get("/api/applications", params={"job_id": stack.local_job}).json()[0]
    assert app["state"] in ("VERIFIED", "SUBMITTED") and app["submitted_at"]
    # approve and submit went out as ADMIN actions via the proxy; the browser itself never sent a token
    sent = [(m, u, h) for m, u, h in page.sent if "/api/" in u]
    assert not [1 for _m, _u, h in sent if "x-admin-token" in h], "the browser must never send X-Admin-Token"
    for action in ("/approve", "/submit"):
        reqs = [h for m, u, h in sent if m == "POST" and u.endswith(action)]
        assert reqs and all(h.get("x-admin-action") == "1" for h in reqs), action
    # the page cannot be used to send it a second time
    open_page(page, stack.web, "application-queue")
    select_job(page, "Local Form Engineer")
    expect(heading(page, "4. Review")).to_be_visible()
    assert page.get_by_role("button", name=re.compile("Submit application")).count() == 0
    assert len(stack.forms.posts) == 1
    assert not page.js_errors


def test_rejecting_an_application_asks_for_confirmation(stack, page):
    job_id, jd = stack.jobs[1]
    open_page(page, stack.web, "application-queue")
    select_job(page, jd["title"])
    page.get_by_role("button", name="🚫 Reject this application").click()
    expect(page.get_by_text("Reject this application?")).to_be_visible()
    app = stack.client.get("/api/applications", params={"job_id": job_id}).json()[0]
    assert stack.client.get(f"/api/applications/{app['application_id']}").json()["dismissed_at"] is None       # the first click only asked
    page.get_by_role("button", name="Yes, confirm").click()
    expect(page.get_by_text(re.compile("You rejected this application"))).to_be_visible()
    assert stack.client.get(f"/api/applications/{app['application_id']}").json()["dismissed_at"]


def test_applied_jobs_shows_evidence_and_an_empty_state(stack, page):
    open_page(page, stack.web, "applied-jobs")
    expect(heading(page, "Applied Jobs").first).to_be_visible()
    expect(metric(page, "Submitted")).to_be_visible()
    page.get_by_label(re.compile(r"^Show")).select_option("Failed")      # the label also contains the select's current option text
    expect(page.get_by_text("No applications match")).to_be_visible()                                       # empty state
    assert not page.js_errors


# ============================================================================================ interviews + analytics
def test_interview_tracker_create_and_validation_error(stack, page):
    open_page(page, stack.web, "interview-tracker")
    company, title, link = page.get_by_label("Company *"), page.get_by_label("Job title *"), page.get_by_label("Meeting link (https)")
    company.fill("Playwright Co")
    title.fill("QA Engineer")
    link.fill("javascript:alert(1)")
    page.get_by_role("button", name="➕ Add interview").click()
    expect(page.get_by_text(re.compile("Not saved"))).to_be_visible()                                       # validation error is shown
    assert stack.db["interviews"].count_documents({"company_name": "Playwright Co"}) == 0
    expect(company).to_have_value("Playwright Co")                                                          # a rejected entry keeps what was typed (the Streamlit form wiped it)
    expect(title).to_have_value("QA Engineer")
    expect(link).to_have_value("javascript:alert(1)")
    link.fill("https://meet.example.com/pw")
    page.get_by_role("button", name="➕ Add interview").click()
    expect(page.get_by_text(re.compile("Interview added: Playwright Co"))).to_be_visible()
    assert stack.db["interviews"].count_documents({"company_name": "Playwright Co"}) == 1
    expect(company).to_have_value("")                                                                       # cleared only after a successful save
    assert not page.js_errors


def test_analytics_page_shows_metrics_charts_and_exports(stack, page):
    open_page(page, stack.web, "analytics")
    expect(metric(page, "Jobs discovered")).to_be_visible()
    expect(heading(page, "Match score distribution")).to_be_visible()
    expect(heading(page, "Application success and failure")).to_be_visible()
    csv = page.get_by_role("button", name=re.compile("Daily.*CSV"))
    expect(csv).to_be_enabled()
    with page.expect_download() as dl:
        csv.click()
    assert dl.value.suggested_filename.endswith(".csv")
    assert not page.js_errors


# ============================================================================================ settings
def test_settings_save_valid_values_reject_invalid_ones_and_never_show_secrets(stack, page):
    open_page(page, stack.web, "settings")
    body = page.locator("body").inner_text()
    assert ADMIN not in body and SECRET not in body
    expect(page.get_by_text("Candidate profile:")).to_be_visible()
    page.get_by_label("Minimum salary (annual, 0 = unset)").fill("1000")
    page.get_by_role("button", name=re.compile("Save preferences")).click()
    expect(page.get_by_text(re.compile("Not saved"))).to_be_visible()                                       # salary without currency: refused
    page.get_by_label("Salary currency").fill("INR")
    page.get_by_label("Preferred roles (one per line or comma separated)").fill("Machine Learning Engineer")
    page.get_by_role("button", name=re.compile("Save preferences")).click()
    expect(page.get_by_text("Preferences saved.")).to_be_visible()
    saved = (stack.tmp / "job_preferences.yaml").read_text(encoding="utf-8")
    assert "Machine Learning Engineer" in saved and "INR" in saved and "# Your job preferences" in saved
    page.reload()
    expect(page.get_by_label("Preferred roles (one per line or comma separated)")).to_have_value("Machine Learning Engineer")   # persisted
    text = page.locator("body").inner_text()
    assert ADMIN not in text and SECRET not in text


# ============================================================================================ error states
def test_an_unreachable_backend_shows_a_clear_message_not_a_stack_trace(stack, page):
    for route in ("", "dashboard", "interview-tracker", "analytics", "settings"):
        open_page(page, stack.dead, route)
        expect(page.get_by_text(re.compile("unreachable", re.I)).first).to_be_visible()
        assert "Traceback" not in page.locator("body").inner_text()
    assert not page.js_errors


# ============================================================================================ NEW for React: token handling + proxy behaviour
def test_the_admin_token_never_reaches_the_browser(stack, page):
    for route in ROUTES:
        open_page(page, stack.web, route)
        text = page.locator("body").inner_text()
        assert ADMIN not in text and SECRET not in text, route
    cfg = page.request.get(f"{stack.web}/web/config")
    assert cfg.json() == {"adminEnabled": True, "backend": "proxied"} and ADMIN not in cfg.text()
    leaks = [url for url, body in readable_bodies(page) if ADMIN in body or SECRET in body]
    assert not leaks, f"the token appears in: {leaks}"
    assert readable_bodies(page), "no responses were inspected"
    store = page.evaluate("() => JSON.stringify([localStorage, sessionStorage, document.cookie])")
    assert ADMIN not in store and SECRET not in store
    assert not page.context.cookies()                                                                       # the app sets no cookies at all
    assert not [1 for _m, _u, h in page.sent if "x-admin-token" in h]                                       # and no request from the page carries a token
    dist = "".join(p.read_text(encoding="utf-8", errors="ignore") for p in (WEB / "dist").rglob("*") if p.is_file() and p.suffix in (".js", ".html", ".css"))
    assert ADMIN not in dist and SECRET not in dist                                                         # ...nor is it baked into the built app


def test_admin_endpoints_need_the_server_side_token_the_browser_cannot_supply(stack, page):
    open_page(page, stack.web, "dashboard")
    req = page.request                                                                                       # a request made from the browser's origin
    # without the admin marker the proxy adds nothing: the backend refuses
    assert req.post(f"{stack.web}/api/scheduler/pause").status == 401
    assert req.post(f"{stack.web}/api/applications/{NO_SUCH_APP}/approve", data={"acknowledge_warnings": False}).status == 401
    # a token typed by the browser is NOT forwarded (the proxy strips it) - even the correct one does not work from the page
    assert req.post(f"{stack.web}/api/scheduler/pause", headers={"X-Admin-Token": ADMIN}).status == 401
    # with the marker the proxy adds the real token and the call works / reaches the application logic
    ok = req.post(f"{stack.web}/api/scheduler/pause", headers={"X-Admin-Action": "1"})
    assert ok.status == 200, ok.text()
    assert req.post(f"{stack.web}/api/scheduler/resume", headers={"X-Admin-Action": "1"}).status == 200
    assert req.post(f"{stack.web}/api/applications/{NO_SUCH_APP}/approve", headers={"X-Admin-Action": "1"}, data={"acknowledge_warnings": False}).status == 404
    # the dead-backend web server (no token configured) cannot be used to perform admin actions either
    assert req.post(f"{stack.dead}/api/scheduler/pause", headers={"X-Admin-Action": "1"}).status == 502


def test_web_config_reports_read_only_when_the_server_has_no_token(stack, page):
    cfg = page.request.get(f"{stack.dead}/web/config").json()
    assert cfg == {"adminEnabled": False, "backend": "proxied"}
    open_page(page, stack.dead, "settings")
    expect(page.get_by_text("Admin controls: read-only (set ADMIN_TOKEN)")).to_be_visible()
