"""Repository, engine and API on a real throw-away MongoDB with MOCKED sources (no live scraping)."""
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.api.routes.jobs import get_discovery_service
from backend.main import app
from backend.services.discovery_service import DiscoveryService
from config.scoring_config import Preferences, load_scoring_config
from config.settings import Settings
from database.models.job import JobRecord
from database.repositories.discovery_runs_repository import DiscoveryRunsRepository
from database.repositories.jobs_repository import JobsRepository
from engines.discovery.discovery_engine import DiscoveryEngine, SourceStats, matches_query, query_from_preferences
from engines.discovery.job_normalizer import normalize
from engines.matching.skill_extractor import get_catalog
from integrations.base_adapter import AdapterError, AdapterResult, JobAdapter, RawJob, SearchQuery, SourceBlocked
from integrations.career_pages.adapter import CompanySource
from integrations.policy import Policy, PolicyRegistry, PolicyViolation, SourceStatus
from integrations.registry import build_adapters
from tests.fixtures.discovery_data import GREENHOUSE_LIST, LEVER_LIST, Recorder, json_response

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("no_network")]
NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
GH_BASE = "https://boards-api.greenhouse.io/v1/boards/acmeanalytics"
LV_BASE = "https://api.lever.co/v0/postings/betalabs"


# ---------------------------------------------------------------------------------------- fixtures
@pytest.fixture
def repos(test_db):
    return JobsRepository(test_db), DiscoveryRunsRepository(test_db)


@pytest.fixture
def make_env(test_db):
    """A full discovery stack with a mocked HTTP layer. Returns (service, recorder, engine)."""
    def _make(routes=None, companies=None, prefs=None, registry=None):
        rec = Recorder(routes if routes is not None else {})
        settings = Settings()
        reg = registry or PolicyRegistry.load(settings)
        adapters = build_adapters(settings, reg, client=rec.client(), sleep=lambda s: None)
        adapters["career_pages"].companies = companies if companies is not None else [
            CompanySource(name="Acme Analytics", ats="greenhouse", board_token="acmeanalytics"),
            CompanySource(name="Beta Labs", ats="lever", board_token="betalabs")]
        jobs, runs = JobsRepository(test_db), DiscoveryRunsRepository(test_db)
        engine = DiscoveryEngine(adapters, jobs, runs, reg, get_catalog(settings), load_scoring_config(settings).seniority.bands,
                                 now_fn=lambda: NOW)
        return DiscoveryService(jobs, runs, reg, adapters, engine, prefs or Preferences()), rec, engine
    return _make


GOOD_ROUTES = {f"{GH_BASE}/jobs": json_response(GREENHOUSE_LIST), LV_BASE: json_response(LEVER_LIST)}


@pytest.fixture
def jobs_api(make_env):
    service, rec, engine = make_env(GOOD_ROUTES)
    app.dependency_overrides[get_discovery_service] = lambda: service
    yield TestClient(app), service, rec
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------------- repository
def make_record(**kw):
    raw = RawJob(source=kw.pop("source", "company_career_page"), provider="greenhouse", source_job_id=kw.pop("sid", "1"),
                 url=kw.pop("url", "https://boards.greenhouse.io/acme/jobs/1"), title=kw.pop("title", "Data Scientist"),
                 company=kw.pop("company", "Acme"), location_text=kw.pop("loc", ["Pune, India"]),
                 description=kw.pop("desc", "Requirements:\n- Python and SQL.\n- 2+ years of experience.\nLocation: Pune"), **kw)
    return normalize(raw, get_catalog(), now=NOW)


def test_repository_insert_find_filter_sort(repos):
    jobs, _ = repos
    a = make_record(title="Data Scientist", company="Acme", sid="1", posted_raw="2026-11-01")
    b = make_record(title="ML Engineer", company="Beta Labs", sid="2", url="https://jobs.lever.co/beta/22222222-2222-3333-4444-555555555555",
                    loc=["Bengaluru, India"], posted_raw="2026-11-10", desc="Requirements:\n- PyTorch and Python.\n- 5+ years of experience.")
    for r in (a, b):
        doc, inserted = jobs.insert_discovered(r)
        assert inserted and doc["job_id"] == r.job_id
    assert jobs.get_job(a.job_id)["company_name"] == "Acme"
    assert {d["job_id"] for d in jobs.candidates_for(make_record(sid="1"))} >= {a.job_id}        # same source id
    assert {d["job_id"] for d in jobs.candidates_for(make_record(sid="x", company="Beta Labs", url="https://example.org/zzz"))} == {b.job_id}  # same company
    ids = lambda **f: [d["job_id"] for d in jobs.list_filtered(**f)]  # noqa: E731
    assert ids(title="data sci") == [a.job_id] and ids(company="beta") == [b.job_id] and ids(location="bengaluru") == [b.job_id]
    assert ids(source="company_career_page", sort_by="posted_date", descending=True) == [b.job_id, a.job_id]
    assert ids(sort_by="posted_date", descending=False) == [a.job_id, b.job_id]
    assert ids(sort_by="job_title", descending=False) == [a.job_id, b.job_id]
    assert ids(skill="pytorch") == [b.job_id] and ids(max_experience=3) == [a.job_id]
    assert ids(posted_after=datetime(2026, 11, 5, tzinfo=timezone.utc)) == [b.job_id]
    assert ids(status="closed") == [] and jobs.count_by_source() == {"company_career_page": 2}
    assert ids(title="(.*[") == []  # regex metacharacters are escaped


