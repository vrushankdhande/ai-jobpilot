"""Phase 7 API: authorization, scheduler control, queue controls, limits, notifications, reports. Mocked externals only."""
import threading

import pytest
from fastapi.testclient import TestClient

from backend.api.routes import scheduler as sched_routes
from backend.api.routes.scheduler import get_runtime
from backend.main import app
from config.settings import get_settings
from scheduler.application_worker import Outcome
from scheduler.runtime import build_runtime
from tests.integration.test_scheduler_flow import Clock, FakeDiscovery, FakeTransport
from tests.fixtures.jd_dataset import JDS
from datetime import datetime, timezone

pytestmark = pytest.mark.integration
TOKEN = "t0ken-for-tests-1234567890"
H = {"X-Admin-Token": TOKEN}


@pytest.fixture
def api(test_db, seeded, make_matching_service, tmp_path):
    from config.settings import get_settings as gs
    settings = gs().model_copy(update={"admin_token": TOKEN, "notifications_enabled": True, "notification_recipient": "me@example.com", "secret_key": "k" * 32,
                                       "notification_credentials_file": str(tmp_path / "g.cred")})
    clock = Clock(datetime(2026, 11, 13, 4, 0, tzinfo=timezone.utc))
    transport, calls = FakeTransport(), []
    disc = FakeDiscovery(test_db, make_matching_service(), JDS[:3])
    rt = build_runtime(test_db, settings, now_fn=clock, processor=lambda t: calls.append(t) or Outcome("awaiting_approval", application_id="a" * 32), transport=transport, discovery_service=disc)
    app.dependency_overrides[get_runtime] = lambda: rt
    app.dependency_overrides[get_settings] = lambda: settings
    yield TestClient(app), rt, settings, transport, calls, test_db
    app.dependency_overrides.clear()
    rt.scheduler.shutdown()


GUARDED = [("post", "/api/scheduler/start"), ("post", "/api/scheduler/stop"), ("post", "/api/scheduler/pause"), ("post", "/api/scheduler/resume"), ("post", "/api/scheduler/discover"),
           ("post", "/api/scheduler/worker/run"), ("put", "/api/scheduler/limits"), ("post", "/api/queue"), ("post", "/api/queue/" + "0" * 32 + "/pause"),
           ("post", "/api/queue/" + "0" * 32 + "/resume"), ("post", "/api/queue/" + "0" * 32 + "/cancel"), ("post", "/api/notifications/test"), ("post", "/api/reports/daily/generate")]


@pytest.mark.parametrize("method,path", GUARDED)
def test_operational_endpoints_need_the_admin_token(api, method, path):
    c, rt, settings, *_ = api
    body = {"json": {"limits": {}} if "limits" in path else {"job_id": "a" * 32} if path == "/api/queue" else {}}
    assert getattr(c, method)(path, **body).status_code == 401                                     # no token
    assert getattr(c, method)(path, headers={"X-Admin-Token": "wrong"}, **body).status_code == 401
    assert getattr(c, method)(path, headers={"X-Admin-Token": TOKEN[:-1]}, **body).status_code == 401
    ok = getattr(c, method)(path, headers=H, **body).status_code
    assert ok not in (401, 403)


def test_operational_endpoints_are_refused_when_no_admin_token_is_configured(api):
    c, rt, settings, *_ = api
    app.dependency_overrides[get_settings] = lambda: settings.model_copy(update={"admin_token": ""})
    for method, path in GUARDED:
        r = getattr(c, method)(path, headers=H, json={})
        assert r.status_code == 403 and "ADMIN_TOKEN" in r.json()["detail"], path
    assert c.get("/api/scheduler/status").status_code == 200                                         # reads stay open


