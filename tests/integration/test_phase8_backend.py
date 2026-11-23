"""Phase 8 backend: interviews, application notes/reject, job filters + paging, analytics calculations, settings validation + persistence.

Real throw-away MongoDB; configuration files are COPIES in tmp_path (the project's real config is never modified).
"""
import shutil
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
import yaml
from fastapi.testclient import TestClient

from backend.api.routes.analytics import get_analytics
from backend.api.routes.interviews import get_interviews
from backend.api.routes.settings import get_settings_service
from backend.main import app
from backend.services.analytics_service import AnalyticsService
from backend.services.settings_service import SettingsService
from config.automation_config import load_limits
from config.scoring_config import load_preferences, load_scoring_config
from config.settings import PROJECT_ROOT, get_settings
from database.connection import get_database_manager
from database.models.application import ApplicationRecord, SubmissionResult, idempotency_key
from database.repositories.applications_repository import ApplicationsRepository
from database.repositories.interviews_repository import InterviewsRepository
from database.repositories.scheduler_repository import SchedulerRepository

pytestmark = pytest.mark.integration
UTC = timezone.utc
IST = ZoneInfo("Asia/Kolkata")
TOKEN = "phase8-admin-token-abcdefghij"
H = {"X-Admin-Token": TOKEN}


@pytest.fixture
def cfg(tmp_path):
    for name in ("job_preferences.yaml", "scoring_weights.yaml", "automation_limits.yaml"):
        shutil.copy(PROJECT_ROOT / "config" / name, tmp_path / name)
    env = tmp_path / ".env"
    env.write_text("# my env\nANTHROPIC_API_KEY=sk-secret-value\nSECRET_KEY=" + "s" * 32 + "\nADMIN_TOKEN=" + TOKEN + "\nBROWSER_HEADLESS=true\n", encoding="utf-8")
    s = get_settings().model_copy(update={"job_preferences_file": str(tmp_path / "job_preferences.yaml"), "scoring_config_file": str(tmp_path / "scoring_weights.yaml"),
                                           "automation_limits_file": str(tmp_path / "automation_limits.yaml"), "admin_token": TOKEN, "secret_key": "s" * 32,
                                           "anthropic_api_key": "sk-secret-value", "notification_credentials_file": str(tmp_path / "g.cred")})
    return s, env, tmp_path


class FakeManager:
    def __init__(self, db):
        self.db = db

    def get_database(self):
        return self.db


@pytest.fixture
def client(test_db, cfg):
    s, env, tmp = cfg
    app.dependency_overrides[get_database_manager] = lambda: FakeManager(test_db)
    app.dependency_overrides[get_settings] = lambda: s
    app.dependency_overrides[get_settings_service] = lambda: SettingsService(s, test_db, env_path=env)
    yield TestClient(app)
    app.dependency_overrides.clear()


# ================================================================================================ interviews
def iv(**over):
    return {"company_name": "Northwind AI", "job_title": "ML Engineer", "round": "Technical 1", "scheduled_at": "2026-11-20T15:30:00+05:30", "kind": "video",
            "meeting_link": "https://meet.example.com/abc", "preparation_notes": "Revise RAG", **over}


def test_interview_create_read_update_and_history(client, test_db):
    r = client.post("/api/interviews", json=iv())
    assert r.status_code == 201
    d = r.json()
    assert d["status"] == "scheduled" and d["outcome"] == "pending" and "dup_key" not in d and d["scheduled_at"].startswith("2026-11-20T10:00")      # stored in UTC
    g = client.get(f"/api/interviews/{d['interview_id']}").json()
    assert g["company_name"] == "Northwind AI" and g["meeting_link"] == "https://meet.example.com/abc"
    u = client.put(f"/api/interviews/{d['interview_id']}", json={"status": "completed", "outcome": "advanced", "feedback": "Went well; asked about vector DBs."}).json()
    assert (u["status"], u["outcome"], u["feedback"]) == ("completed", "advanced", "Went well; asked about vector DBs.") and u["history"][-1]["changed"] == ["feedback", "outcome", "status"]
    again = client.put(f"/api/interviews/{d['interview_id']}", json={"status": "completed"}).json()
    assert len(again["history"]) == 1                                                                    # no-op update adds no history
    assert test_db["interviews"].count_documents({}) == 1


def test_interview_naive_times_are_read_in_the_scheduler_timezone(client):
    d = client.post("/api/interviews", json=iv(scheduled_at="2026-11-20T09:00:00")).json()
    assert d["scheduled_at"].startswith("2026-11-20T03:30")                                              # 09:00 IST = 03:30 UTC


def test_interview_validation_and_duplicates(client):
    assert client.post("/api/interviews", json=iv()).status_code == 201
    dup = client.post("/api/interviews", json=iv(company_name="  northwind ai ", job_title="ml engineer", round="technical 1"))
    assert dup.status_code == 409 and "already recorded" in dup.json()["detail"]
    assert client.post("/api/interviews", json=iv(round="Technical 2")).status_code == 201                  # another round is a different interview
    for bad in (iv(meeting_link="javascript:alert(1)"), iv(meeting_link="http://127.0.0.1/x"), iv(meeting_link="ftp://x.com/a"), iv(company_name=""), iv(duration_minutes=1),
                iv(status="bogus"), iv(scheduled_at="not a date"), {**iv(), "extra": "x"}, iv(application_id="zz"), iv(reminder_minutes_before=-1)):
        assert client.post("/api/interviews", json=bad).status_code == 422, bad
    assert client.post("/api/interviews", json=iv(application_id="a" * 32, round="X")).status_code == 404         # linked application must exist