def test_identical_content_cannot_be_inserted_twice(repos):
    jobs, _ = repos
    r = make_record()
    jobs.insert_discovered(r)
    doc, inserted = jobs.insert_discovered(make_record())  # same content -> unique content_hash
    assert inserted is False and doc["job_id"] == r.job_id and jobs.count() == 1


def test_indexes_exist(test_db):
    info = test_db["jobs"].index_information()
    assert {"uq_job_id", "uq_job_content_hash", "ix_job_company_key", "ix_job_ref_url", "ix_job_ref_source_id", "ix_job_posted"} <= set(info)
    assert info["uq_job_id"].get("unique") and "uq_run_id" in test_db["discovery_runs"].index_information()


def test_runs_repository(repos):
    _, runs = repos
    assert runs.latest() is None and runs.running() is None
    run = runs.create({"titles": ["x"]}, ["career_pages"], {"linkedin": "manual"})
    assert runs.running()["run_id"] == run["run_id"] and runs.get(run["run_id"])["status"] == "running"
    runs.update(run["run_id"], {"status": "completed"})
    assert runs.running() is None and runs.latest()["status"] == "completed"
    old = runs.create({}, [], {})
    runs.update(old["run_id"], {"started_at": datetime.now(timezone.utc) - timedelta(hours=2)})
    assert runs.running() is None  # a crashed, stale "running" run does not block new runs


# ------------------------------------------------------------------------------------ engine
def test_engine_runs_permitted_sources_and_reports_stats(make_env):
    service, rec, engine = make_env(GOOD_ROUTES)
    run = engine.run(query_from_preferences(Preferences()), ["career_pages"])
    assert run["status"] == "completed"
    t = run["totals"]
    assert (t["fetched"], t["new"], t["duplicates"], t["filtered_out"], t["invalid"]) == (3, 3, 0, 0, 0)
    assert run["sources"]["career_pages"]["state"] == "done" and run["sources"]["career_pages"]["requests"] == 2
    stored = service.jobs.list_filtered()
    assert {j["job_title"] for j in stored} == {"Machine Learning Engineer", "Sales Manager", "Senior Data Scientist"}
    ml = next(j for j in stored if j["job_title"] == "Machine Learning Engineer")
    assert ml["source"] == "company_career_page" and ml["source_job_id"] == "101" and ml["status"] == "active"
    assert ml["job_url"] == "https://boards.greenhouse.io/acmeanalytics/jobs/101" and ml["salary_min"] == 2500000
    assert ml["source_refs"][0]["original"]["job_title"] == "Machine Learning Engineer (m/f/d)" and ml["discovered_at"] == NOW


def test_second_run_finds_duplicates_not_new_jobs(make_env):
    service, rec, engine = make_env(GOOD_ROUTES)
    q = query_from_preferences(Preferences())
    engine.run(q, ["career_pages"])
    second = engine.run(q, ["career_pages"])
    assert second["totals"]["new"] == 0 and second["totals"]["unchanged"] == 3 and second["totals"]["duplicates"] == 3
    assert service.jobs.count() == 3


def test_changed_vacancy_is_updated_in_place(make_env):
    service, rec, engine = make_env(GOOD_ROUTES)
    q = query_from_preferences(Preferences())
    engine.run(q, ["career_pages"])
    before = service.jobs.get_job(service.jobs.list_filtered(title="Machine Learning")[0]["job_id"])
    changed = {"jobs": [dict(GREENHOUSE_LIST["jobs"][0], content=GREENHOUSE_LIST["jobs"][0]["content"].replace("3+ years", "5+ years"))]}
    rec.routes[f"{GH_BASE}/jobs"] = json_response(changed)
    later = datetime(2026, 11, 20, tzinfo=timezone.utc)
    engine.now_fn = lambda: later
    run = engine.run(q, ["career_pages"])
    assert run["totals"]["updated"] == 1 and run["totals"]["new"] == 0
    after = service.jobs.get_job(before["job_id"])
    assert after["experience_min"] == 5 and after["last_updated_at"] == later.replace(tzinfo=None) or after["last_updated_at"] == later
    assert after["discovered_at"] == before["discovered_at"] and after["last_change"]["fields"]  # first-discovered time preserved
    assert service.jobs.count() == 3


def test_cross_platform_duplicate_is_merged_and_keeps_all_source_references(make_env):
    service, rec, engine = make_env(GOOD_ROUTES)
    engine.run(query_from_preferences(Preferences()), ["career_pages"])
    ml = service.jobs.list_filtered(title="Machine Learning")[0]
    adapter = engine.adapters["linkedin"]
    raw = adapter.import_manual("https://www.linkedin.com/jobs/view/3912345678?trackingId=z",
                                {"job_title": "Machine Learning Engineer", "company_name": "Acme Analytics", "location": "Pune, India",
                                 "job_description": service.jobs.get_job(ml["job_id"])["job_description"] + "\nApply on LinkedIn."})
    out = engine.ingest(raw, adapter, None, SourceStats(), manual=True)
    assert out["action"] == "merge" and out["job_id"] == ml["job_id"]
    merged = service.jobs.get_job(ml["job_id"])
    assert {r["source"] for r in merged["source_refs"]} == {"company_career_page", "linkedin"}
    assert merged["dedup"]["status"] == "merged" and merged["dedup"]["confidence"] > 0.9 and service.jobs.count() == 3
    # importing the same LinkedIn job again changes nothing
    again = engine.ingest(adapter.import_manual("https://linkedin.com/jobs/view/3912345678", {"job_title": "Machine Learning Engineer", "company_name": "Acme Analytics",
                          "location": "Pune, India", "job_description": merged["job_description"] + "\nApply on LinkedIn."}), adapter, None, SourceStats(), manual=True)
    assert again["action"] in ("unchanged", "update") and service.jobs.count() == 3


