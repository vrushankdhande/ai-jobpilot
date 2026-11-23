"""The 30-JD evaluation dataset: coverage of scenarios + per-JD agreement with hand-written expectations.

NOTE: automated agreement is NOT manual validation. See docs/phase3_evaluation_report.md for the
human-review report (which is still pending sign-off).
"""
import pytest

from engines.matching.evaluation import evaluate_one, metrics
from tests.fixtures.candidate import synthetic_profile
from tests.fixtures.jd_dataset import JDS
from tests.fixtures.preferences import evaluation_preferences

# Known limitation, kept visible (strict xfail): the responsibility matcher is lexical, so a JD with a single vague
# duty ("Build and ship generative AI features") earns almost no responsibility credit and the total drops to
# 'Moderate' although every mandatory skill matches. If this ever starts to pass, update the expectation/report.
KNOWN_MISSES = {"JD03": "lexical responsibility matching cannot see 'Build and ship GenAI features' ~ 'Built a RAG pipeline'"}


def test_dataset_has_30_distinct_jds_and_required_fields():
    assert len(JDS) >= 30
    assert len({j["id"] for j in JDS}) == len(JDS) and len({j["text"] for j in JDS}) == len(JDS)
    for j in JDS:
        for key in ("id", "scenario", "title", "text", "mandatory", "preferred", "eligibility", "classes", "score_range", "why"):
            assert key in j, (j["id"], key)
        assert j["eligibility"] in ("PASS", "FAIL", "UNKNOWN") and j["score_range"][0] <= j["score_range"][1]


def test_dataset_covers_the_required_scenarios():
    text = " ".join(j["scenario"].lower() for j in JDS)
    for needle in ["excellent", "ai/ml", "generative", "data science", "python", "machine learning", "deep learning", "nlp", "rag",
                   "computer vision", "data engineering", "backend", "cloud", "mlops", "data analyst", "entry", "senior",
                   "experience requirement mismatch", "mandatory skill mismatch", "preferred skill mismatch", "education",
                   "certification", "remote", "hybrid", "on-site", "location mismatch", "salary", "incomplete", "ambiguous", "unrelated"]:
        assert needle in text, needle
    assert {"PASS", "FAIL"} <= {j["eligibility"] for j in JDS}


@pytest.fixture(scope="module")
def cfg_module():
    from config.scoring_config import load_scoring_config
    return load_scoring_config()


@pytest.fixture(scope="module")
def rows(catalog, cfg_module):
    return [evaluate_one(j, synthetic_profile(), cfg_module, evaluation_preferences(), catalog) for j in JDS]


_PARAMS = [
    pytest.param(j, id=j["id"], marks=(pytest.mark.xfail(strict=True, reason=KNOWN_MISSES[j["id"]]) if j["id"] in KNOWN_MISSES else ()))
    for j in JDS
]


@pytest.mark.parametrize("jd", _PARAMS)
def test_each_jd(jd, rows):
    row = next(r for r in rows if r.jd["id"] == jd["id"])
    assert row.diffs == [] and row.explanation_issues == [], row.diffs + row.explanation_issues


def test_aggregate_metrics(rows):
    m = metrics(rows)
    assert m["mandatory_precision"] >= 0.95 and m["mandatory_recall"] >= 0.95
    assert m["preferred_precision"] >= 0.95 and m["preferred_recall"] >= 0.95
    assert m["eligibility_accuracy"] >= 0.95
    assert m["explanation_consistent"] == 1.0
    assert m["scores_in_expected_range"] >= 0.9


def test_safety_invariants_hold_for_every_jd(rows):
    for r in rows:
        rep = r.report
        assert 0 <= rep.match_score <= 100
        if rep.eligibility.status == "FAIL":
            assert rep.recommendation_code == "NOT_ELIGIBLE"
        for s in rep.missing_mandatory_skills:
            assert s not in rep.matched_skills
        assert not (set(r.analysis.preferred_skills) & set(rep.eligibility.failed_checks))