def test_updating_into_a_duplicate_is_refused_and_unknown_fields_rejected(client):
    a = client.post("/api/interviews", json=iv()).json()
    b = client.post("/api/interviews", json=iv(round="Round 2")).json()
    assert client.put(f"/api/interviews/{b['interview_id']}", json={"round": "Technical 1"}).status_code == 409
    assert client.put(f"/api/interviews/{b['interview_id']}", json={"interview_id": "x"}).status_code == 422
    assert client.put(f"/api/interviews/{b['interview_id']}", json={"meeting_link": "javascript:1"}).status_code == 422
    assert client.put(f"/api/interviews/{'0' * 32}", json={"status": "completed"}).status_code == 404 and client.put(f"/api/interviews/{a['interview_id']}", json={"meeting_link": ""}).json()["meeting_link"] is None


def test_interview_list_filters_pagination_upcoming_and_reminders(client, test_db):
    now = datetime.now(UTC)
    rows = [("Alpha", "Dev", "R1", now + timedelta(minutes=30), 60, "scheduled"), ("Beta", "Dev", "R1", now + timedelta(days=2), 60, "scheduled"), ("Gamma", "QA", "R1", now + timedelta(days=30), 60, "scheduled"),
            ("Delta", "Dev", "R1", now - timedelta(days=2), 60, "completed"), ("Eps", "Dev", "R1", now + timedelta(minutes=45), 10, "scheduled"), ("Zeta", "Dev", "R1", now + timedelta(hours=5), 60, "cancelled")]
    for c, t, r, when, rem, st in rows:
        d = client.post("/api/interviews", json={**iv(company_name=c, job_title=t, round=r, reminder_minutes_before=rem), "scheduled_at": when.isoformat()}).json()
        client.put(f"/api/interviews/{d['interview_id']}", json={"status": st})
    lst = client.get("/api/interviews").json()
    assert lst["total"] == 6 and [i["company_name"] for i in lst["items"]][0] == "Delta"                  # oldest first by default
    assert client.get("/api/interviews", params={"status": "scheduled"}).json()["total"] == 4 and client.get("/api/interviews", params={"q": "gam"}).json()["total"] == 1
    p1 = client.get("/api/interviews", params={"limit": 2, "skip": 0}).json(); p2 = client.get("/api/interviews", params={"limit": 2, "skip": 2}).json()
    assert p1["total"] == 6 and len(p1["items"]) == 2 and len(p2["items"]) == 2 and not {i["interview_id"] for i in p1["items"]} & {i["interview_id"] for i in p2["items"]}
    assert client.get("/api/interviews", params={"newest_first": True}).json()["items"][0]["company_name"] == "Gamma"
    up = client.get("/api/interviews/upcoming", params={"hours": 72}).json()
    assert [i["company_name"] for i in up] == ["Alpha", "Eps", "Beta"] and all(i["status"] == "scheduled" for i in up)
    due = [i["company_name"] for i in client.get("/api/interviews/reminders").json()]
    assert due == ["Alpha"]                                                                              # Eps starts in 45 min but its reminder is only 10 min before
    assert client.get("/api/interviews", params={"limit": 0}).status_code == 422 and client.get("/api/interviews/zz").status_code == 422
    assert client.delete(f"/api/interviews/{lst['items'][0]['interview_id']}").status_code == 204 and client.get("/api/interviews").json()["total"] == 5
    assert client.delete(f"/api/interviews/{'0' * 32}").status_code == 404


# ================================================================================================ application notes + reject
def app_doc(job="a" * 32, state="READY_FOR_REVIEW", **kw):
    return ApplicationRecord(idempotency_key=idempotency_key("r" * 32, job), job_id=job, resume_id="r" * 32, profile_id="p" * 32, profile_version=1, source=kw.pop("source", "local_test"),
                             state=state, job_title=kw.pop("job_title", "ML Engineer"), company_name=kw.pop("company_name", "Northwind"), **kw)


@pytest.fixture
def app_client(test_db, make_matching_service, seeded, cfg, tmp_path):
    """ApplicationService on the real database (no browser is used by notes/reject/page)."""
    from backend.api.routes.applications import get_application_service
    from backend.services.application_service import ApplicationService
    from backend.services.document_service import DocumentService
    from browser.screenshot_manager import ScreenshotManager
    from browser.session_manager import SessionManager
    from config.automation_config import load_limits
    from database.repositories.documents_repository import DocumentsRepository
    from engines.application.application_engine import ApplicationEngine
    from engines.application.retry_manager import RetryManager
    from integrations.application_policy import ApplicationPolicy
    from integrations.policy import PolicyRegistry
    s = cfg[0]
    m = make_matching_service()
    apps = ApplicationsRepository(test_db)
    shots, limits = ScreenshotManager(tmp_path / "shots"), load_limits(s)
    svc = ApplicationService(apps, m, DocumentService(DocumentsRepository(test_db), m, s, m.catalog, m.prefs, m.cfg), s, ApplicationPolicy.load(s, PolicyRegistry.load(s)),
                             ApplicationEngine(s, SessionManager(s), shots, limits), RetryManager(apps, 2), limits, shots)
    app.dependency_overrides[get_application_service] = lambda: svc
    return TestClient(app), apps, svc


