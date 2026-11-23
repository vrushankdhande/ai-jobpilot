"""Matching API + service + repositories on a real throw-away MongoDB (skipped if unreachable)."""
import json

from datetime import datetime

import pytest

from ai.schemas.candidate_profile import ProfileContent
from backend.services.matching_service import MatchingService
from database.repositories.resumes_repository import ResumesRepository
from tests.fixtures.fake_ai import make_client
from tests.fixtures.jd_dataset import JDS

pytestmark = pytest.mark.integration

JD = {"job_description": JDS[3]["text"], "job_title": "Data Scientist", "company_name": "Insight Metrics", "location": "Pune, India"}


def score(api, job=JD, **extra):
    return api.post("/api/matching/score", json={"job": job, **extra})


def test_thresholds_endpoint(matching_api):
    r = matching_api.get("/api/matching/thresholds").json()
    assert r["thresholds"] == {"excellent": 90.0, "strong": 80.0, "good": 75.0, "moderate": 60.0}
    assert r["recommendation_threshold"] == 75 and r["scoring_version"] and sum(r["weights"].values()) == 100
    assert r["ai"]["state"] in {"disabled", "no_api_key", "no_consent", "enabled"}


def test_analyze_jd_and_cache(matching_api, test_db):
    first = matching_api.post("/api/matching/analyze-jd", json=JD).json()
    assert first["cached"] is False and first["analysis"]["parser"] == "deterministic"
    assert set(first["analysis"]["mandatory_skills"]) == {"Python", "SQL", "Scikit-learn", "Machine Learning"}
    second = matching_api.post("/api/matching/analyze-jd", json=JD).json()
    assert second["cached"] is True and second["job_id"] == first["job_id"]
    assert test_db["jobs"].count_documents({}) == 1  # the JD text is stored once


def test_score_returns_validated_report_and_persists(matching_api, test_db, seeded):
    r = score(matching_api)
    assert r.status_code == 200 and r.json()["created"] is True
    rep = r.json()["report"]
    assert 0 <= rep["match_score"] <= 100 and rep["eligibility"]["status"] == "PASS"
    assert rep["profile_id"] == seeded.profile_id and rep["scoring_version"] and rep["jd_parser"] == "deterministic"
    assert rep["ai_used"] is False and rep["ai_summary"] is None
    stored = test_db["job_matches"].find_one({"match_id": rep["match_id"]})
    assert stored and stored["eligibility_status"] == "PASS" and "jd_text" not in stored  # no duplicate JD text
    got = matching_api.get(f"/api/matching/{rep['match_id']}")
    assert got.status_code == 200 and got.json()["match_score"] == rep["match_score"]


def test_identical_inputs_do_not_create_duplicates_but_force_does(matching_api, test_db):
    a = score(matching_api).json()
    b = score(matching_api).json()
    assert b["created"] is False and b["report"]["match_id"] == a["report"]["match_id"]
    assert test_db["job_matches"].count_documents({}) == 1
    c = score(matching_api, force=True).json()
    assert c["created"] is True and test_db["job_matches"].count_documents({}) == 2


def test_unreviewed_profile_requires_confirmation(matching_api, test_db, seeded):
    repo = ResumesRepository(test_db)
    p = repo.get_profile(seeded.resume_id)
    p.review_status = "pending_review"
    repo.save_profile(p)
    r = score(matching_api)
    assert r.status_code == 409 and "not been reviewed" in r.json()["detail"]
    assert test_db["job_matches"].count_documents({}) == 0
    ok = score(matching_api, allow_unreviewed=True)
    assert ok.status_code == 200
    assert any("not been marked as reviewed" in l for l in ok.json()["report"]["limitations"])


def test_no_active_resume_and_missing_profile(matching_service, test_db):
    from fastapi.testclient import TestClient
    from backend.api.routes.matching import get_matching_service
    from backend.main import app
    app.dependency_overrides[get_matching_service] = lambda: matching_service
    try:
        c = TestClient(app)
        r = c.post("/api/matching/score", json={"job": JD})
        assert r.status_code == 409 and "No active resume" in r.json()["detail"]
        r = c.post("/api/matching/score", json={"job": JD, "resume_id": "does-not-exist"})
        assert r.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_score_by_job_id_and_validation(matching_api):
    job_id = matching_api.post("/api/matching/analyze-jd", json=JD).json()["job_id"]
    assert matching_api.post("/api/matching/score", json={"job_id": job_id}).status_code == 200
    assert matching_api.post("/api/matching/score", json={"job_id": "f" * 32}).status_code == 404
    assert matching_api.post("/api/matching/score", json={}).status_code == 422                       # neither
    assert matching_api.post("/api/matching/score", json={"job": JD, "job_id": job_id}).status_code == 422  # both
    assert matching_api.post("/api/matching/score", json={"job_id": "not-an-id"}).status_code == 422


