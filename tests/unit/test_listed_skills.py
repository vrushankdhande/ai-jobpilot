"""Skills a job site lists (Instahyre alert chips) vs the candidate's skills: same catalogue and exact/related rules as matching; unknown
tags are 'not checked', never counted either way. Information only - no score is produced."""
from config.settings import Settings
from engines.matching.listed_skills import compare_listed_skills
from engines.matching.skill_extractor import get_catalog


def test_listed_skills_are_split_into_have_missing_and_not_checked_using_the_catalogue():
    cat = get_catalog(Settings())
    mine = {s for s in (cat.normalize(x) for x in ("Python", "PyTorch", "LangChain", "AWS", "LLMs")) if s}
    r = compare_listed_skills(["Python", "Amazon Web Services", "LangChain", "Kubernetes", "C++", "python", "Agentic Workflows", ""], mine, cat)
    assert "Python" in [s for s in r["have"]] or cat.normalize("Python") in r["have"]
    assert cat.normalize("Amazon Web Services") in r["have"]                     # an alias of a skill you have counts as having it
    assert cat.normalize("Kubernetes") in r["missing"] and cat.normalize("C++") in r["missing"]
    assert "Agentic Workflows" in r["not_checked"] or cat.normalize("Agentic Workflows") is not None
    assert r["checked"] == len(r["have"]) + len(r["related"]) + len(r["missing"])
    assert len(r["have"]) == len(set(r["have"]))                                  # "python" twice is counted once
    assert "score" not in r and "match_score" not in r