def test_scheduler_start_stop_pause_resume_and_status(api):
    c, rt, *_ = api
    st = c.get("/api/scheduler/status").json()
    assert st["enabled"] is False and st["timezone"] == "Asia/Kolkata" and {j["name"] for j in st["jobs"]} == {"daily_discovery", "mail_check", "rejection_check", "application_worker", "daily_report", "notification_retry"}
    d = next(j for j in st["jobs"] if j["name"] == "daily_discovery")
    assert d["schedule"].startswith("daily at 07:00") and d["next_run_local"] and st["limits"]["effective"]["max_applications_per_day"] == 20 and st["notifications"]["configured"] is True
    assert c.post("/api/scheduler/start", headers=H).json()["enabled"] is True and rt.sched_repo.get_state()["enabled"] is True and rt.scheduler.alive
    assert c.post("/api/scheduler/pause", headers=H).json()["paused"] is True
    assert c.post("/api/scheduler/resume", headers=H).json()["paused"] is False
    assert c.post("/api/scheduler/stop", headers=H).json()["enabled"] is False and not rt.scheduler.alive and rt.sched_repo.get_state()["enabled"] is False


def test_manual_discovery_trigger_runs_once_and_is_refused_while_running(api):
    c, rt, settings, transport, calls, db = api
    r = c.post("/api/scheduler/discover", headers=H)
    assert r.status_code == 202 and r.json()["job"] == "daily_discovery"
    runs = c.get("/api/scheduler/runs", params={"job": "daily_discovery"}).json()
    assert len(runs) == 1 and runs[0]["trigger"] == "manual" and runs[0]["status"] == "completed" and runs[0]["result"]["new"] == 3
    assert rt.sched_repo.acquire_lock("job:daily_discovery", "someone-else", 60, rt.scheduler.now_fn())
    r2 = c.post("/api/scheduler/discover", headers=H)
    assert r2.status_code == 409 and "already in progress" in r2.json()["detail"] and len(c.get("/api/scheduler/runs").json()) == 1
    assert c.get("/api/scheduler/status").json()["last_discovery"]["status"] == "completed"


def test_worker_trigger_prepares_but_never_submits(api):
    c, rt, settings, transport, calls, db = api
    rt.queue.enqueue(job_id="a" * 32, resume_id=rt.discovery.matching.profile_for_matching(None, False).resume_id, match_score=90, eligibility="PASS")
    assert c.post("/api/scheduler/worker/run", headers=H).status_code == 202
    q = c.get("/api/queue").json()
    assert q["counts"]["awaiting_approval"] == 1 and len(calls) == 1 and db["applications"].count_documents({"state": {"$in": ["SUBMITTING", "SUBMITTED", "VERIFIED"]}}) == 0


def test_queue_endpoints_list_enqueue_and_control(api):
    c, rt, settings, transport, calls, db = api
    rt.discovery.discovery.execute(rt.discovery.discovery.runs.create({}, [], {})["run_id"], type("Q", (), {"max_jobs": 3})(), ["career_pages"])
    job = db["jobs"].find_one({}, {"job_id": 1})
    r = c.post("/api/queue", headers=H, json={"job_id": job["job_id"]})
    assert r.status_code == 201, r.text and r.json()["created"] is True
    tid = r.json()["task"]["task_id"]
    assert "idempotency_key" not in r.json()["task"] and c.post("/api/queue", headers=H, json={"job_id": job["job_id"]}).json()["created"] is False
    listing = c.get("/api/queue").json()
    assert listing["counts"]["queued"] == 1 and listing["tasks"][0]["task_id"] == tid and "history" not in listing["tasks"][0]
    assert c.get("/api/queue", params={"status": "failed"}).json()["tasks"] == [] and c.get("/api/queue", params={"status": "bogus"}).status_code == 422
    assert c.post(f"/api/queue/{tid}/pause", headers=H).json()["status"] == "paused"
    assert c.post(f"/api/queue/{tid}/pause", headers=H).status_code == 409
    assert c.post(f"/api/queue/{tid}/resume", headers=H).json()["status"] == "queued"
    assert [h["event"] for h in c.get(f"/api/queue/{tid}").json()["history"]] == ["queued", "paused", "resumed"]
    assert c.post(f"/api/queue/{tid}/cancel", headers=H).json()["status"] == "cancelled" and c.post(f"/api/queue/{tid}/cancel", headers=H).status_code == 409
    assert c.post(f"/api/queue/{'0' * 32}/cancel", headers=H).status_code == 404 and c.post("/api/queue/not-an-id/cancel", headers=H).status_code == 422
    assert c.post("/api/queue", headers=H, json={"job_id": "nope"}).status_code == 422 and c.post("/api/queue", headers=H, json={"job_id": "f" * 32}).status_code == 404