def test_uncertain_duplicate_is_flagged_for_review_and_not_merged(make_env):
    service, rec, engine = make_env(GOOD_ROUTES)
    engine.run(query_from_preferences(Preferences()), ["career_pages"])
    adapter = engine.adapters["manual"]
    raw = adapter.import_manual("https://careers.acme.example/job/77", {"job_title": "Machine Learning Engineer", "company_name": "Acme Analytics",
                                                                     "location": "Pune, India"})  # no description to compare
    out = engine.ingest(raw, adapter, None, SourceStats(), manual=True)
    assert out["action"] == "flag"
    flagged = service.jobs.get_job(out["job_id"])
    assert flagged["dedup"]["needs_review"] and flagged["dedup"]["status"] == "possible_duplicate" and flagged["dedup"]["matched_job_id"]
    assert service.jobs.count() == 4  # kept separate until a human decides


def test_policy_blocked_sources_are_never_called(make_env):
    service, rec, engine = make_env(GOOD_ROUTES)

    class Spy(JobAdapter):
        name = "spy"
        called = False

        def search(self, q): Spy.called = True; return AdapterResult(source="spy")
        def fetch_details(self, j): return j
        def to_raw(self, p, **c): return RawJob(source="spy")

    for status in ("DISABLED", "MANUAL_ONLY", "PENDING_POLICY_REVIEW"):
        engine.adapters["spy"] = Spy(Policy(name="spy", status=SourceStatus(status)))
        engine.registry = PolicyRegistry({"spy": {"discovery_status": status}, **engine.registry._data})
        runnable, skipped = engine.plan(["spy"])
        assert runnable == [] and "spy" in skipped
        run = engine.run(query_from_preferences(Preferences()), ["spy"])
        assert run["status"] == "completed" and not Spy.called and run["skipped_sources"]["spy"]
    runnable, skipped = engine.plan(["linkedin", "naukri", "indeed", "workday", "nonexistent", "career_pages"])
    assert runnable == ["career_pages"] and set(skipped) == {"linkedin", "naukri", "indeed", "workday", "nonexistent"}
    assert rec.requests == []  # planning is free of network access


def test_policy_enabled_but_no_authorised_method_is_still_refused(make_env):
    reg = PolicyRegistry({"linkedin": {"discovery_status": "ENABLED", "manual_import": True, "requests_per_minute": 60},
                          "career_pages": {"discovery_status": "ENABLED", "requests_per_minute": 20}})
    service, rec, engine = make_env(GOOD_ROUTES, registry=reg)
    runnable, skipped = engine.plan(["linkedin"])
    assert runnable == [] and "no authorised automated method" in skipped["linkedin"]
    assert rec.requests == []


def test_blocked_and_failing_sources_are_reported_not_hidden(make_env):
    service, rec, engine = make_env({f"{GH_BASE}/jobs": httpx.Response(429, text="slow down"), LV_BASE: json_response(LEVER_LIST)})
    run = engine.run(query_from_preferences(Preferences()), ["career_pages"])
    s = run["sources"]["career_pages"]
    assert run["status"] == "completed_with_errors" and s["blocked"] and "refused" in s["errors"][0] and s["new"] == 1
    assert sum("greenhouse" in u for u in rec.urls) == 1  # not retried


def test_adapter_crash_is_contained(make_env):
    service, rec, engine = make_env(GOOD_ROUTES)

    class Boom(JobAdapter):
        name = "boom"

        def search(self, q): raise RuntimeError("secret stack detail /srv/x")
        def fetch_details(self, j): return j
        def to_raw(self, p, **c): return RawJob(source="boom")

    engine.adapters["boom"] = Boom(Policy(name="boom", status=SourceStatus.ENABLED))
    engine.registry = PolicyRegistry({"boom": {"discovery_status": "ENABLED"}})
    run = engine.run(query_from_preferences(Preferences()), ["boom"])
    assert run["status"] == "completed_with_errors" and run["sources"]["boom"]["state"] == "error"
    assert "secret" not in str(run["sources"]["boom"]["errors"])


def test_rate_limit_is_enforced_during_a_run(test_db):
    sleeps = []
    rec = Recorder({f"{GH_BASE}/jobs": json_response(GREENHOUSE_LIST), LV_BASE: json_response(LEVER_LIST)})
    settings, reg = Settings(), PolicyRegistry.load(Settings())
    adapters = build_adapters(settings, reg, client=rec.client(), sleep=sleeps.append, clock=lambda: 0.0)
    adapters["career_pages"].companies = [CompanySource(name="Acme Analytics", ats="greenhouse", board_token="acmeanalytics"),
                                          CompanySource(name="Acme Two", ats="greenhouse", board_token="acmeanalytics")]
    engine = DiscoveryEngine(adapters, JobsRepository(test_db), DiscoveryRunsRepository(test_db), reg, get_catalog(), now_fn=lambda: NOW)
    engine.run(query_from_preferences(Preferences()), ["career_pages"])
    assert len(rec.requests) == 2 and sleeps == [2.0]  # 30 requests/min for greenhouse => a 2 s gap between the two calls


# ------------------------------------------------------------------------------------ filters
def test_preferences_become_query_and_unknown_does_not_exclude():
    q = query_from_preferences(Preferences(titles=["ML Engineer"], remote=True, hybrid=None, onsite=False, min_salary=2000000,
                                           salary_currency="INR", experience_years_max=7, posted_within_days=30))
    assert q.work_modes == ["hybrid", "remote"] and q.titles == ["ML Engineer"] and q.min_salary == 2000000
    assert query_from_preferences(Preferences(remote=True)).work_modes == []  # only an explicit NO excludes a mode
    assert query_from_preferences(Preferences(titles=["a"]), {"titles": ["b"]}).titles == ["b"]  # explicit request overrides


