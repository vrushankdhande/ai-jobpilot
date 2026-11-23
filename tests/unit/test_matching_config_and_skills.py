"""Configuration loading and skill normalisation."""
import pytest
from pydantic import ValidationError

from config.scoring_config import Preferences, ScoringConfig, load_preferences, load_scoring_config
from config.settings import Settings
from engines.matching.skill_extractor import SkillCatalog, skill_key


# ---- configuration ---------------------------------------------------------------------------
def test_scoring_config_loads_documented_defaults(scoring_cfg):
    assert scoring_cfg.weights == {"technical_skills": 30, "experience": 20, "projects": 15, "responsibilities": 15,
                                   "education": 5, "location": 5, "seniority": 10}
    assert sum(scoring_cfg.weights.values()) == 100
    t = scoring_cfg.thresholds
    assert (t.excellent, t.strong, t.good, t.moderate) == (90, 80, 75, 60)
    assert scoring_cfg.recommendation_threshold == 75
    assert scoring_cfg.scoring_version and scoring_cfg.config_hash
    assert scoring_cfg.skills.mandatory_share == 0.8 and scoring_cfg.skills.preferred_share == 0.2


def _cfg(**over):
    base = {"weights": {"technical_skills": 30, "experience": 20, "projects": 15, "responsibilities": 15,
                        "education": 5, "location": 5, "seniority": 10}}
    base.update(over)
    return base


@pytest.mark.parametrize("bad", [
    _cfg(weights={"technical_skills": 50, "experience": 20, "projects": 15, "responsibilities": 15, "education": 5, "location": 5, "seniority": 10}),
    _cfg(weights={"technical_skills": 100}),
    _cfg(thresholds={"excellent": 70, "strong": 80, "good": 75, "moderate": 60}),
    _cfg(missing_data_policy="invent"),
    _cfg(skills={"mandatory_share": 0.5, "preferred_share": 0.2}),
])
def test_invalid_scoring_config_rejected(bad):
    with pytest.raises(ValidationError):
        ScoringConfig.model_validate(bad)


def test_config_hash_changes_with_file_content(tmp_path, scoring_cfg):
    p = tmp_path / "w.yaml"
    raw = Settings().config_path("scoring_config_file").read_text(encoding="utf-8")
    p.write_text(raw, encoding="utf-8")
    h1 = load_scoring_config(path=p).config_hash
    p.write_text(raw.replace("technical_skills: 30", "technical_skills: 25").replace("seniority: 10", "seniority: 15"), encoding="utf-8")
    cfg2 = load_scoring_config(path=p)
    assert cfg2.config_hash != h1 and cfg2.weights["technical_skills"] == 25


def test_preferences_default_to_unknown(tmp_path):
    p = Preferences()
    assert p.open_to_relocation is None and p.min_salary is None and p.work_authorization == []
    assert load_preferences(path=tmp_path / "missing.yaml") == Preferences()


# ---- normalisation ---------------------------------------------------------------------------------
@pytest.mark.parametrize("raw,canonical", [
    ("Gen AI", "Generative AI"), ("GenAI", "Generative AI"), ("LLM", "Large Language Models"), ("LLMs", "Large Language Models"),
    ("Py Torch", "PyTorch"), ("pytorch", "PyTorch"), ("SKLearn", "Scikit-learn"), ("scikit learn", "Scikit-learn"),
    ("NLP", "Natural Language Processing"), ("Vector DB", "Vector Database"), ("RAG Pipeline", "Retrieval-Augmented Generation"),
    ("RAG", "Retrieval-Augmented Generation"), ("k8s", "Kubernetes"), ("Postgres", "PostgreSQL"), ("nodejs", "Node.js"),
    ("CI/CD", "CI/CD"), ("A/B testing", "A/B Testing"), ("python3", "Python"), ("Fine tuning", "Fine-tuning"),
])
def test_normalize_synonyms(catalog, raw, canonical):
    assert catalog.normalize(raw) == canonical


def test_unknown_skill_is_kept_not_dropped(catalog):
    assert catalog.normalize("Quantum Basketweaving") is None
    assert catalog.normalize_or_raw("  Quantum   Basketweaving. ") == "Quantum Basketweaving"


def test_skill_key_ignores_spacing_and_punctuation():
    assert skill_key("Scikit-learn") == skill_key("scikit learn") == "scikitlearn"
    assert skill_key("C++") == "c++" and skill_key("C#") == "c#"


def test_find_in_text_boundaries(catalog):
    names = lambda t: [m.canonical for m in catalog.find_in_text(t)]  # noqa: E731
    assert names("We use JavaScript and Java.") == ["JavaScript", "Java"]
    assert "SQL" not in names("PostgreSQL and MySQL experience")
    assert names("GitHub Actions") == ["GitHub"]
    assert names("Experience with Python.") == ["Python"]
    assert names("Gen AI and large language models") == ["Generative AI", "Large Language Models"]
    assert names("We love to excel at teamwork") == []  # 'excel' alone is ambiguous
    assert names("Go to market strategy, R&D") == []  # ambiguous single letters / words are not skills in prose
    assert catalog.list_item_skill("R") == "R" and catalog.list_item_skill("Go") == "Go"


def test_different_technologies_are_not_equivalent(catalog):
    assert catalog.match("Java", {"Python"}).kind == "none"          # same category, no credit
    assert catalog.match("Python", {"Java"}).credit == 0
    tf = catalog.match("PyTorch", {"TensorFlow"})
    assert tf.kind == "related" and 0 < tf.credit < 1 and "not equivalent" in tf.reason
    assert catalog.match("PyTorch", {"PyTorch"}).kind == "exact"
    assert catalog.match("AWS", {"Azure"}).credit < 1
    assert catalog.match("MySQL", {"PostgreSQL"}).credit < 1


def test_related_credit_is_directional_where_documented(catalog):
    assert catalog.match("Machine Learning", {"Scikit-learn"}).credit == 0.7
    assert catalog.match("Scikit-learn", {"Machine Learning"}).kind == "none"  # knowing 'ML' does not imply Scikit-learn


def test_new_skills_need_no_code_change(tmp_path):
    f = tmp_path / "skills.yaml"
    f.write_text("""skills:
  - {name: Zig, category: programming_languages, aliases: [ziglang]}
  - {name: Bun, category: tools}
related_groups:
  - {members: [Zig, Bun], credit: 0.25, reason: custom}
""", encoding="utf-8")
    cat = SkillCatalog.from_file(f)
    assert cat.normalize("ziglang") == "Zig"
    assert [m.canonical for m in cat.find_in_text("Experience with Zig required")] == ["Zig"]
    assert cat.match("Bun", {"Zig"}).credit == 0.25 and cat.digest


def test_catalog_digest_changes_when_file_changes(tmp_path):
    f = tmp_path / "s.yaml"
    f.write_text("skills:\n  - {name: A1, category: tools}\n", encoding="utf-8")
    d1 = SkillCatalog.from_file(f).digest
    f.write_text("skills:\n  - {name: A1, category: tools}\n  - {name: B2, category: tools}\n", encoding="utf-8")
    assert SkillCatalog.from_file(f).digest != d1
