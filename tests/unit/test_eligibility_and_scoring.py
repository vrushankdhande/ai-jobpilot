"""Eligibility checker, weighted scorer, thresholds and report consistency."""
import pytest
from pydantic import ValidationError

from ai.schemas.candidate_profile import Certification, Education, WorkExperience
from ai.schemas.job_schema import JobInput
from ai.schemas.match_schema import CategoryScore, MatchReport
from config.scoring_config import Preferences, ScoringConfig
from engines.matching.candidate_view import build_candidate_view
from engines.matching.eligibility_checker import check_eligibility
from engines.matching.jd_parser import parse_jd
from engines.matching.match_scorer import classify, score_match
from engines.matching.matching_engine import run_match
from engines.matching.skill_analysis import analyze_skills
from tests.fixtures.candidate import synthetic_profile
from tests.fixtures.preferences import evaluation_preferences


def run(text, catalog, cfg, profile=None, prefs=None, **job):
    a = parse_jd(JobInput(job_description=text, **job), catalog, bands=cfg.seniority.bands)
    return a, run_match(profile or synthetic_profile(), a, "job1", cfg, prefs or evaluation_preferences(), catalog)


def check(report, name):
    return next(c for c in report.eligibility.checks if c.name == name)


def cfg_with(base: ScoringConfig, **over) -> ScoringConfig:
    return ScoringConfig.model_validate({**base.model_dump(), **over})


GOOD = "Machine Learning Engineer\nRequirements:\n- 3+ years of experience in machine learning.\n- Python and Scikit-learn.\nLocation: Pune"


# ---- eligibility: statuses ------------------------------------------------------------------------------
def test_pass_fail_unknown_not_applicable(catalog, scoring_cfg):
    _, r = run(GOOD, catalog, scoring_cfg)
    assert check(r, "min_experience").status == "PASS" and check(r, "mandatory_skills").status == "PASS"
    assert check(r, "education").status == "NOT_APPLICABLE" and check(r, "max_experience").status == "NOT_APPLICABLE"
    _, r = run("Requirements:\n- 12+ years of experience.\n- Python", catalog, scoring_cfg)
    assert check(r, "min_experience").status == "FAIL" and r.eligibility.status == "FAIL"


def test_missing_candidate_info_is_unknown_not_fail_and_blocks(catalog, scoring_cfg):
    p = synthetic_profile()
    p.summary.total_experience_years = None
    for j in p.work_experience:
        j.start_date = None  # experience cannot be calculated
    _, r = run("Requirements:\n- 5+ years of experience.\n- Python", catalog, scoring_cfg, profile=p)
    c = check(r, "min_experience")
    assert c.status == "UNKNOWN" and "min_experience" in r.eligibility.unresolved_checks
    assert r.eligibility.status == "UNKNOWN" and r.eligibility.can_proceed is False
    assert r.recommendation_code == "ELIGIBILITY_UNRESOLVED"


def test_unknown_can_be_allowed_by_policy(catalog, scoring_cfg):
    p = synthetic_profile()
    p.summary.total_experience_years = None
    for j in p.work_experience:
        j.start_date = None
    cfg = cfg_with(scoring_cfg, eligibility={**scoring_cfg.eligibility.model_dump(), "allow_unknown_to_proceed": True})
    _, r = run("Requirements:\n- 5+ years of experience.\n- Python", catalog, cfg, profile=p)
    assert r.eligibility.status == "UNKNOWN" and r.eligibility.can_proceed is True


def test_preferred_skills_never_become_hard_requirements(catalog, scoring_cfg):
    _, r = run("Requirements:\n- Python\nNice to have:\n- Kafka\n- Terraform\n- Spark", catalog, scoring_cfg)
    assert r.eligibility.status == "PASS" and set(r.missing_preferred_skills) == {"Kafka", "Terraform", "Spark"}


def test_unrequired_qualifications_do_not_reject(catalog, scoring_cfg):
    _, r = run("Requirements:\n- Python", catalog, scoring_cfg)  # no degree, no certs, no experience asked
    assert r.eligibility.status == "PASS"
    assert all(check(r, n).status == "NOT_APPLICABLE" for n in ("education", "certifications", "min_experience"))


def test_example_skills_are_not_required(catalog, scoring_cfg):
    _, r = run("Requirements:\n- Python.\n- Cloud platforms such as Oracle or Heroku.", catalog, scoring_cfg)
    assert r.eligibility.status == "PASS"