@pytest.mark.parametrize("query,expected,reason", [
    (dict(titles=["machine learning"]), True, ""), (dict(titles=["sales"]), False, "title"),
    (dict(skills=["PyTorch"]), True, ""), (dict(skills=["Kafka"]), False, "skills"),
    (dict(experience_max=2), False, "experience"), (dict(experience_max=5), True, ""),
    (dict(locations=["Pune"]), True, ""), (dict(locations=["Chennai"]), False, "location"),
    (dict(work_modes=["remote"]), False, "work_mode"), (dict(work_modes=["hybrid", "remote"]), True, ""),
    (dict(employment_types=["contract"]), False, "employment_type"), (dict(employment_types=["Full-Time"]), True, ""),
    (dict(min_salary=4000000, salary_currency="INR"), False, "salary"), (dict(min_salary=2000000, salary_currency="INR"), True, ""),
    (dict(min_salary=40000000, salary_currency="USD"), True, ""),               # different currency: cannot compare -> keep
    (dict(posted_within_days=5), False, "date_posted"), (dict(posted_within_days=30), True, ""),
    (dict(companies=["Acme Analytics Inc"]), True, ""), (dict(companies=["Other Co"]), False, "company"),
    (dict(keywords_exclude=["rag"]), False, "excluded_keyword"), (dict(keywords_include=["langchain"]), True, ""),
])
def test_filters(query, expected, reason):
    ml = normalize(RawJob(source="company_career_page", provider="greenhouse", source_job_id="101", title="Machine Learning Engineer",
                          company="Acme Analytics", url="https://boards.greenhouse.io/a/jobs/101", location_text=["Pune, India"],
                          description=GREENHOUSE_PLAIN, posted_raw="2026-11-01"), get_catalog(), now=NOW)
    keep, why = matches_query(ml, SearchQuery(**query), get_catalog(), NOW)
    assert keep is expected and why == reason


GREENHOUSE_PLAIN = ("Acme Analytics is hiring a Machine Learning Engineer.\nResponsibilities:\n- Build RAG pipelines with LangChain.\n"
                    "Requirements:\n- 3+ years of experience in machine learning.\n- Strong Python and SQL skills.\n- Experience with PyTorch or TensorFlow.\n"
                    "Nice to have:\n- Docker.\nLocation: Pune, India (Hybrid). Salary: INR 25-35 LPA. Full-time.")


def test_unknown_fields_never_exclude_a_job():
    bare = normalize(RawJob(source="manual", title="Machine Learning Engineer", url="https://example.org/j"), get_catalog(), now=NOW)
    q = SearchQuery(skills=["Python"], locations=["Pune"], work_modes=["remote"], employment_types=["full-time"], min_salary=9e9,
                    posted_within_days=1, experience_max=1, titles=["machine learning"])
    assert matches_query(bare, q, get_catalog(), NOW) == (True, "")
    untitled = normalize(RawJob(source="manual", url="https://example.org/j"), get_catalog(), now=NOW)
    assert matches_query(untitled, SearchQuery(titles=["x"]), get_catalog(), NOW) == (False, "title")  # but no title cannot match a title filter


# ------------------------------------------------------------------------------------ API
def test_sources_endpoint_shows_policy_status(jobs_api):
    client, *_ = jobs_api
    rows = {r["source"]: r for r in client.get("/api/jobs/sources").json()}
    assert rows["linkedin"]["status"] == "MANUAL_ONLY" and rows["linkedin"]["automated_discovery"] is False and rows["linkedin"]["manual_import"] is True
    assert rows["naukri"]["status"] == "PENDING_POLICY_REVIEW" and "policy" in rows["naukri"]["reason"].lower()
    assert rows["indeed"]["automated_discovery"] is False and rows["career_pages"]["automated_discovery"] is True
    assert rows["career_pages"]["companies_configured"] == 2 and rows["career_pages"]["providers"]["workday"]["status"] == "PENDING_POLICY_REVIEW"
    assert all("credential" not in str(r).lower() and "password" not in str(r).lower() for r in rows.values())


def test_discover_status_list_detail(jobs_api):
    client, service, rec = jobs_api
    r = client.post("/api/jobs/discover", json={"sources": ["career_pages", "linkedin", "naukri"]})
    assert r.status_code == 202
    body = r.json()
    assert body["runnable_sources"] == ["career_pages"] and set(body["skipped_sources"]) == {"linkedin", "naukri"}
    st = client.get("/api/jobs/discovery/status", params={"run_id": body["run_id"]}).json()  # background task already ran
    assert st["status"] == "completed" and st["totals"]["new"] == 3 and st["skipped_sources"]["linkedin"]
    assert client.get("/api/jobs/discovery/status").json()["run_id"] == body["run_id"]
    jobs = client.get("/api/jobs").json()
    assert len(jobs) == 3 and all(j["job_url"].startswith("https://") and j["status"] == "active" for j in jobs)
    ml = next(j for j in jobs if j["job_title"] == "Machine Learning Engineer")
    assert ml["sources"][0]["provider"] == "greenhouse" and ml["location"] == ["Pune, India"] and "Machine Learning" in ml["mandatory_skills"]
    detail = client.get(f"/api/jobs/{ml['job_id']}").json()
    assert "Design and deploy" in detail["job_description"] and detail["source_originals"][0]["original"]["board_token"] == "acmeanalytics"
    assert not any(u.startswith("https://www.linkedin") for u in rec.urls)