@pytest.mark.parametrize("job", [{"job_description": ""}, {"job_description": "x"}, {"job_description": "ok text here", "job_url": "javascript:1"}])
def test_invalid_job_input_422(matching_api, job):
    assert matching_api.post("/api/matching/score", json={"job": job}).status_code == 422
    assert matching_api.post("/api/matching/analyze-jd", json=job).status_code == 422


def test_bulk_score(matching_api, test_db):
    jobs = [{"job_description": j["text"], "job_title": j["title"], "company_name": j["company"], "location": j.get("location")}
            for j in JDS[:6]]
    r = matching_api.post("/api/matching/bulk-score", json={"jobs": jobs})
    assert r.status_code == 200
    body = r.json()
    assert body["succeeded"] == 6 and body["failed"] == 0 and [i["index"] for i in body["results"]] == list(range(6))
    assert test_db["job_matches"].count_documents({}) == 6 and test_db["jobs"].count_documents({}) == 6
    again = matching_api.post("/api/matching/bulk-score", json={"jobs": jobs}).json()
    assert all(i["created"] is False for i in again["results"])
    assert matching_api.post("/api/matching/bulk-score", json={"jobs": []}).status_code == 422
    assert matching_api.post("/api/matching/bulk-score", json={"jobs": jobs * 5}).status_code == 422  # > 25


def test_bulk_item_failure_does_not_abort_batch(matching_service, seeded, monkeypatch):
    real = matching_service._score
    calls = {"n": 0}

    def flaky(profile, doc, use_ai, force):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("boom with secret internals")
        return real(profile, doc, use_ai, force)

    monkeypatch.setattr(matching_service, "_score", flaky)
    from ai.schemas.job_schema import JobInput
    out = matching_service.bulk_score([JobInput(job_description=j["text"], job_title=j["title"]) for j in JDS[:3]])
    assert [o["status"] for o in out] == ["ok", "error", "ok"]
    assert "secret" not in out[1]["error"]


def test_results_listing_and_filters(matching_api):
    for j in JDS[:10]:
        matching_api.post("/api/matching/score", json={"job": {"job_description": j["text"], "job_title": j["title"],
                                                               "company_name": j["company"], "location": j.get("location")}})
    allr = matching_api.get("/api/matching/results").json()
    assert len(allr) == 10 and allr == sorted(allr, key=lambda r: r["created_at"], reverse=True)
    passing = matching_api.get("/api/matching/results", params={"eligibility": "PASS"}).json()
    failing = matching_api.get("/api/matching/results", params={"eligibility": "FAIL"}).json()
    assert len(passing) + len(failing) <= 10 and all(r["eligibility_status"] == "PASS" for r in passing) and failing
    hi = matching_api.get("/api/matching/results", params={"min_score": 70}).json()
    assert all(r["match_score"] >= 70 for r in hi)
    lo = matching_api.get("/api/matching/results", params={"max_score": 70}).json()
    assert all(r["match_score"] <= 70 for r in lo) and len(hi) + len(lo) >= 10
    assert all("data scientist" in r["job_title"].lower() for r in matching_api.get("/api/matching/results", params={"job_title": "DATA scientist"}).json())
    assert all("Northwind" in r["company_name"] for r in matching_api.get("/api/matching/results", params={"company": "northwind"}).json())
    assert matching_api.get("/api/matching/results", params={"limit": 3}).json().__len__() == 3
    assert matching_api.get("/api/matching/results", params={"eligibility": "MAYBE"}).status_code == 422
    assert matching_api.get("/api/matching/results", params={"min_score": 140}).status_code == 422
    assert matching_api.get("/api/matching/results", params={"job_title": "(.*["}).status_code == 200  # regex chars are escaped