def test_score_and_eligibility_are_separate(catalog, scoring_cfg):
    _, r = run("Staff Machine Learning Engineer\nRequirements:\n- 12+ years of experience.\n- Python and Scikit-learn.", catalog, scoring_cfg)
    assert r.match_score >= 60 and r.eligibility.status == "FAIL"
    assert r.recommendation_code == "NOT_ELIGIBLE" and "Not eligible" in r.recommendation


def test_mandatory_skill_policy_is_configurable(catalog, scoring_cfg):
    text = "Requirements:\n- Python, SQL, Kafka and Java."
    _, strict = run(text, catalog, scoring_cfg)
    assert check(strict, "mandatory_skills").status == "FAIL"
    lenient = cfg_with(scoring_cfg, eligibility={**scoring_cfg.eligibility.model_dump(),
                                                 "mandatory_skills_fail_if_missing_fraction_above": 0.5})
    _, r = run(text, catalog, lenient)
    assert check(r, "mandatory_skills").status == "PASS"  # 2/4 missing is within 50 %


def test_partial_skill_does_not_satisfy_mandatory(catalog, scoring_cfg):
    _, r = run("Requirements:\n- Experience with Kubernetes.", catalog, scoring_cfg)
    assert check(r, "mandatory_skills").status == "FAIL" and "partial via Docker" in check(r, "mandatory_skills").detail


def test_mandatory_skills_unknown_when_profile_has_no_skills(catalog, scoring_cfg):
    p = synthetic_profile()
    p.technical_skills = type(p.technical_skills)()
    p.work_experience, p.projects = [], []
    _, r = run("Requirements:\n- Python", catalog, scoring_cfg, profile=p)
    assert check(r, "mandatory_skills").status == "UNKNOWN"


def test_education_checks(catalog, scoring_cfg):
    _, r = run("Requirements:\n- Master's degree in Computer Science.", catalog, scoring_cfg)
    assert check(r, "education").status == "FAIL"
    _, r = run("Requirements:\n- Master's degree or equivalent experience.", catalog, scoring_cfg)
    assert check(r, "education").status == "UNKNOWN"  # equivalent experience accepted -> confirm manually
    p = synthetic_profile(education=[])
    _, r = run("Requirements:\n- Bachelor's degree.", catalog, scoring_cfg, profile=p)
    assert check(r, "education").status == "UNKNOWN"
    _, r = run("Requirements:\n- Bachelor's degree.", catalog, scoring_cfg)
    assert check(r, "education").status == "PASS"
    _, r = run("Nice to have:\n- PhD", catalog, scoring_cfg)
    assert check(r, "education").status == "NOT_APPLICABLE"  # preferred only


def test_certification_checks(catalog, scoring_cfg):
    _, r = run("Requirements:\n- AWS Certified Solutions Architect is required.", catalog, scoring_cfg)
    assert check(r, "certifications").status == "FAIL"
    p = synthetic_profile(certifications=[Certification(certification_name="AWS Certified Solutions Architect - Associate")])
    _, r = run("Requirements:\n- AWS Certified Solutions Architect is required.", catalog, scoring_cfg, profile=p)
    assert check(r, "certifications").status == "PASS"
    unreviewed = synthetic_profile(certifications=[], review_status="pending_review")
    _, r = run("Requirements:\n- AWS Certified Solutions Architect is required.", catalog, scoring_cfg, profile=unreviewed)
    assert check(r, "certifications").status == "UNKNOWN"


def test_location_checks(catalog, scoring_cfg):
    far = "Requirements:\n- Python\nLocation: Chennai (On-site)"
    assert check(run(far, catalog, scoring_cfg)[1], "location").status == "FAIL"
    assert check(run(far, catalog, scoring_cfg, prefs=evaluation_preferences(open_to_relocation=True))[1], "location").status == "PASS"
    assert check(run(far, catalog, scoring_cfg, prefs=evaluation_preferences(open_to_relocation=None))[1], "location").status == "UNKNOWN"
    assert check(run("Requirements:\n- Python\nLocation: Remote", catalog, scoring_cfg)[1], "location").status == "NOT_APPLICABLE"
    # location unknown never blocks automatically
    _, r = run(far, catalog, scoring_cfg, prefs=evaluation_preferences(open_to_relocation=None))
    assert "location" not in r.eligibility.unresolved_checks