def test_notes_are_saved_searchable_and_audited_without_content(app_client, test_db):
    c, apps, svc = app_client
    a = apps.insert(app_doc(state="SUBMITTED", submission=SubmissionResult(outcome="confirmed", reference="REF-42"), submitted_at=datetime.now(UTC)))
    r = c.put(f"/api/applications/{a['application_id']}/notes", json={"notes": "  Recruiter: Priya, follow up Friday  "})
    assert r.status_code == 200 and r.json()["user_notes"] == "Recruiter: Priya, follow up Friday"
    page = c.get("/api/applications/page", params={"q": "priya"}).json()
    assert page["total"] == 1 and page["items"][0]["user_notes"].startswith("Recruiter") and c.get("/api/applications/page", params={"q": "REF-42"}).json()["total"] == 1
    ev = [e for e in apps.list_events(a["application_id"]) if e["type"] == "notes_updated"]
    assert len(ev) == 1 and "Priya" not in str(ev) and ev[0]["detail"] == {"length": len("Recruiter: Priya, follow up Friday")}
    assert c.put(f"/api/applications/{a['application_id']}/notes", json={"notes": ""}).json()["user_notes"] is None
    assert c.put(f"/api/applications/{a['application_id']}/notes", json={"notes": "x" * 2001}).status_code == 422 and c.put(f"/api/applications/{'0' * 32}/notes", json={}).status_code == 404


def test_reject_withdraws_approval_cancels_the_queue_entry_and_blocks_submission(app_client, test_db):
    from backend.services.application_service import ActionNotAllowed
    from database.repositories.queue_repository import QueueRepository
    c, apps, svc = app_client
    a = apps.insert(app_doc(state="APPROVED", mode="automated", approval={"plan_hash": "p" * 64}))
    QueueRepository(test_db).enqueue(idempotency_key=a["idempotency_key"], job_id=a["job_id"], resume_id="r" * 32, priority=90, match_score=90, eligibility="PASS", job_title="T", company_name="C", source="x", max_attempts=4)
    r = c.post(f"/api/applications/{a['application_id']}/reject", json={"reason": "salary too low"})
    d = r.json()
    assert r.status_code == 200 and d["state"] == "READY_FOR_REVIEW" and d["approval"] is None and d["dismissed_at"] and d["dismissed_reason"] == "salary too low"
    assert test_db["application_queue"].find_one({})["status"] == "cancelled"
    assert "rejected" in [e["type"] for e in apps.list_events(a["application_id"])]
    for action in (lambda: svc.approve(a["application_id"]), lambda: svc.submit(a["application_id"])):
        with pytest.raises(ActionNotAllowed) as exc:
            action()
        assert "rejected" in str(exc.value)
    assert c.get("/api/applications/page", params={"dismissed": False}).json()["total"] == 0 and c.get("/api/applications/page", params={"dismissed": True}).json()["total"] == 1
    assert c.post(f"/api/applications/{a['application_id']}/reject", json={}).status_code == 200            # idempotent


@pytest.mark.parametrize("state", ["SUBMITTING", "SUBMITTED", "VERIFICATION_PENDING", "VERIFIED"])
def test_a_possibly_submitted_application_cannot_be_rejected(app_client, state):
    c, apps, svc = app_client
    a = apps.insert(app_doc(state=state))
    assert c.post(f"/api/applications/{a['application_id']}/reject", json={}).status_code == 409


def test_applications_page_filters_dates_and_pagination(app_client):
    c, apps, svc = app_client
    now = datetime.now(UTC)
    for i in range(7):
        a = apps.insert(app_doc(job=f"{i:032x}", state="SUBMITTED" if i % 2 else "AWAITING_USER_INPUT", company_name=f"Co{i}"))
        apps.col.update_one({"application_id": a["application_id"]}, {"$set": {"updated_at": now - timedelta(days=i)}})   # (update() always stamps "now")
    allp = c.get("/api/applications/page", params={"limit": 3}).json()
    assert allp["total"] == 7 and len(allp["items"]) == 3
    seen = {i["application_id"] for s in (0, 3, 6) for i in c.get("/api/applications/page", params={"limit": 3, "skip": s}).json()["items"]}
    assert len(seen) == 7                                                                               # pages cover everything exactly once
    assert c.get("/api/applications/page", params={"state": "SUBMITTED"}).json()["total"] == 3
    assert c.get("/api/applications/page", params={"date_from": (now - timedelta(days=2, hours=12)).isoformat()}).json()["total"] == 3
    assert c.get("/api/applications/page", params={"limit": 0}).status_code == 422


# ================================================================================================ jobs filters + paging, match filters
def add_job(db, i, **kw):
    d = {"job_id": f"{i:032x}", "content_hash": f"h{i}", "source": kw.pop("source", "career_pages"), "status": "active", "job_title": kw.pop("title", f"Engineer {i}"), "company_name": kw.pop("company", f"Co{i}"),
         "location": kw.pop("location", "Pune"), "discovered_at": kw.pop("discovered_at", datetime.now(UTC)), "created_at": datetime.now(UTC), "last_updated_at": datetime.now(UTC),
         "mandatory_skills": [], "preferred_skills": [], "source_refs": [], "job_description": "x", **kw}
    db["jobs"].insert_one(dict(d))
    return d


def add_match(db, job_id, score, cls="Good Match", elig="PASS", rec="recommended", created=None, resume="r" * 32):
    db["job_matches"].insert_one({"match_id": f"m{job_id[:8]}{int(score)}", "job_id": job_id, "resume_id": resume, "match_score": score, "classification": cls, "eligibility_status": elig,
                                  "recommendation_code": rec, "created_at": created or datetime.now(UTC)})