def test_limits_can_be_lowered_but_never_raised_above_the_yaml_caps(api):
    c, rt, *_ = api
    caps = c.get("/api/scheduler/limits").json()["caps"]
    r = c.put("/api/scheduler/limits", headers=H, json={"limits": {"max_applications_per_day": 5, "max_jobs_processed_per_day": 10_000, "min_match_score": 60}}).json()
    assert r["effective"]["max_applications_per_day"] == 5 and r["effective"]["max_jobs_processed_per_day"] == caps["max_jobs_processed_per_day"]
    assert r["effective"]["min_match_score"] == caps["min_match_score"] and r["caps"]["max_applications_per_day"] == caps["max_applications_per_day"]
    assert c.get("/api/scheduler/status").json()["limits"]["effective"]["max_applications_per_day"] == 5
    assert c.put("/api/scheduler/limits", headers=H, json={"limits": {"timezone": 1}}).status_code == 422                                  # not an adjustable limit
    assert c.put("/api/scheduler/limits", headers=H, json={"limits": {"delay_seconds_between_actions": 0}}).status_code == 422             # not adjustable
    assert c.put("/api/scheduler/limits", headers=H, json={"limits": {}}).json()["overrides"] == {}


def test_notifications_status_history_and_test_message(api):
    c, rt, settings, transport, *_ = api
    t = c.post("/api/notifications/test", headers=H).json()
    assert t["status"] == "sent" and len(transport.sent) == 1
    h = c.get("/api/notifications").json()
    assert h["config"] == {"enabled": True, "recipient_set": True, "recipient": "m***@example.com", "credentials_stored": False, "configured": True}
    assert h["notifications"][0]["type"] == "test" and "payload" not in h["notifications"][0] and "me@example.com" not in str(h)
    assert c.get("/api/notifications", params={"status": "failed"}).json()["notifications"] == []


def test_daily_report_endpoints(api):
    c, rt, *_ = api
    r = c.post("/api/reports/daily/generate", headers=H, json={"date": "2026-11-13", "email": True})
    assert r.status_code == 201 and r.json()["date"] == "2026-11-13" and r.json()["email"]["status"] == "sent" and r.json()["applications"]["submitted"] == 0
    stored = c.get("/api/reports/daily").json()
    assert len(stored) == 1 and stored[0]["report_id"] == r.json()["report_id"] and stored[0]["trigger"] == "manual"
    assert c.get("/api/reports/daily", params={"date": "2026-11-12"}).json() == [] and c.get("/api/reports/daily", params={"date": "13-11-2026"}).status_code == 422
    assert c.post("/api/reports/daily/generate", headers=H, json={"date": "bad"}).status_code == 422


def test_scheduler_resumes_after_a_restart_because_the_enabled_flag_is_persisted(api):
    c, rt, settings, transport, calls, db = api
    c.post("/api/scheduler/start", headers=H)
    rt.scheduler.shutdown()                                                                         # process ends without an explicit stop
    new_rt = build_runtime(db, settings, now_fn=rt.scheduler.now_fn, processor=lambda t: Outcome("awaiting_approval"), discovery_service=rt.discovery.discovery)
    assert new_rt.scheduler.status()["enabled"] is True and not new_rt.scheduler.alive
    sched_routes.start_if_enabled(db, settings.model_copy(update={"admin_token": TOKEN}))
    try:
        assert sched_routes._holder["owner"] is not None and sched_routes._holder["owner"].scheduler.alive
    finally:
        sched_routes.reset_runtime()
    assert db["scheduler_state"].find_one({"key": "state"})["enabled"] is True                       # shutting down does not clear the user's choice
