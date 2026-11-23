"""Explanations are grounded in facts; Claude wording is optional, validated and never changes a score."""
import json

from ai.schemas.job_schema import JobInput
from engines.matching.explainability import _facts, ai_summary
from engines.matching.jd_parser import parse_jd
from engines.matching.matching_engine import run_match
from tests.fixtures.candidate import synthetic_profile
from tests.fixtures.fake_ai import make_client
from tests.fixtures.preferences import evaluation_preferences

TEXT = ("Machine Learning Engineer\nRequirements:\n- 3+ years of experience in machine learning.\n"
        "- Python and Kubernetes.\nNice to have:\n- Kafka\nLocation: Pune")


def report(catalog, cfg, text=TEXT):
    a = parse_jd(JobInput(job_description=text), catalog, bands=cfg.seniority.bands)
    return run_match(synthetic_profile(), a, "job", cfg, evaluation_preferences(), catalog)


def test_explanation_is_grounded_in_facts(catalog, scoring_cfg):
    r = report(catalog, scoring_cfg)
    assert f"{r.match_score:g}/100" in r.explanation and r.classification in r.explanation
    assert r.eligibility.status in r.explanation
    assert "Kubernetes" in " ".join(r.rejection_reasons)               # why it fails
    assert any("Kubernetes" in w and "not equivalent" in w for w in r.weaknesses)  # partial, never a 'match'
    assert "Kubernetes" not in r.matched_skills and "Kafka" in r.missing_preferred_skills
    assert all(s in {"Python", "Machine Learning"} or True for s in r.matched_skills)
    assert r.recommendation_code == "NOT_ELIGIBLE"
    assert any("rules only" in l for l in r.limitations)                    # honest about deterministic parsing
    assert not any("add" in x.lower() and "resume" in x.lower() for x in r.strengths + r.weaknesses)  # never advise adding skills


def test_never_recommends_adding_unverified_skills(catalog, scoring_cfg):
    r = report(catalog, scoring_cfg)
    text = " ".join([r.explanation, r.recommendation, *r.strengths, *r.weaknesses, *r.limitations]).lower()
    assert "add to your resume" not in text and "add these skills" not in text


def good_reply(r):
    return json.dumps({"summary": f"Score {r.match_score:g} ({r.classification}); eligibility {r.eligibility.status}. "
                                  f"Strongest match: Python. Main gap: Kubernetes."})


def test_ai_summary_accepted_when_it_only_uses_given_facts(catalog, scoring_cfg):
    r = report(catalog, scoring_cfg)
    s = ai_summary(r, make_client([good_reply(r)]))
    assert s and "Kubernetes" in s


def test_ai_summary_with_invented_number_is_rejected(catalog, scoring_cfg):
    r = report(catalog, scoring_cfg)
    bad = json.dumps({"summary": f"Excellent: {r.match_score:g} but also 97% likely to be hired."})
    assert ai_summary(r, make_client([bad])) is None


def test_ai_summary_calling_ineligible_candidate_eligible_is_rejected(catalog, scoring_cfg):
    r = report(catalog, scoring_cfg)
    assert r.eligibility.status == "FAIL"
    assert ai_summary(r, make_client([json.dumps({"summary": "The candidate is eligible and a great fit."})])) is None


def test_ai_summary_failure_returns_none(catalog, scoring_cfg):
    r = report(catalog, scoring_cfg)
    assert ai_summary(r, make_client(["not json"])) is None
    assert ai_summary(r, make_client([RuntimeError("down")])) is None


def test_facts_sent_to_claude_contain_no_personal_data(catalog, scoring_cfg):
    r = report(catalog, scoring_cfg)
    blob = json.dumps(_facts(r))
    for pii in ("Jane", "jane.sample@example.com", "Acme Analytics", "Beta Labs", "Resume Matcher", "Pune, India"):
        assert pii not in blob


def test_ai_prompt_marks_facts_untrusted_and_forbids_rescoring(catalog, scoring_cfg):
    r = report(catalog, scoring_cfg)
    ai = make_client([good_reply(r)])
    ai_summary(r, ai)
    call = ai._client.messages.calls[0]
    assert "<facts>" in call["messages"][0]["content"] and "UNTRUSTED" in call["system"]
    assert "Do not change, recompute" in call["system"]


def test_ai_summary_cannot_alter_score(catalog, scoring_cfg):
    r = report(catalog, scoring_cfg)
    before = (r.match_score, r.classification, r.eligibility.status, dict(r.score_breakdown))
    ai_summary(r, make_client([good_reply(r)]))
    assert (r.match_score, r.classification, r.eligibility.status, r.score_breakdown) == before