def test_salary_is_a_soft_check_unless_configured(catalog, scoring_cfg):
    text = "Requirements:\n- Python\nSalary: INR 5-8 LPA"
    _, r = run(text, catalog, scoring_cfg)
    c = check(r, "salary")
    assert c.status == "FAIL" and c.hard is False and r.eligibility.status == "PASS"
    assert any("salary" in w for w in r.eligibility.soft_warnings)
    hard = cfg_with(scoring_cfg, eligibility={**scoring_cfg.eligibility.model_dump(), "salary_is_hard": True})
    assert run(text, catalog, hard)[1].eligibility.status == "FAIL"


def test_work_authorization(catalog, scoring_cfg):
    text = "Requirements:\n- Python\n- Must be authorized to work in the United States."
    assert check(run(text, catalog, scoring_cfg)[1], "work_authorization").status == "FAIL"
    assert check(run(text, catalog, scoring_cfg, prefs=evaluation_preferences(work_authorization=["United States"]))[1], "work_authorization").status == "PASS"
    assert check(run(text, catalog, scoring_cfg, prefs=evaluation_preferences(work_authorization=[]))[1], "work_authorization").status == "UNKNOWN"


# ---- scoring -----------------------------------------------------------------------------------------------
def test_score_formula_matches_documented_definition(catalog, scoring_cfg):
    _, r = run(GOOD, catalog, scoring_cfg)
    ev = [c for c in r.category_details if c.status == "EVALUATED"]
    assert all(abs(c.earned_points - c.ratio * c.weight) < 1e-3 for c in ev)
    assert abs(r.match_score - sum(c.earned_points for c in ev) / sum(c.weight for c in ev) * 100) <= 0.06
    assert 0 <= r.match_score <= 100 and abs(sum(c.weight for c in r.category_details) - 100) < 1e-9
    assert r.score_breakdown.keys() == scoring_cfg.weights.keys()


def test_weights_come_from_config(catalog, scoring_cfg):
    heavy_edu = cfg_with(scoring_cfg, weights={"technical_skills": 5, "experience": 5, "projects": 5, "responsibilities": 5,
                                               "education": 70, "location": 5, "seniority": 5})
    _, a = run(GOOD, catalog, scoring_cfg)
    _, b = run(GOOD, catalog, heavy_edu)
    assert a.match_score != b.match_score and {c.name: c.weight for c in b.category_details}["education"] == 70
    assert a.config_hash != b.config_hash and a.input_hash != b.input_hash


def test_missing_mandatory_skill_hurts_meaningfully(catalog, scoring_cfg):
    _, full = run("Requirements:\n- Python\n- Scikit-learn\n- SQL", catalog, scoring_cfg)
    _, miss = run("Requirements:\n- Python\n- Scikit-learn\n- Kafka", catalog, scoring_cfg)
    sk = lambda r: next(c for c in r.category_details if c.name == "technical_skills")  # noqa: E731
    assert sk(full).ratio == 1.0 and sk(miss).ratio < 0.75
    assert full.match_score - miss.match_score > 3


def test_exact_beats_related_beats_missing(catalog, scoring_cfg):
    ratio = lambda text: next(c for c in run(text, catalog, scoring_cfg)[1].category_details if c.name == "technical_skills").ratio  # noqa: E731
    exact, related, missing = ratio("Requirements:\n- Docker"), ratio("Requirements:\n- Kubernetes"), ratio("Requirements:\n- Terraform")
    assert exact == 1.0 and 0 < related < 1.0 and missing == 0.0


def test_mandatory_and_preferred_shares(catalog, scoring_cfg):
    _, r = run("Requirements:\n- Python\nNice to have:\n- Kafka", catalog, scoring_cfg)
    ratio = next(c for c in r.category_details if c.name == "technical_skills").ratio
    assert abs(ratio - 0.8) < 1e-6  # 80% mandatory (met) + 20% preferred (missed)


def test_any_of_group_counts_once(catalog, scoring_cfg):
    _, r = run("Requirements:\n- PyTorch or Keras.", catalog, scoring_cfg)
    assert check(r, "mandatory_skills").status == "PASS"  # PyTorch is present, Keras is not needed


def test_unknown_categories_are_unknown_not_zero_or_full(catalog, scoring_cfg):
    _, r = run("Requirements:\n- Python", catalog, scoring_cfg)  # no responsibilities/location/seniority in the JD
    by = {c.name: c for c in r.category_details}
    for n in ("responsibilities", "location", "seniority"):
        assert by[n].status == "UNKNOWN" and by[n].earned_points == 0 and r.score_breakdown[n] is None
    assert r.data_coverage == 100 - sum(by[n].weight for n in ("responsibilities", "location", "seniority"))