def test_results_latest_only_sort_and_experience_filter(matching_api):
    ids = {}
    for j in JDS[:8]:
        job = {"job_description": j["text"], "job_title": j["title"], "company_name": j["company"], "location": j.get("location")}
        ids[j["title"]] = matching_api.post("/api/matching/score", json={"job": job}).json()["report"]["job_id"]
        matching_api.post("/api/matching/score", json={"job": job, "force": True})                 # a second, newer match of the same job
    everything = matching_api.get("/api/matching/results", params={"limit": 200}).json()
    latest = matching_api.get("/api/matching/results", params={"limit": 200, "latest_only": True}).json()
    assert len(everything) == 16 and len(latest) == 8 and len({r["job_id"] for r in latest}) == 8
    newest = {}
    for r in everything:
        newest.setdefault(r["job_id"], r["match_id"])                                               # newest first
    assert {r["match_id"] for r in latest} == set(newest.values())                                  # the newest match of each job, not an older one

    by_score = matching_api.get("/api/matching/results", params={"latest_only": True, "sort": "score"}).json()
    assert [r["match_score"] for r in by_score] == sorted((r["match_score"] for r in by_score), reverse=True)
    assert matching_api.get("/api/matching/results", params={"sort": "nonsense"}).status_code == 422

    # filters apply to the NEWEST match of each job and combine with latest_only
    passing = matching_api.get("/api/matching/results", params={"latest_only": True, "eligibility": "PASS"}).json()
    assert passing and all(r["eligibility_status"] == "PASS" for r in passing)
    years = [r["min_experience_required"] for r in latest if r["min_experience_required"] is not None]
    assert years, "the sample JDs state minimum experience"
    cap = sorted(years)[len(years) // 2]
    few = matching_api.get("/api/matching/results", params={"latest_only": True, "max_experience_required": cap}).json()
    assert few and all(r["min_experience_required"] is None or r["min_experience_required"] <= cap for r in few)
    assert len(few) < len(latest) or all(y <= cap for y in years)
    assert matching_api.get("/api/matching/results", params={"max_experience_required": -1}).status_code == 422


def test_role_fit_is_reported_and_lowers_the_score_of_a_different_function(matching_api):
    sales = {"job_title": "Enterprise Account Executive", "company_name": "Acme", "location": "Pune, India", "job_description":
             "Enterprise Account Executive\nRequirements:\n- 3+ years of experience in enterprise sales.\n- Experience with machine learning products and Python.\n"
             "Responsibilities:\n- Own the sales cycle for machine learning platform deals.\n- Build pipeline with enterprise customers."}
    rep = matching_api.post("/api/matching/score", json={"job": sales}).json()["report"]
    rf = rep["role_fit"]
    assert rf["relation"] == "different" and rf["factor"] == 0.4 and rf["job_family"] == "sales"
    assert rep["match_score"] == pytest.approx(rf["score_before_role_fit"] * 0.4, abs=0.11)
    assert any(w.startswith("Role fit:") for w in rep["weaknesses"])
    summary = next(r for r in matching_api.get("/api/matching/results").json() if r["match_id"] == rep["match_id"])
    assert summary["role_fit"] == "different" and summary["min_experience_required"] == 3

    ml = matching_api.post("/api/matching/score", json={"job": JD}).json()["report"]            # "Data Scientist": the candidate's own function
    assert ml["role_fit"]["relation"] == "same" and ml["role_fit"]["factor"] == 1.0
    assert ml["match_score"] == ml["role_fit"]["score_before_role_fit"]


def test_unknown_and_malformed_match_ids(matching_api):
    assert matching_api.get("/api/matching/" + "a" * 32).status_code == 404
    assert matching_api.get("/api/matching/not-a-valid-id").status_code == 422
    assert matching_api.post("/api/matching/" + "a" * 32 + "/rerun").status_code == 404


def test_rerun_after_profile_correction_and_history(matching_api, test_db, seeded, matching_service):
    first = score(matching_api).json()["report"]
    # candidate corrects the profile (Phase 2 flow bumps profile_version)
    repo = ResumesRepository(test_db)
    p = repo.get_profile(seeded.resume_id)
    p.technical_skills.other_skills.append("Statistics")
    p.profile_version += 1
    repo.save_profile(p)
    again = score(matching_api).json()          # new profile version -> different input hash -> new record
    assert again["created"] is True and again["report"]["profile_version"] == 2
    rerun = matching_api.post(f"/api/matching/{first['match_id']}/rerun")
    assert rerun.status_code == 200 and rerun.json()["profile_version"] == 2 and rerun.json()["match_id"] != first["match_id"]
    hist = matching_api.get(f"/api/matching/{first['match_id']}/history").json()
    assert len(hist) == 3 and {h["profile_version"] for h in hist} == {1, 2}


def test_rerun_after_scoring_config_change(make_matching_service, seeded, scoring_cfg):
    from config.scoring_config import ScoringConfig
    from ai.schemas.job_schema import JobInput
    svc = make_matching_service()
    job = JobInput(**{"job_description": JD["job_description"], "job_title": "Data Scientist"})
    r1, created1 = svc.score(job=job)
    bumped = f"{scoring_cfg.scoring_version}-test"
    changed = ScoringConfig.model_validate({**scoring_cfg.model_dump(), "scoring_version": bumped,
                                            "weights": {**scoring_cfg.weights, "technical_skills": 40, "projects": 5}})
    svc2 = make_matching_service(cfg=changed)
    r2, created2 = svc2.score(job=job)
    assert created1 and created2 and r1.input_hash != r2.input_hash
    assert (r1.scoring_version, r2.scoring_version) == (scoring_cfg.scoring_version, bumped) and r1.config_hash != r2.config_hash
    r3 = svc2.rerun(r1.match_id)  # re-run uses the CURRENT configuration
    assert r3.scoring_version == bumped
    assert len(svc2.history(r1.match_id)) == 3


def test_deleting_resume_removes_its_matches(matching_api, test_db, seeded):
    score(matching_api)
    assert test_db["job_matches"].count_documents({"resume_id": seeded.resume_id}) == 1
    ResumesRepository(test_db).delete_resume(seeded.resume_id)
    assert test_db["job_matches"].count_documents({}) == 0


# ---- Claude paths (fake SDK client) -------------------------------------------------------------------------------------
def ai_service(make_matching_service, script):
    return make_matching_service(ai_client=make_client(script))


def test_claude_unavailable_falls_back_to_deterministic(make_matching_service, seeded):
    from ai.schemas.job_schema import JobInput
    svc = ai_service(make_matching_service, [RuntimeError("network down")])
    report, _ = svc.score(job=JobInput(job_description=JD["job_description"], job_title="Data Scientist"))
    assert report.jd_parser == "deterministic" and report.ai_used is False and report.ai_summary is None
    assert report.match_score > 0 and report.eligibility.status == "PASS"
    _, analysis, _ = svc.analyze_jd(JobInput(job_description=JD["job_description"], job_title="Data Scientist"))
    assert any("deterministic parse only" in w for w in analysis.warnings)


def test_claude_malformed_response_falls_back(make_matching_service, seeded):
    from ai.schemas.job_schema import JobInput
    svc = ai_service(make_matching_service, ["totally not json"])
    report, _ = svc.score(job=JobInput(job_description=JD["job_description"], job_title="Data Scientist"))
    assert report.ai_used is False and report.jd_parser == "deterministic"


def test_claude_success_marks_ai_used_but_score_stays_deterministic(make_matching_service, seeded):
    from ai.schemas.job_schema import JobInput
    job = JobInput(job_description=JD["job_description"], job_title="Data Scientist")
    det, _ = make_matching_service().score(job=job, use_ai=False)
    extraction = json.dumps({"skills": [{"name": s, "importance": "mandatory"} for s in ("Python", "SQL", "Scikit-learn", "Machine Learning")]
                             + [{"name": "XGBoost", "importance": "preferred"}, {"name": "Tableau", "importance": "preferred"}]})
    summary = json.dumps({"summary": f"Score {det.match_score:g} ({det.classification}); eligibility PASS."})
    svc = make_matching_service(ai_client=make_client([extraction, summary, summary]))
    ai_report, created = svc.score(job=job)
    assert created and ai_report.ai_used and ai_report.jd_parser == "claude+deterministic" and ai_report.ai_model
    assert ai_report.ai_summary and ai_report.explanation and ai_report.explanation != ai_report.ai_summary
    # same classification of skills => same deterministic numbers; the AI wording did not touch them
    assert ai_report.match_score == det.match_score and ai_report.score_breakdown == det.score_breakdown


def test_ai_refine_off_keeps_the_ai_to_summary_wording_so_it_cannot_change_scores(make_matching_service, seeded):
    """AI_REFINE_JD=false: the AI may word the summary but never re-classifies the job's skills (which can move scores/eligibility)."""
    from ai.schemas.job_schema import JobInput
    job = JobInput(job_description=JD["job_description"], job_title="Data Scientist")
    det, _ = make_matching_service().score(job=job, use_ai=False)
    summary = json.dumps({"summary": "Wording only."})
    ai = make_client([summary])
    svc = make_matching_service(ai_client=ai)
    svc.settings = svc.settings.model_copy(update={"ai_refine_jd": False})
    report, created = svc.score(job=job, force=True)
    assert created and report.jd_parser == "deterministic"
    assert report.match_score == det.match_score and report.eligibility.status == det.eligibility.status and report.score_breakdown == det.score_breakdown
    assert report.ai_used and report.ai_summary == "Wording only."                      # the summary call still happened
    assert len(ai._client.messages.calls) == 1                                          # ...and it was the ONLY call: no skill re-classification
    assert "summary" in ai._client.messages.calls[0]["system"].lower()


def test_use_ai_false_never_calls_claude(make_matching_service, seeded):
    from ai.schemas.job_schema import JobInput
    ai = make_client(['{"skills": []}'])
    svc = make_matching_service(ai_client=ai)
    svc.score(job=JobInput(job_description=JD["job_description"]), use_ai=False)
    assert ai._client.messages.calls == []


def test_analysis_cache_is_keyed_by_ai_mode(make_matching_service, seeded):
    from ai.schemas.job_schema import JobInput
    job = JobInput(job_description=JD["job_description"], job_title="Data Scientist")
    _, _, cached1 = make_matching_service().analyze_jd(job)
    _, _, cached2 = make_matching_service().analyze_jd(job)
    _, _, cached3 = make_matching_service(ai_client=make_client(['{"skills": []}'])).analyze_jd(job)
    assert (cached1, cached2, cached3) == (False, True, False)  # a different parser mode is not served from the old cache


def test_preferred_only_keeps_jobs_you_scored_yourself_and_filters_by_job_ids(matching_api, matching_service):
    r = score(matching_api)
    assert r.status_code == 200
    jid = r.json()["report"]["job_id"]
    assert jid in matching_service.preferred_job_ids()                                          # a job you pasted yourself always counts
    rows = matching_api.get("/api/matching/results", params={"preferred_only": "true"}).json()
    assert [x["job_id"] for x in rows] == [jid]
    assert matching_service.matches.list_matches(job_ids=[]) == []                              # an empty selection means no rows, not all rows


def test_counts_and_result_total(matching_api):
    assert score(matching_api).status_code == 200
    c = matching_api.get("/api/matching/counts").json()
    assert c["jobs_stored"] == 1 and c["fit_preferences"] == 1 and c["scored"] == 1 and c["not_scored"] == 0
    assert c["eligible"] + c["not_eligible"] + c["eligibility_unknown"] == 1 and 0 <= c["good_matches"] <= c["eligible"]
    assert matching_api.get("/api/matching/results/count", params={"latest_only": "true", "preferred_only": "true"}).json() == {"total": 1}
    assert matching_api.get("/api/matching/results/count", params={"min_score": 100, "max_score": 100, "job_title": "zzz"}).json() == {"total": 0}


def test_applied_filter_and_counts(matching_api, matching_service):
    jid = score(matching_api).json()["report"]["job_id"]
    q = {"latest_only": "true", "preferred_only": "true"}
    assert [r["job_id"] for r in matching_api.get("/api/matching/results", params={**q, "applied": "no"}).json()] == [jid]
    assert matching_api.get("/api/matching/results", params={**q, "applied": "yes"}).json() == []
    assert matching_api.get("/api/matching/results", params=q).json()[0]["application_state"] is None
    matching_service.jobs._jobs.database["applications"].insert_one({"application_id": "x" * 32, "job_id": jid, "state": "SUBMITTED", "created_at": datetime.now()})
    rows = matching_api.get("/api/matching/results", params={**q, "applied": "yes"}).json()
    assert [r["job_id"] for r in rows] == [jid] and rows[0]["application_state"] == "SUBMITTED"
    assert matching_api.get("/api/matching/results/count", params={**q, "applied": "no"}).json() == {"total": 0}
    c = matching_api.get("/api/matching/counts").json()
    assert c["applied"] == 1 and c["to_apply"] == 0