def test_discover_filters_and_preferences(make_env):
    service, rec, engine = make_env(GOOD_ROUTES, prefs=Preferences(titles=["machine learning"], locations=["Pune"]))
    app.dependency_overrides[get_discovery_service] = lambda: service
    try:
        c = TestClient(app)
        c.post("/api/jobs/discover", json={"sources": ["career_pages"]})
        assert [j["job_title"] for j in c.get("/api/jobs").json()] == ["Machine Learning Engineer"]
        stats = c.get("/api/jobs/discovery/status").json()["totals"]
        assert stats["filtered_out"] == 2 and stats["new"] == 1
        # explicit request overrides preferences; use_preferences=false ignores them
        c.post("/api/jobs/discover", json={"sources": ["career_pages"], "use_preferences": False, "titles": ["sales"]})
        assert {j["job_title"] for j in c.get("/api/jobs").json()} == {"Machine Learning Engineer", "Sales Manager"}
    finally:
        app.dependency_overrides.clear()


def test_nothing_to_run_busy_and_validation(jobs_api, repos):
    client, service, _ = jobs_api
    r = client.post("/api/jobs/discover", json={"sources": ["linkedin", "indeed"]})
    assert r.status_code == 200 and r.json()["status"] == "nothing_to_run" and not r.json()["runnable_sources"]
    assert client.get("/api/jobs/discovery/status").json() == {"status": "never_run"}
    service.runs.create({}, ["career_pages"], {})  # a run in progress
    assert client.post("/api/jobs/discover", json={"sources": ["career_pages"]}).status_code == 409
    for bad in ({"work_modes": ["office"]}, {"min_salary": -1}, {"max_jobs": 0}, {"posted_within_days": 0}, {"titles": "x"}):
        assert client.post("/api/jobs/discover", json=bad).status_code == 422
    assert client.get("/api/jobs/discovery/status", params={"run_id": "nope"}).status_code == 422


def test_list_sort_filter_params(jobs_api):
    client, *_ = jobs_api
    client.post("/api/jobs/discover", json={"sources": ["career_pages"]})
    titles = lambda **p: [j["job_title"] for j in client.get("/api/jobs", params=p).json()]  # noqa: E731
    assert titles(sort_by="job_title", descending="false") == ["Machine Learning Engineer", "Sales Manager", "Senior Data Scientist"]
    assert titles(company="beta") == ["Senior Data Scientist"] and titles(source="company_career_page", limit=2).__len__() == 2
    assert titles(location="mumbai") == ["Sales Manager"]
    assert sorted(titles(work_mode="hybrid")) == ["Machine Learning Engineer", "Senior Data Scientist"]
    assert client.get("/api/jobs", params={"sort_by": "bogus"}).status_code == 422
    assert client.get("/api/jobs", params={"limit": 500}).status_code == 422 and client.get("/api/jobs", params={"work_mode": "x"}).status_code == 422


def test_unknown_and_malformed_job_ids(jobs_api):
    client, *_ = jobs_api
    assert client.get("/api/jobs/" + "a" * 32).status_code == 404
    assert client.get("/api/jobs/not-an-id").status_code == 422
    assert client.post("/api/jobs/" + "a" * 32 + "/refresh").status_code == 404


def test_import_manual_linkedin_makes_no_request(jobs_api):
    client, service, rec = jobs_api
    body = {"url": "https://www.linkedin.com/jobs/view/3912345678/?trackingId=abc",
            "job": {"job_title": "AI Engineer", "company_name": "Zeta Corp", "location": "Pune, India",
                    "job_description": "Requirements:\n- Python and PyTorch.\n- 3+ years of experience.", "employment_type": "Full-time"}}
    r = client.post("/api/jobs/import", json=body)
    assert r.status_code == 201 and rec.requests == []
    j = r.json()["job"]
    assert j["source"] == "linkedin" and j["source_job_id"] == "3912345678" and j["job_url"] == "https://linkedin.com/jobs/view/3912345678"
    assert j["job_title"] == "AI Engineer" and "Python" in j["mandatory_skills"] and j["employment_type"] == "full-time" and j["status"] == "active"
    again = client.post("/api/jobs/import", json=body)
    assert again.status_code == 200 and again.json()["action"] in ("unchanged", "update") and len(client.get("/api/jobs").json()) == 1


def test_linkedin_table_and_suggestions(jobs_api):
    client, service, rec = jobs_api
    s = client.get("/api/jobs/linkedin/suggestions")
    assert s.status_code == 200 and s.json()["profile_available"] is False and s.json()["titles"] == []   # no profile: no invented defaults
    assert client.get("/api/jobs/linkedin/table").json() == {"total": 0, "items": []}
    body = {"url": "https://www.linkedin.com/jobs/view/3912345678/", "job": {"job_title": "AI Engineer", "company_name": "Zeta Corp", "location": "Pune, India",
            "job_description": "Requirements:\n- Python and PyTorch.\n- 3+ years of experience."}}
    assert client.post("/api/jobs/import", json=body).status_code == 201
    client.post("/api/jobs/import", json=body)                                          # duplicate: still one row
    t = client.get("/api/jobs/linkedin/table").json()
    assert t["total"] == 1 and rec.requests == []
    row = t["items"][0]
    assert row["job_title"] == "AI Engineer" and row["company_name"] == "Zeta Corp" and row["match_score"] is None and row["eligibility"] is None
    assert row["application_status"] == "NOT_STARTED" and row["job_url"].startswith("https://") and row["experience_min"] == 3