def test_missing_data_policy_normalize_vs_zero(catalog, scoring_cfg):
    zero = cfg_with(scoring_cfg, missing_data_policy="zero")
    _, norm = run("Requirements:\n- Python", catalog, scoring_cfg)
    _, z = run("Requirements:\n- Python", catalog, zero)
    assert z.match_score < norm.match_score and z.data_coverage == norm.data_coverage < 100
    assert abs(z.match_score - sum(c.earned_points for c in z.category_details)) < 0.06


def test_low_coverage_is_insufficient_data(catalog, scoring_cfg):
    p = synthetic_profile(projects=[], work_experience=[], education=[])
    p.summary.total_experience_years = None
    _, r = run("Requirements:\n- Python", catalog, scoring_cfg, profile=p)
    assert r.data_coverage < scoring_cfg.min_data_coverage and r.recommendation_code == "INSUFFICIENT_DATA"


@pytest.mark.parametrize("score,label", [
    (100, "Excellent Match"), (90, "Excellent Match"), (89.9, "Strong Match"), (80, "Strong Match"), (79.9, "Good Match"),
    (75, "Good Match"), (74.9, "Moderate Match"), (60, "Moderate Match"), (59.9, "Low Match"), (0, "Low Match")])
def test_threshold_boundaries(scoring_cfg, score, label):
    assert classify(score, scoring_cfg) == label


def test_thresholds_configurable(catalog, scoring_cfg):
    strict = cfg_with(scoring_cfg, thresholds={"excellent": 99, "strong": 98, "good": 97, "moderate": 96})
    _, r = run(GOOD, catalog, strict)
    assert r.classification == "Low Match"
    high_bar = cfg_with(scoring_cfg, recommendation_threshold=99)
    _, r = run(GOOD, catalog, high_bar)
    assert r.recommendation_code == "BELOW_THRESHOLD"


def test_recommended_requires_pass_threshold_and_coverage(catalog, scoring_cfg):
    _, r = run("Machine Learning Engineer\nRequirements:\n- 3+ years of experience in machine learning.\n- Python and SQL.\n"
               "Responsibilities:\n- Build and deploy machine learning models.\nLocation: Pune, India", catalog, scoring_cfg)
    assert r.eligibility.status == "PASS" and r.match_score >= 75 and r.recommendation_code == "RECOMMENDED"


# ---- experience / projects / responsibilities / seniority / location specifics ----------------------------------------
def cat(report, name):
    return next(c for c in report.category_details if c.name == name)


def test_experience_not_confused_with_skill_years_and_no_double_counting(catalog, scoring_cfg):
    p = synthetic_profile(work_experience=[
        WorkExperience(company_name="A", job_title="Dev", start_date="2020-01", end_date="2022-12", technologies_used=["Python"]),
        WorkExperience(company_name="B", job_title="Dev", start_date="2021-01", end_date="2023-12", technologies_used=["Python"]),
    ])
    view = build_candidate_view(p, catalog, scoring_cfg.seniority.bands)
    assert view.skill_years("Python", catalog) == 4.0   # 2020-01..2023-12 once, not 6 years
    assert view.skill_years("Kafka", catalog) == 0.0    # never used


def test_experience_is_unknown_without_data_and_not_invented(catalog, scoring_cfg):
    p = synthetic_profile(work_experience=[])
    p.summary.total_experience_years = None
    _, r = run("Requirements:\n- 3+ years of experience.\n- Python", catalog, scoring_cfg, profile=p)
    assert cat(r, "experience").status == "UNKNOWN"


def test_skill_years_unknown_when_dates_missing(catalog, scoring_cfg):
    p = synthetic_profile(work_experience=[WorkExperience(company_name="A", job_title="Dev", technologies_used=["Python"])])
    view = build_candidate_view(p, catalog, scoring_cfg.seniority.bands)
    assert view.skill_years("Python", catalog) is None  # used, but duration cannot be known


def test_projects_scored_and_unknown_when_absent(catalog, scoring_cfg):
    _, r = run("Requirements:\n- Python\n- FastAPI\n- MongoDB", catalog, scoring_cfg)
    assert cat(r, "projects").ratio > 0.5 and r.project_relevance[0].project == "Resume Matcher"
    _, r = run("Requirements:\n- Python", catalog, scoring_cfg, profile=synthetic_profile(projects=[]))
    assert cat(r, "projects").status == "UNKNOWN"