def test_jobs_page_filters_by_match_score_dates_source_and_paginates(client, test_db):
    from backend.api.routes.jobs import get_discovery_service
    from backend.services.discovery_service import DiscoveryService
    from database.repositories.discovery_runs_repository import DiscoveryRunsRepository
    from database.repositories.jobs_repository import JobsRepository
    from engines.discovery.discovery_engine import DiscoveryEngine
    from integrations.policy import PolicyRegistry
    s = get_settings()
    reg = PolicyRegistry.load(s)
    jobs, runs = JobsRepository(test_db), DiscoveryRunsRepository(test_db)
    app.dependency_overrides[get_discovery_service] = lambda: DiscoveryService(jobs, runs, reg, {}, None, None)
    base = datetime(2026, 11, 1, tzinfo=UTC)
    for i in range(12):
        add_job(test_db, i, source="career_pages" if i < 8 else "manual_paste", discovered_at=base + timedelta(days=i), title="ML Engineer" if i % 2 == 0 else "Data Analyst")
    add_match(test_db, f"{0:032x}", 92); add_match(test_db, f"{1:032x}", 70); add_match(test_db, f"{2:032x}", 80)
    add_match(test_db, f"{2:032x}", 60, created=datetime.now(UTC) + timedelta(seconds=5))              # job 2's LATEST score is 60
    p = client.get("/api/jobs/page", params={"limit": 5}).json()
    assert p["total"] == 12 and len(p["items"]) == 5
    assert {i["job_id"] for s_ in (0, 5, 10) for i in client.get("/api/jobs/page", params={"limit": 5, "skip": s_}).json()["items"]} == {f"{i:032x}" for i in range(12)}
    hi = client.get("/api/jobs/page", params={"min_match_score": 75}).json()
    assert hi["total"] == 1 and hi["items"][0]["job_id"] == f"{0:032x}"
    assert client.get("/api/jobs/page", params={"min_match_score": 60}).json()["total"] == 3 and client.get("/api/jobs/page", params={"min_match_score": 99}).json()["total"] == 0
    assert client.get("/api/jobs/page", params={"source": "manual_paste"}).json()["total"] == 4 and client.get("/api/jobs/page", params={"title": "analyst"}).json()["total"] == 6
    rng = client.get("/api/jobs/page", params={"discovered_after": "2026-11-03T00:00:00+00:00", "discovered_before": "2026-11-06T00:00:00+00:00"}).json()
    assert rng["total"] == 3
    assert client.get("/api/jobs/page", params={"min_match_score": 101}).status_code == 422
    DiscoveryRunsRepository(test_db).create({}, ["career_pages"], {})
    assert len(client.get("/api/jobs/discovery/runs").json()) == 1
    app.dependency_overrides.pop(get_discovery_service, None)


def test_match_results_can_be_filtered_by_recommendation(matching_api):
    from tests.fixtures.jd_dataset import JDS
    for jd in (JDS[0], JDS[3], JDS[7]):
        r = matching_api.post("/api/matching/analyze-jd", json={"job_description": jd["text"], "job_title": jd["title"], "company_name": jd["company"]})
        assert matching_api.post("/api/matching/score", json={"job_id": r.json()["job_id"]}).status_code == 200
    allr = matching_api.get("/api/matching/results").json()
    codes = {x["recommendation_code"] for x in allr}
    assert len(allr) == 3 and codes
    for code in codes:
        sub = matching_api.get("/api/matching/results", params={"recommendation_code": code}).json()
        assert sub and all(x["recommendation_code"] == code for x in sub) and len(sub) == sum(1 for x in allr if x["recommendation_code"] == code)
    assert matching_api.get("/api/matching/results", params={"recommendation_code": "no_such_code"}).json() == []

# ================================================================================================ analytics
@pytest.fixture
def analytics(test_db, cfg):
    s = cfg[0]
    clock = [datetime(2026, 11, 13, 6, 0, tzinfo=UTC)]                                                   # 11:30 IST on the 13th
    svc = AnalyticsService(test_db, s, now_fn=lambda: clock[0])
    app.dependency_overrides[get_database_manager] = lambda: FakeManager(test_db)
    app.dependency_overrides[get_settings] = lambda: s
    app.dependency_overrides[get_analytics] = lambda: svc
    yield svc, test_db
    app.dependency_overrides.clear()          # never leak an override (pointing at this test's database) into the next test