@pytest.mark.parametrize("body,status", [
    ({}, 422), ({"url": "https://www.linkedin.com/jobs/view/1"}, 201),  # URL only: a minimal record, details stay unknown
    ({"url": "https://evil.example/jobs/view/3912345678", "job": {"job_title": "x"}}, 201),      # unknown host => stored as 'manual', never fetched
    ({"url": "javascript:alert(1)", "job": {"job_title": "x"}}, 422), ({"url": "http://127.0.0.1/x", "job": {"job_title": "x"}}, 422),
    ({"job": {"job_title": "x" * 500}}, 422), ({"job": {"work_mode": "office"}}, 422),
])
def test_import_validation(jobs_api, body, status):
    client, _, rec = jobs_api
    assert client.post("/api/jobs/import", json=body).status_code == status
    assert rec.requests == []


def test_import_greenhouse_url_uses_the_public_api_only_when_enabled(make_env):
    detail = {"id": 101, "title": "ML Engineer", "absolute_url": "https://boards.greenhouse.io/acmeanalytics/jobs/101", "company_name": "Acme Analytics",
              "location": {"name": "Pune, India"}, "content": "&lt;p&gt;Requirements: Python. 3+ years of experience.&lt;/p&gt;", "updated_at": "2026-11-10T00:00:00Z"}
    service, rec, _ = make_env({f"{GH_BASE}/jobs/101": json_response(detail)})
    app.dependency_overrides[get_discovery_service] = lambda: service
    try:
        c = TestClient(app)
        r = c.post("/api/jobs/import", json={"url": "https://job-boards.greenhouse.io/acmeanalytics/jobs/101?gh_src=x"})
        assert r.status_code == 201 and r.json()["job"]["company_name"] == "Acme Analytics" and rec.urls == [f"{GH_BASE}/jobs/101"]
    finally:
        app.dependency_overrides.clear()
    blocked_reg = PolicyRegistry({"career_pages": {"discovery_status": "ENABLED", "manual_import": True,
                                                   "providers": {"greenhouse": {"discovery_status": "PENDING_POLICY_REVIEW"}}},
                                  "manual": {"discovery_status": "MANUAL_ONLY", "manual_import": True}})
    service2, rec2, _ = make_env({f"{GH_BASE}/jobs/101": json_response(detail)}, registry=blocked_reg)
    app.dependency_overrides[get_discovery_service] = lambda: service2
    try:
        r = TestClient(app).post("/api/jobs/import", json={"url": "https://boards.greenhouse.io/acmeanalytics/jobs/101"})
        assert r.status_code == 403 and "policy" in r.json()["detail"].lower() and rec2.requests == []
    finally:
        app.dependency_overrides.clear()


def test_refresh_updates_closes_and_refuses_manual_sources(make_env):
    service, rec, engine = make_env(GOOD_ROUTES)
    app.dependency_overrides[get_discovery_service] = lambda: service
    try:
        c = TestClient(app)
        c.post("/api/jobs/discover", json={"sources": ["career_pages"]})
        ml = next(j for j in c.get("/api/jobs").json() if j["job_title"] == "Machine Learning Engineer")
        newer = dict(GREENHOUSE_LIST["jobs"][0], content=GREENHOUSE_LIST["jobs"][0]["content"].replace("3+ years", "6+ years"))
        rec.routes[f"{GH_BASE}/jobs/101"] = json_response(newer)
        r = c.post(f"/api/jobs/{ml['job_id']}/refresh")
        assert r.status_code == 200 and r.json()["action"] == "update" and r.json()["job"]["experience_min"] == 6
        rec.routes[f"{GH_BASE}/jobs/101"] = httpx.Response(404)
        gone = c.post(f"/api/jobs/{ml['job_id']}/refresh")
        assert gone.json()["action"] == "closed" and c.get(f"/api/jobs/{ml['job_id']}").json()["status"] == "closed"
        manual = c.post("/api/jobs/import", json={"url": "https://www.linkedin.com/jobs/view/3912345678", "job": {"job_title": "AI Eng", "company_name": "Zeta"}}).json()["job"]
        n = len(rec.requests)
        r = c.post(f"/api/jobs/{manual['job_id']}/refresh")
        assert r.status_code == 409 and "manual" in r.json()["detail"].lower() and len(rec.requests) == n
    finally:
        app.dependency_overrides.clear()


def test_discovered_jobs_can_be_scored_by_phase_3(jobs_api, seeded, make_matching_service):
    """Phase 4 jobs plug into the Phase 3 matcher through their job_id (reuses the same `jobs` collection)."""
    client, *_ = jobs_api
    client.post("/api/jobs/discover", json={"sources": ["career_pages"]})
    ml = next(j for j in client.get("/api/jobs").json() if j["job_title"] == "Machine Learning Engineer")
    svc = make_matching_service()
    report, created = svc.score(job_id=ml["job_id"])
    assert created and report.job_id == ml["job_id"] and 0 <= report.match_score <= 100 and report.eligibility.status in ("PASS", "FAIL", "UNKNOWN")
    assert report.job_title == "Machine Learning Engineer"


def test_import_scores_the_job_straight_away_when_a_reviewed_profile_exists(jobs_api, seeded):
    client, service, rec = jobs_api
    body = {"url": "https://www.linkedin.com/jobs/view/3912345679/", "job": {"job_title": "Data Scientist", "company_name": "Zeta Corp", "location": "Pune, India",
            "job_description": "Requirements:\n- Python and SQL.\n- 2+ years of experience building machine learning models."}}
    r = client.post("/api/jobs/import", json=body)
    assert r.status_code == 201 and rec.requests == []
    m = r.json()["match"]
    assert m and 0 <= m["match_score"] <= 100 and m["eligibility"] in ("PASS", "FAIL", "UNKNOWN")
    row = client.get("/api/jobs/linkedin/table").json()["items"][0]
    assert row["match_score"] == m["match_score"] and row["eligibility"] == m["eligibility"]           # visible in the LinkedIn table without another step