def test_responsibilities_semantic_overlap(catalog, scoring_cfg):
    _, good = run("Responsibilities:\n- Deploy machine learning models to production.\n- Mentor junior engineers.\nRequirements:\n- Python", catalog, scoring_cfg)
    _, bad = run("Responsibilities:\n- Negotiate vendor contracts.\n- Plan annual budgets.\nRequirements:\n- Python", catalog, scoring_cfg)
    assert cat(good, "responsibilities").ratio > 0.7 and cat(bad, "responsibilities").ratio == 0.0


def test_education_optional_not_penalised(catalog, scoring_cfg):
    _, r = run("Nice to have:\n- PhD in Machine Learning\nRequirements:\n- Python", catalog, scoring_cfg)
    assert cat(r, "education").ratio == scoring_cfg.education.preferred_unmet_credit
    _, r = run("Requirements:\n- PhD in Machine Learning is required\n- Python", catalog, scoring_cfg)
    assert cat(r, "education").ratio == scoring_cfg.education.two_levels_below_credit


def test_seniority_uses_experience_not_title(catalog, scoring_cfg):
    p = synthetic_profile()
    p.summary.current_job_title = "Principal Architect"  # title alone must not raise the level
    p.summary.total_experience_years = 1.5
    _, r = run("Senior Engineer\nRequirements:\n- Python", catalog, scoring_cfg, profile=p, job_title="Senior Engineer")
    s = cat(r, "seniority")
    assert s.status == "EVALUATED" and s.ratio == scoring_cfg.seniority.credit_by_difference["-2"]
    assert any("title was not used" in n for n in s.notes)


def test_location_scoring_variants(catalog, scoring_cfg):
    loc = lambda text, **p: cat(run(text, catalog, scoring_cfg, prefs=evaluation_preferences(**p))[1], "location")  # noqa: E731
    assert loc("Requirements:\n- Python\nLocation: Pune").ratio == 1.0
    assert loc("Requirements:\n- Python\nLocation: Chennai (On-site)", open_to_relocation=True).ratio == scoring_cfg.location.city_mismatch_open_to_relocation
    assert loc("Requirements:\n- Python\nLocation: Chennai (On-site)").ratio == 0.0
    assert loc("Requirements:\n- Python\nLocation: Chennai (On-site)", open_to_relocation=None).status == "UNKNOWN"
    assert loc("Requirements:\n- Python\nLocation: Remote").ratio == 1.0
    assert loc("Requirements:\n- Python\nLocation: Remote", remote=False).ratio == 0.3


# ---- report schema validation ----------------------------------------------------------------------------------------------
def test_report_rejects_inconsistent_score(catalog, scoring_cfg):
    _, r = run(GOOD, catalog, scoring_cfg)
    data = r.model_dump()
    data["match_score"] = min(100.0, r.match_score + 7)
    with pytest.raises(ValidationError):
        MatchReport.model_validate(data)
    data = r.model_dump()
    data["match_score"] = 140
    with pytest.raises(ValidationError):
        MatchReport.model_validate(data)


def test_category_score_bounds():
    with pytest.raises(ValidationError):
        CategoryScore(name="x", weight=10, earned_points=11, status="EVALUATED", ratio=1)
    with pytest.raises(ValidationError):
        CategoryScore(name="x", weight=10, earned_points=3, status="UNKNOWN")


def test_identical_inputs_give_identical_hash_and_score(catalog, scoring_cfg):
    _, a = run(GOOD, catalog, scoring_cfg)
    _, b = run(GOOD, catalog, scoring_cfg)
    assert a.input_hash == b.input_hash and a.match_score == b.match_score
    p2 = synthetic_profile()
    p2.profile_version = 2
    assert run(GOOD, catalog, scoring_cfg, profile=p2)[1].input_hash != a.input_hash


def test_report_contains_all_required_sections(catalog, scoring_cfg):
    _, r = run(GOOD + "\nNice to have:\n- Kafka", catalog, scoring_cfg)
    d = r.model_dump()
    for key in ("match_score", "classification", "eligibility", "score_breakdown", "matched_skills", "missing_mandatory_skills",
                "missing_preferred_skills", "partial_matches", "strengths", "weaknesses", "explanation", "recommendation",
                "data_coverage", "scoring_version", "created_at", "experience_analysis", "project_relevance",
                "responsibility_analysis", "education_analysis", "location_analysis", "rejection_reasons", "limitations"):
        assert key in d
    assert r.scoring_version == scoring_cfg.scoring_version