def seed_analytics(db):
    t = lambda d, h=6: datetime(2026, 11, d, h, 0, tzinfo=UTC)  # noqa: E731
    for i, (d, src) in enumerate([(10, "career_pages"), (10, "career_pages"), (11, "manual_paste"), (12, "career_pages"), (13, "career_pages"), (13, "linkedin")]):
        add_job(db, i, source=src, discovered_at=t(d))
    for i, (sc, cls, el) in enumerate([(92, "Excellent Match", "PASS"), (83, "Strong Match", "PASS"), (76, "Good Match", "UNKNOWN"), (65, "Moderate Match", "PASS"), (40, "Low Match", "FAIL"), (78, "Good Match", "PASS")]):
        add_match(db, f"{i:032x}", sc, cls, el, created=t(10 + i // 2))
    apps = ApplicationsRepository(db)
    specs = [("SUBMITTED", "confirmed", "career_pages", t(12)), ("VERIFIED", "confirmed", "career_pages", t(12)), ("SUBMITTED", "user_reported", "linkedin", t(13)), ("VERIFICATION_PENDING", "unconfirmed", "career_pages", t(13)),
             ("SUBMISSION_FAILED", "failed", "career_pages", None), ("READY_FOR_REVIEW", "not_attempted", "career_pages", None), ("MANUAL_HANDOFF_REQUIRED", "not_attempted", "linkedin", None)]
    for i, (state, outcome, src, sub) in enumerate(specs):
        a = apps.insert(app_doc(job=f"{i + 100:032x}", state=state, source=src, submission=SubmissionResult(outcome=outcome), submitted_at=sub, failure_stage="after_submit" if state == "SUBMISSION_FAILED" else None))
        apps.update(a["application_id"], {"created_at": t(12)})
        # the repository stamps the real "now" on every update; write the fixed test date directly so the test does not depend on today's date
        db["applications"].update_one({"application_id": a["application_id"]}, {"$set": {"updated_at": t(13)}})
    return apps


def test_dashboard_metrics_come_from_the_stored_records(analytics, client):
    svc, db = analytics
    empty = svc.dashboard()
    assert empty["jobs"]["total"] == 0 and empty["jobs"]["average_match_score"] is None and empty["applications"]["submitted"] == 0 and empty["interviews"]["upcoming_7_days"] == 0
    assert all(d["jobs_discovered"] == 0 for d in empty["activity"]) and len(empty["activity"]) == 14 and empty["recent"] == []
    seed_analytics(db)
    db["interviews"].insert_one({"interview_id": "i" * 32, "company_name": "Co", "job_title": "T", "round": "R1", "dup_key": "dash1", "scheduled_at": datetime(2026, 11, 15, 6, tzinfo=UTC), "status": "scheduled",
                                 "outcome": "pending", "created_at": datetime(2026, 11, 12, tzinfo=UTC), "meeting_link": None})
    d = svc.dashboard()
    assert d["jobs"]["total"] == 6 and d["jobs"]["scored"] == 0 or d["jobs"]["scored"] == 6
    assert d["jobs"]["discovered_today"] == 2
    a = d["applications"]
    assert (a["awaiting_approval"], a["submitted"], a["verified"], a["failed"]) == (1, 3, 1, 1)
    assert a["pending_actions"]["verify_outcome"] == 1 and a["pending_actions"]["manual_handoff"] == 1 and a["pending_actions"]["ready_to_approve"] == 1 and a["pending_actions_total"] == 3
    assert d["interviews"]["upcoming_7_days"] == 1 and d["interviews"]["next"][0]["company_name"] == "Co"
    assert sum(x["jobs_discovered"] for x in d["activity"]) == 6 and d["activity"][-1]["date"] == "2026-11-13" and d["activity"][-1]["jobs_discovered"] == 2
    assert d["system"]["database"] == "connected" and d["system"]["ai_key_configured"] is True and "sk-secret" not in str(d)


def test_matching_stats_use_the_latest_match_per_job_and_the_effective_minimum(analytics):
    svc, db = analytics
    seed_analytics(db)
    add_match(db, f"{0:032x}", 50, "Low Match", "PASS", created=datetime(2026, 11, 13, 5, tzinfo=UTC))   # job 0's latest score dropped to 50
    d = svc.dashboard()
    assert d["jobs"]["scored"] == 6 and d["jobs"]["average_match_score"] == round((50 + 83 + 76 + 65 + 40 + 78) / 6, 1)
    assert d["jobs"]["matching_profile"] == 2 and d["min_match_score"] == load_limits(get_settings()).min_match_score                          # 83 PASS, 78 PASS; 76 is UNKNOWN (not a good match), 40 FAIL
    SchedulerRepository(db).set_state({"limit_overrides": {"min_match_score": 80}})
    assert svc.dashboard()["jobs"]["matching_profile"] == 1


def test_analytics_overview_is_internally_consistent(analytics, client):
    svc, db = analytics
    seed_analytics(db)
    o = svc.overview("2026-11-10", "2026-11-13")
    assert o["days"] == 4 and [r["date"] for r in o["daily"]] == ["2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"]
    assert o["totals"]["jobs_discovered"] == 6 == sum(r["jobs_discovered"] for r in o["daily"]) == sum(w["jobs_discovered"] for w in o["weekly"])
    for k in o["totals"]:
        assert sum(w[k] for w in o["weekly"]) == o["totals"][k]                                          # weekly buckets add up to the daily totals
    assert sum(x["jobs"] for x in o["score_distribution"]) == o["scored_jobs"] == 6
    assert {x["range"]: x["jobs"] for x in o["score_distribution"]}["90-100"] == 1 and {x["range"]: x["jobs"] for x in o["score_distribution"]}["70-79"] == 2
    assert sum(o["applications_by_status"].values()) == 7
    oc = o["application_outcomes"]
    assert (oc["confirmed"], oc["self_reported"], oc["pending_verification"], oc["failed_after_send"]) == (2, 1, 1, 1) and oc["attempts"] == 5
    assert oc["success_rate"] == 60.0 and oc["failure_rate"] == 20.0 and oc["unknown_rate"] == 20.0 and round(oc["success_rate"] + oc["failure_rate"] + oc["unknown_rate"], 1) == 100.0
    src = {s["source"]: s for s in o["sources"]}
    assert src["career_pages"]["jobs"] == 4 and src["manual_paste"]["jobs"] == 1 and src["linkedin"]["jobs"] == 1 and sum(s["jobs"] for s in o["sources"]) == 6
    assert src["career_pages"]["applications"] == 5 and src["career_pages"]["submitted"] == 2 and src["linkedin"]["submitted"] == 1
    assert sum(s["applications"] for s in o["sources"]) == 7 == sum(o["applications_by_status"].values())
    narrow = svc.overview("2026-11-12", "2026-11-12")
    assert narrow["totals"]["jobs_discovered"] == 1 and narrow["application_outcomes"]["confirmed"] == 2


def test_interview_conversion_and_rate_edge_cases(analytics):
    svc, db = analytics
    assert svc.overview("2026-11-10", "2026-11-13")["application_outcomes"]["success_rate"] is None       # no attempts -> no rate, not 0 %
    seed_analytics(db)
    for i, (st, out) in enumerate([("completed", "offer"), ("completed", "rejected"), ("scheduled", "pending"), ("completed", "advanced")]):
        db["interviews"].insert_one({"interview_id": f"{i}" * 32, "company_name": "C", "job_title": "T", "round": f"R{i}", "dup_key": f"k{i}", "scheduled_at": datetime(2026, 11, 12, 6, tzinfo=UTC), "status": st, "outcome": out,
                                     "created_at": datetime(2026, 11, 12, tzinfo=UTC)})
    c = svc.overview("2026-11-10", "2026-11-13")["interview_conversion"]
    assert (c["interviews"], c["completed"], c["offers"], c["rejected"], c["advanced"]) == (4, 3, 1, 1, 1) and c["submitted_applications"] == 3
    assert c["interviews_per_submission"] == round(4 / 3, 2) and c["offer_rate_of_completed"] == round(100 / 3, 1)


def test_range_validation_and_timezone_day_boundaries(analytics, client):
    svc, db = analytics
    for a, b in (("2026-11-13", "2026-11-10"), ("2025-01-01", "2026-11-13"), ("garbage", None)):
        assert client.get("/api/analytics/overview", params={"date_from": a, "date_to": b} if b else {"date_from": a}).status_code == 422
    add_job(db, 1, discovered_at=datetime(2026, 11, 12, 18, 29, tzinfo=UTC))                              # 23:59 IST on the 12th
    add_job(db, 2, discovered_at=datetime(2026, 11, 12, 18, 31, tzinfo=UTC))                              # 00:01 IST on the 13th
    d = {r["date"]: r["jobs_discovered"] for r in svc.overview("2026-11-12", "2026-11-13")["daily"]}
    assert d == {"2026-11-12": 1, "2026-11-13": 1}
    assert client.get("/api/analytics/overview").status_code == 200 and client.get("/api/dashboard/summary", params={"days": 2}).status_code == 422


def test_csv_export_matches_the_report_and_neutralises_formulas(analytics, client):
    svc, db = analytics
    seed_analytics(db)
    r = client.get("/api/analytics/export", params={"kind": "daily", "date_from": "2026-11-10", "date_to": "2026-11-13"})
    lines = r.text.strip().splitlines()
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv") and "attachment" in r.headers["content-disposition"]
    assert lines[0].startswith("date,jobs_discovered") and len(lines) == 5
    assert sum(int(l.split(",")[1]) for l in lines[1:]) == 6
    for kind in ("weekly", "status", "sources", "scores"):
        assert client.get("/api/analytics/export", params={"kind": kind}).status_code == 200
    assert client.get("/api/analytics/export", params={"kind": "bogus"}).status_code == 422
    add_job(db, 77, source="=cmd|' /C calc'!A0", discovered_at=datetime(2026, 11, 13, 6, tzinfo=UTC))
    src = client.get("/api/analytics/export", params={"kind": "sources", "date_from": "2026-11-13", "date_to": "2026-11-13"}).text
    assert "'=cmd" in src and "\n=cmd" not in src


# ================================================================================================ settings
def test_settings_read_exposes_no_secret(client, cfg):
    s, env, tmp = cfg
    r = client.get("/api/settings")
    txt = r.text
    assert r.status_code == 200 and "sk-secret-value" not in txt and TOKEN not in txt and "s" * 32 not in txt and "mongodb://" not in txt
    d = r.json()
    assert d["ai"]["api_key_configured"] is True and d["security"] == {"admin_token_configured": True, "secret_key_configured": True, "master_automation_switch": False}
    assert d["scoring"]["weights"]["technical_skills"] == 30 and d["automation"]["caps"]["max_applications_per_day"] == 20 and d["scheduler"]["timezone"] == "Asia/Kolkata"
    assert {x["source"] for x in d["sources"]} >= {"linkedin", "indeed", "naukri", "career_pages"} and any(p["platform"] == "linkedin" and p["status"] == "MANUAL_HANDOFF" for p in d["application_platforms"])
    assert "ANTHROPIC_API_KEY" not in d["env_editable"] and "ADMIN_TOKEN" not in d["env_editable"] and d["notifications"]["configured"] is False


WRITES = [("put", "/api/settings/preferences", {"values": {"remote": True}}), ("put", "/api/settings/scoring", {"weights": {}}), ("put", "/api/settings/automation", {"values": {}}), ("put", "/api/settings/env", {"values": {}})]


@pytest.mark.parametrize("method,path,body", WRITES)
def test_every_settings_write_needs_the_admin_token(client, method, path, body):
    assert getattr(client, method)(path, json=body).status_code == 401 and getattr(client, method)(path, json=body, headers={"X-Admin-Token": "nope"}).status_code == 401
    assert getattr(client, method)(path, json=body, headers=H).status_code == 200


def test_preferences_are_validated_saved_with_comments_and_survive_a_reload(client, cfg):
    s, env, tmp = cfg
    before = (tmp / "job_preferences.yaml").read_text(encoding="utf-8")
    ok = client.put("/api/settings/preferences", headers=H, json={"values": {"titles": ["Machine Learning Engineer", "Data Scientist"], "locations": ["Pune", "Bengaluru"], "remote": True,
                                                                              "min_salary": 2500000, "salary_currency": "INR", "experience_years_min": 3, "experience_years_max": 8, "notice_period": "30 days"}})
    assert ok.status_code == 200 and set(ok.json()["saved"]) == {"titles", "locations", "min_salary", "salary_currency", "experience_years_min", "experience_years_max", "notice_period"}
    text = (tmp / "job_preferences.yaml").read_text(encoding="utf-8")
    assert "# Your job preferences" in text and "# preferred job titles" in text and text.count("\n") == before.count("\n")                       # comments and layout preserved
    reloaded = load_preferences(s)
    assert reloaded.titles == ["Machine Learning Engineer", "Data Scientist"] and reloaded.min_salary == 2500000 and reloaded.salary_currency == "INR" and reloaded.notice_period == "30 days"
    assert client.get("/api/settings").json()["preferences"]["locations"] == ["Pune", "Bengaluru"]                                               # persisted: a fresh read sees it
    assert client.put("/api/settings/preferences", headers=H, json={"values": {"remote": True}}).json()["saved"] == []                           # unchanged -> nothing written


@pytest.mark.parametrize("values,needle", [({"experience_years_min": 9, "experience_years_max": 3}, "cannot exceed"), ({"min_salary": 100}, "salary_currency"), ({"nonsense": 1}, "unknown preference"),
                                           ({"titles": "not a list"}, "titles"), ({"remote": "maybe"}, "remote"), ({"posted_within_days": 0}, "posted_within_days")])
def test_invalid_preferences_are_rejected_and_nothing_is_written(client, cfg, values, needle):
    s, env, tmp = cfg
    before = (tmp / "job_preferences.yaml").read_bytes()
    r = client.put("/api/settings/preferences", headers=H, json={"values": values})
    assert r.status_code == 422 and needle in " ".join(r.json()["detail"]["problems"]) and (tmp / "job_preferences.yaml").read_bytes() == before


def test_scoring_changes_are_validated_bump_the_version_back_up_and_report_before_after(client, cfg):
    s, env, tmp = cfg
    cur = load_scoring_config(s).scoring_version                                   # the shipped version (1.1 now); a change bumps its minor number
    nxt = f"{cur.split('.')[0]}.{int(cur.split('.')[1]) + 1}"
    old_hash = load_scoring_config(s).config_hash
    r = client.put("/api/settings/scoring", headers=H, json={"weights": {"technical_skills": 35, "experience": 15}, "thresholds": {"good": 76}})
    d = r.json()
    assert r.status_code == 200 and d["saved"] is True and d["scoring_version"] == nxt and d["before"]["weights"]["technical_skills"] == 30 and d["after"]["weights"]["technical_skills"] == 35
    assert d["before"]["thresholds"]["good"] == 75 and d["after"]["thresholds"]["good"] == 76 and "re-run" in d["note"]
    new = load_scoring_config(s)
    assert new.scoring_version == nxt and new.weights["technical_skills"] == 35 and new.config_hash != old_hash and sum(new.weights.values()) == 100
    text = (tmp / "scoring_weights.yaml").read_text(encoding="utf-8")
    assert "# AI JobPilot - matching score configuration." in text and "# 90-100  Excellent Match" in text
    backups = list((tmp / "backups").glob(f"scoring_weights_{cur}_*.yaml"))
    assert len(backups) == 1 and "technical_skills: 30" in backups[0].read_text(encoding="utf-8")
    assert client.put("/api/settings/scoring", headers=H, json={"weights": {"technical_skills": 35}}).json()["saved"] is False                   # no-op: no version bump


@pytest.mark.parametrize("body,needle", [({"weights": {"technical_skills": 50}}, "total 100"), ({"weights": {"nope": 1}}, "unknown weight"), ({"weights": {"technical_skills": -5, "experience": 55}}, "non-negative"),
                                         ({"thresholds": {"excellent": 70}}, "excellent > strong"), ({"thresholds": {"good": 120}}, "good"), ({"missing_data_policy": "guess"}, "missing_data_policy"),
                                         ({"recommendation_threshold": 150}, "recommendation_threshold")])
def test_invalid_scoring_is_rejected_without_touching_the_file(client, cfg, body, needle):
    s, env, tmp = cfg
    before = (tmp / "scoring_weights.yaml").read_bytes()
    r = client.put("/api/settings/scoring", headers=H, json=body)
    assert r.status_code == 422 and needle in " ".join(r.json()["detail"]["problems"]), r.text
    assert (tmp / "scoring_weights.yaml").read_bytes() == before and not (tmp / "backups").exists()


def test_automation_caps_are_validated_and_persisted_and_the_delay_cannot_be_loosened(client, cfg):
    s, env, tmp = cfg
    ok = client.put("/api/settings/automation", headers=H, json={"values": {"max_applications_per_day": 25, "max_jobs_processed_per_day": 80, "max_retries_per_application": 2, "retry_backoff_seconds": 120,
                                                                              "discovery_time": "06:30", "application_window": ["08:00", "20:00"], "min_match_score": 78}})
    assert ok.status_code == 200 and ok.json()["automation"]["max_applications_per_day"] == 25
    from config.automation_config import load_limits
    lim = load_limits(s)
    assert (lim.max_applications_per_day, lim.max_jobs_processed_per_day, lim.max_retries_per_application, lim.retry_backoff_seconds, lim.discovery_time, lim.application_window, lim.min_match_score) == (25, 80, 2, 120.0, "06:30", ("08:00", "20:00"), 78.0)
    text = (tmp / "automation_limits.yaml").read_text(encoding="utf-8")
    assert "# Automation limits. These are HARD CAPS" in text and "delay_seconds_between_actions: [3, 8]" in text and "# a goal to aim for" in text.replace("# a goal to aim for (20-30)", "# a goal to aim for")
    for bad, needle in (({"max_applications_per_day": 500}, "between 0 and 100"), ({"max_applications_per_day": -1}, "between"), ({"max_applications_per_day": 1.5}, "whole number"), ({"discovery_time": "25:99"}, "HH:MM"),
                        ({"application_window": ["09:00"]}, "[start, end]"), ({"delay_seconds_between_actions": 0}, "not adjustable"), ({"timezone": "x"}, "cannot be changed"),
                        ({"max_applications_per_platform_per_day": 40, "max_applications_per_day": 20}, "cannot exceed"), ({"max_concurrent_workers": 0}, "between 1 and 4"), ({"max_applications_per_day": True}, "whole number")):
        r = client.put("/api/settings/automation", headers=H, json={"values": bad})
        assert r.status_code == 422 and needle in " ".join(r.json()["detail"]["problems"]), (bad, r.text)
    assert load_limits(s).max_applications_per_day == 25


def test_env_settings_use_an_allow_list_keep_other_lines_and_never_touch_secrets(client, cfg):
    s, env, tmp = cfg
    r = client.put("/api/settings/env", headers=H, json={"values": {"BROWSER_HEADLESS": False, "BROWSER_TIMEOUT_MS": 20000, "SCHEDULER_TIMEZONE": "Europe/London", "BROWSER_CHANNEL": "msedge"}})
    assert r.status_code == 200 and r.json()["restart_required"] is True and set(r.json()["saved"]) == {"BROWSER_HEADLESS", "BROWSER_TIMEOUT_MS", "SCHEDULER_TIMEZONE", "BROWSER_CHANNEL"}
    text = env.read_text(encoding="utf-8")
    assert "# my env" in text and "ANTHROPIC_API_KEY=sk-secret-value" in text and f"ADMIN_TOKEN={TOKEN}" in text                                  # untouched lines survive
    assert "BROWSER_HEADLESS=false" in text and text.count("BROWSER_HEADLESS") == 1 and "BROWSER_TIMEOUT_MS=20000" in text and "SCHEDULER_TIMEZONE=Europe/London" in text
    for k in ("ANTHROPIC_API_KEY", "SECRET_KEY", "ADMIN_TOKEN", "MONGODB_URI", "APPLICATION_AUTOMATION_ENABLED", "ALLOW_LOCAL_TEST_FORMS"):
        r = client.put("/api/settings/env", headers=H, json={"values": {k: "x"}})
        assert r.status_code == 422 and "cannot be changed here" in r.text, k
    for bad, needle in (({"BROWSER_TIMEOUT_MS": 5}, "between"), ({"SCHEDULER_TIMEZONE": "Mars/Base"}, "time zone"), ({"BROWSER_HEADLESS": "yes"}, "true or false"), ({"BROWSER_CHANNEL": "firefox"}, "one of"),
                        ({"NOTIFICATION_RECIPIENT": "nope"}, "e-mail"), ({"UNKNOWN_KEY": 1}, "not an editable"), ({"NOTIFICATIONS_ENABLED": True, "NOTIFICATION_RECIPIENT": ""}, "valid NOTIFICATION_RECIPIENT")):
        assert needle in client.put("/api/settings/env", headers=H, json={"values": bad}).text, bad
    assert "sk-secret-value" in env.read_text(encoding="utf-8")


def test_sessions_cannot_be_persisted_without_a_secret_key(client, cfg, test_db):
    s, env, tmp = cfg
    svc = SettingsService(s.model_copy(update={"secret_key": ""}), test_db, env_path=env)
    from backend.services.settings_service import SettingsError
    with pytest.raises(SettingsError) as exc:
        svc.update_env({"PERSIST_BROWSER_SESSIONS": True})
    assert "SECRET_KEY" in str(exc.value)


def test_a_corrupt_result_never_replaces_the_real_file(cfg, test_db, monkeypatch):
    s, env, tmp = cfg
    svc = SettingsService(s, test_db, env_path=env)
    from backend.services import settings_service as mod
    from backend.services.settings_service import SettingsError
    monkeypatch.setattr(mod, "set_value", lambda text, path, value: "weights: [this is: not valid\n")
    before = (tmp / "scoring_weights.yaml").read_bytes()
    with pytest.raises(SettingsError):
        svc.update_scoring({"weights": {"technical_skills": 31, "experience": 19}})
    assert (tmp / "scoring_weights.yaml").read_bytes() == before


def test_recent_activity_hides_idle_scheduler_ticks(analytics):
    svc, db = analytics
    t = datetime(2026, 11, 13, 5, tzinfo=UTC)
    runs = [("application_worker", {"processed": [], "recovered": 0, "synced": 0}), ("notification_retry", {"retried": 0, "sent": 0, "failed": 0}),
            ("daily_discovery", {"new": 4, "queued": 1}), ("application_worker", {"processed": ["x"], "recovered": 0, "synced": 0})]
    for i, (job, res) in enumerate(runs):
        db["scheduler_runs"].insert_one({"run_id": f"{i:032x}", "job": job, "trigger": "scheduled", "status": "completed", "started_at": t + timedelta(minutes=i), "result": res})
    texts = [r["text"] for r in svc.dashboard()["recent"] if r["kind"] == "scheduler"]
    assert texts == ["application worker (scheduled) completed: 1 application(s) prepared", "daily discovery (scheduled) completed: 4 new job(s), 1 queued"]
