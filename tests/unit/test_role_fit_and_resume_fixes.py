"""Role fit (function match) + resume-parsing fixes found while auditing real postings and a real resume."""
import pytest

from config.scoring_config import RoleFitCfg, load_scoring_config
from engines.matching.role_fit import candidate_families, family_of, role_fit
from engines.resume.profile_extractor import split_sections
from engines.resume.skills_taxonomy import is_spoken_language
from tests.fixtures.candidate import synthetic_profile


@pytest.fixture(scope="module")
def cfg() -> RoleFitCfg:
    c = load_scoring_config().role_fit
    assert c.enabled
    return c


@pytest.mark.parametrize("title,family", [
    ("Senior Machine Learning Engineer, Ads", "technical"), ("Sr. Data Scientist, Marketing", "technical"),      # a technical noun beats "Marketing"
    ("Sales Engineer", "sales"), ("Enterprise Account Executive - Nordics", "sales"),
    ("Product Marketing Manager, Application Development", "marketing"), ("Analyst Relations Manager", "marketing"),
    ("Product Finance Director, R&D Capital Allocation & Engineering", "finance_legal"),                          # "Engineering" is a department here
    ("Director, Order to Revenue Compliance", "sales"), ("Senior Counsel, Commercial", "finance_legal"),
    ("Technical Recruiter", "people"), ("Data Analyst", "technical"), ("Staff Product Manager, ML Foundations", "product"),
    ("AI Engineer, Customer Success", "technical"), ("Customer Enablement AI Programs Manager", "customer"),
    ("Engineering Manager, Streaming", "technical"), ("Deployment Strategist", "customer"), ("Wizard of Oz", None), (None, None),
])
def test_job_title_families(cfg, title, family):
    assert family_of(title, cfg) == family


def test_role_fit_same_adjacent_different_unknown(cfg):
    p = synthetic_profile()                                                       # an ML/NLP engineer
    assert candidate_families(p, cfg) == ["technical"]
    same, adj, diff, unk = (role_fit(t, p, cfg) for t in ("Machine Learning Engineer", "Technical Product Manager", "Account Executive", "Wizard"))
    assert (same.relation, same.factor) == ("same", 1.0)
    assert (adj.relation, adj.factor) == ("adjacent", 0.8) and adj.job_family == "product"
    assert (diff.relation, diff.factor) == ("different", 0.4) and "different function" in diff.note
    assert (unk.relation, unk.factor) == ("unknown", 1.0)                          # nothing is penalised when the function cannot be read


def test_role_fit_off_or_unreadable_candidate_changes_nothing(cfg):
    p = synthetic_profile()
    assert role_fit("Account Executive", p, RoleFitCfg()).factor == 1.0            # disabled by default
    p.summary.current_job_title = None
    for w in p.work_experience:
        w.job_title = None
    assert role_fit("Account Executive", p, cfg).relation == "unknown"


# ---------------------------------------------------------------- resume parsing
@pytest.mark.parametrize("item,spoken", [("English (Native)", True), ("German - Fluent", True), ("French", True), ("Hindi", True),
                                         ("Python", False), ("R", False), ("Go", False), ("SQL", False), ("Java", False)])
def test_spoken_languages_are_not_technical_skills(item, spoken):
    assert is_spoken_language(item) is spoken


def test_spoken_language_line_is_dropped_but_programming_languages_stay():
    from engines.resume.profile_extractor import extract_skills
    skills, _, _ = extract_skills(["Languages: Python, SQL, R", "Spoken Languages: English (Native), German (Fluent)", "Tools: Docker, Git"],
                                  "Languages: Python, SQL, R\nSpoken Languages: English (Native), German (Fluent)")
    assert skills.programming_languages == ["Python", "SQL", "R"]
    assert not any("English" in s or "German" in s for _, s in skills.all_skills())


def test_trailing_sections_do_not_leak_into_certifications_and_page_footers_are_dropped():
    text = ("CERTIFICATIONS\n• Python 3 Programming — Udemy\n• Machine Learning — Coursera\n"
            "COMPETITIONS & SEMINARS\n• Winner, Office Hackathon — Compare Flow\n"
            "CONTENT & PUBLICATIONS\n• Authored blogs on CNNs on Medium.\n"
            "JANE DOE • 2\nEDUCATION\nB.E. Computer Engineering")
    secs = split_sections(text)
    assert [l for l in secs["certifications"] if l.strip()] == ["• Python 3 Programming — Udemy", "• Machine Learning — Coursera"]
    assert any("Hackathon" in l for l in secs["achievements"]) and any("blogs" in l for l in secs["achievements"])
    assert not any("JANE DOE" in l for lines in secs.values() for l in lines)