def test_import_without_a_profile_still_succeeds_unscored(jobs_api):
    client, *_ = jobs_api
    r = client.post("/api/jobs/import", json={"url": "https://www.linkedin.com/jobs/view/3912345680/", "job": {"job_title": "Data Scientist", "company_name": "Zeta",
                    "job_description": "Requirements:\n- Python and SQL.\n- 2+ years of experience."}})
    assert r.status_code == 201 and r.json()["match"] is None


def test_pasted_naukri_job_without_a_link_keeps_naukri_as_its_source_and_is_not_added_twice(jobs_api):
    client, service, rec = jobs_api
    body = {"source": "naukri", "job": {"job_title": "AI Engineer", "company_name": "Acme Analytics", "location": "Pune", "experience_requirement": "5-10 Yrs",
            "posted_date": "2026-11-21", "job_description": "AI Engineer\nAcme Analytics\nExperience: 5-10 Yrs\nBuild LLM apps with Python and PyTorch."}}
    r = client.post("/api/jobs/import", json=body)
    assert r.status_code == 201 and rec.requests == []                                               # nothing is fetched from Naukri
    j = r.json()["job"]
    assert j["source"] == "naukri" and j["job_url"] is None and j["experience_min"] == 5
    again = client.post("/api/jobs/import", json=body)
    assert again.status_code == 200 and again.json()["action"] != "insert"                            # the same pasted job is not stored twice
    assert client.get("/api/jobs", params={"source": "naukri"}).json()[0]["job_title"] == "AI Engineer"


def test_source_hint_is_ignored_when_a_link_is_given_and_rejects_unknown_sites(jobs_api):
    client, *_ = jobs_api
    r = client.post("/api/jobs/import", json={"url": "https://www.linkedin.com/jobs/view/3912345690/", "source": "naukri",
                                              "job": {"job_title": "ML Engineer", "company_name": "Zeta", "job_description": "Requirements:\n- Python and SQL.\n- 2+ years."}})
    assert r.status_code == 201 and r.json()["job"]["source"] == "linkedin"                         # the link decides; the hint only applies without one
    assert client.post("/api/jobs/import", json={"source": "google", "job": {"job_title": "x", "company_name": "y"}}).status_code == 422


def test_jobs_page_can_show_only_jobs_that_fit_the_preferences(make_env):
    from config.scoring_config import Preferences
    service, rec, engine = make_env(GOOD_ROUTES, prefs=Preferences(titles=["data scientist"]))
    app.dependency_overrides[get_discovery_service] = lambda: service
    try:
        client = TestClient(app)
        assert client.post("/api/jobs/discover", json={"use_preferences": False}).status_code == 202       # stores every listed job (no title filter)
        all_jobs = client.get("/api/jobs/page", params={"limit": 50}).json()
        fit = client.get("/api/jobs/page", params={"limit": 50, "preferred_only": "true"}).json()
        assert all_jobs["total"] > fit["total"] >= 1
        assert all("data scientist" in j["job_title"].lower() for j in fit["items"])
        imported = client.post("/api/jobs/import", json={"job": {"job_title": "Barista", "company_name": "Cafe", "job_description": "Make coffee every day for our guests."}})
        assert imported.status_code == 201
        assert client.get("/api/jobs/page", params={"limit": 50, "preferred_only": "true"}).json()["total"] == fit["total"] + 1   # a job you imported yourself always counts
    finally:
        app.dependency_overrides.clear()


def test_companies_late_in_the_list_are_still_read_when_early_boards_are_large(make_env):
    """Regression: the per-run job cap used to stop reading companies once 300 jobs (from ANY location) had been listed."""
    from config.scoring_config import Preferences
    from integrations.career_pages.adapter import CompanySource
    big = {"jobs": [{"id": 1000 + i, "title": "Data Scientist", "absolute_url": f"https://boards.greenhouse.io/bigco/jobs/{1000 + i}", "location": {"name": "Austin, TX"},
                     "updated_at": "2026-11-10T00:00:00Z", "content": "Requirements: Python and SQL. 2+ years of experience."} for i in range(6)]}
    late = {"jobs": [{"id": 7, "title": "Data Scientist", "absolute_url": "https://boards.greenhouse.io/lateco/jobs/7", "location": {"name": "Pune, India"},
                      "updated_at": "2026-11-10T00:00:00Z", "content": "Requirements: Python and SQL. 2+ years of experience."}]}
    routes = {"https://boards-api.greenhouse.io/v1/boards/bigco/jobs": json_response(big), "https://boards-api.greenhouse.io/v1/boards/lateco/jobs": json_response(late)}
    service, rec, engine = make_env(routes, companies=[CompanySource(name="BigCo", ats="greenhouse", board_token="bigco"), CompanySource(name="LateCo", ats="greenhouse", board_token="lateco")],
                                    prefs=Preferences(titles=["data scientist"], locations=["India"]))
    engine.registry.get("career_pages").max_jobs_per_run = 3                    # smaller than BigCo's board
    run, _, query, runnable = service.start(None, ["career_pages"], True)
    service.execute(run["run_id"], query, runnable)
    stored = [d["company_name"] for d in service.jobs._jobs.find({}, {"company_name": 1})]
    assert stored == ["LateCo"]                                                  # BigCo's 6 US jobs no longer crowd out the India job listed later


def test_linkedin_bulk_paste_job_without_description_is_stored_unscored_with_its_link_and_details(jobs_api, seeded):
    # What the LinkedIn bulk paste sends for a list-only job: no description, an approximate date, a work mode, a salary text.
    client, service, rec = jobs_api
    body = {"url": "https://www.linkedin.com/jobs/view/4300000014/", "job": {"job_title": "Data Scientist ML/Gen AI Engineer", "company_name": "Irth", "location": "India",
            "work_mode": "remote", "posted_date": "2026-10-25", "salary": "$45K/yr - $45K/yr"}}
    r = client.post("/api/jobs/import", json=body)
    assert r.status_code == 201 and rec.requests == [] and r.json()["match"] is None                 # nothing fetched; nothing to score yet
    j = r.json()["job"]
    assert j["source"] == "linkedin" and j["source_job_id"] == "4300000014" and j["work_mode"] == "remote" and j["location"] == ["India"]
    assert j["posted_date"].startswith("2026-10-25")
    # later the candidate pastes the description into the quick import: same job (no duplicate), and now it is scored
    body["job"]["job_description"] = "Requirements:\n- Python, PyTorch and LLMs.\n- 3+ years of experience in machine learning."
    again = client.post("/api/jobs/import", json=body)
    assert again.status_code == 200 and again.json()["action"] in ("update", "unchanged") and again.json()["match"] is not None
    assert len(client.get("/api/jobs", params={"source": "linkedin"}).json()) == 1
    no_link = client.post("/api/jobs/import", json={"source": "linkedin", "job": {"job_title": "AI Engineer", "company_name": "Jeeves", "location": "India", "work_mode": "remote"}})
    assert no_link.status_code == 201 and no_link.json()["job"]["source"] == "linkedin" and no_link.json()["job"]["job_url"] is None


def test_naukri_job_with_its_link_is_stored_as_naukri_with_the_clean_link_and_fetches_nothing(jobs_api):
    client, service, rec = jobs_api
    url = "https://www.naukri.com/job-listings-ai-engineer-acme-pune-5-to-10-years-211125012345"
    body = {"url": url, "job": {"job_title": "AI Engineer", "company_name": "Acme Analytics", "location": "Pune", "experience_requirement": "5-10 Yrs",
            "job_description": "AI Engineer\nAcme Analytics\nBuild LLM apps with Python and PyTorch."}}
    r = client.post("/api/jobs/import", json=body)
    assert r.status_code == 201 and rec.requests == []                                               # the link is stored, never opened
    j = r.json()["job"]
    assert j["source"] == "naukri" and j["source_job_id"] == "211125012345" and "naukri.com/job-listings-ai-engineer" in j["job_url"]
    assert client.post("/api/jobs/import", json=body).json()["action"] != "insert"


def test_pasting_a_description_into_a_job_from_an_alert_e_mail_scores_that_same_job_and_fetches_nothing(jobs_api, seeded):
    client, service, rec = jobs_api
    # jobs as alert e-mails store them: link, title, company, place - no description
    for url, title in (("https://www.instahyre.com/job-448472-associate-architect-ml-at-quantiphi-analytics-bangalore-mumbai/", "Associate Architect - ML"),
                       ("https://www.glassdoor.co.in/Job/jobs.htm?sc.keyword=AI+Engineer+Acme+Robotics", "AI Engineer")):
        r = client.post("/api/jobs/import", json={"url": url, "job": {"job_title": title, "company_name": "Acme Robotics", "location": "Bangalore"}})
        assert r.status_code == 201 and r.json()["match"] is None
        jid = r.json()["job"]["job_id"]
        text = ("About the role: build and deploy machine learning systems.\nRequirements:\n- 3+ years of experience in machine learning.\n"
                "- Python, PyTorch, LLMs and MLOps on AWS.\n- Experience with retrieval-augmented generation.\n") * 2
        done = client.post(f"/api/jobs/{jid}/description", json={"job_description": text})
        assert done.status_code == 200, done.text
        assert done.json()["job"]["job_id"] == jid and "PyTorch" in done.json()["job"]["job_description"]   # the SAME job, now with its description
        assert done.json()["match"] is not None and done.json()["match"]["match_score"] is not None          # and scored right away
        assert client.post(f"/api/jobs/{jid}/description", json={"job_description": text}).status_code == 409   # never replaces a description
    assert rec.requests == []                                                                             # nothing was fetched from any site
    assert client.post(f"/api/jobs/{jid}/description", json={"job_description": "too short"}).status_code == 422
    assert client.post(f"/api/jobs/{'f' * 32}/description", json={"job_description": text}).status_code == 404
    assert len(client.get("/api/jobs").json()) == 2                                                       # no duplicate jobs were created


def test_one_unreadable_job_record_does_not_break_the_job_list(jobs_api, test_db):
    client, service, rec = jobs_api
    ok = client.post("/api/jobs/import", json={"job": {"job_title": "AI Engineer", "company_name": "Acme", "location": "Pune",
                                                       "job_description": "Build LLM apps with Python and PyTorch for enterprise search."}})
    assert ok.status_code == 201
    test_db["jobs"].insert_one({"job_id": "e" * 32, "content_hash": "broken", "company_key": None, "job_title": "Broken"})   # e.g. a bad manual edit
    r = client.get("/api/jobs/page", params={"limit": 50})
    assert r.status_code == 200 and [j["job_title"] for j in r.json()["items"]] == ["AI Engineer"]
    assert client.get("/api/jobs").status_code == 200
