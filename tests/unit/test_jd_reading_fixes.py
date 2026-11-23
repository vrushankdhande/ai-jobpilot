"""Regression tests for JD-reading faults found by running the engine on real job postings (see docs/scoring_methodology.md, v1.1).

Each case is a *generic* wording pattern seen in real postings - none is tuned to one company or to the evaluation dataset.
"""
import pytest

from ai.schemas.job_schema import JobInput
from engines.matching.jd_parser import parse_jd, tag_lines


def parse(text: str, title: str = "Machine Learning Engineer", company: str = "Acme"):
    return parse_jd(JobInput(job_description=text, job_title=title, company_name=company))


def imp(a, name: str) -> str | None:
    return next((s.importance for s in a.skills if s.name == name), None)


# ---------------------------------------------------------------- headings
def test_curly_apostrophes_do_not_hide_the_section_headings():
    text = "Intro line about the team.\nWhat you’ll do:\n- Build Spark pipelines\nWhat we’re looking for:\n- Python and SQL\n- 5+ years of experience"
    secs = dict(tag_lines(text.translate(str.maketrans({"’": "'"}))))
    assert secs["- Python and SQL"] == "required"
    a = parse(text)
    assert imp(a, "Python") == "mandatory" and imp(a, "SQL") == "mandatory" and a.minimum_experience_years == 5
    assert not any("No requirements/responsibilities" in w for w in a.warnings)


@pytest.mark.parametrize("heading", ["What we look for", "Your Expertise", "The impact you will have", "The Difference You Will Make", "A Typical Day"])
def test_common_requirement_and_duty_headings_are_recognised(heading):
    a = parse(f"{heading}\n- Experience with Python\n- Experience with Kafka")
    assert not any("No requirements/responsibilities" in w for w in a.warnings)


def test_boilerplate_after_the_requirements_does_not_become_a_requirement():
    text = ("What we look for:\n- Python\n"
            "Pay Range Transparency\nAcme is committed to fair and equitable compensation practices. Experience with Kafka is helpful.\n"
            "About Acme\nWe use Spark and Kubernetes everywhere; experience required in nothing.")
    a = parse(text)
    assert imp(a, "Python") == "mandatory"
    assert imp(a, "Kafka") != "mandatory" and imp(a, "Spark") != "mandatory" and imp(a, "Kubernetes") != "mandatory"


def test_key_value_lines_are_not_mistaken_for_headings():
    assert dict(tag_lines("Requirements:\n- Python\nLocation: Pune"))["Location: Pune"] is not None
    assert parse("Requirements:\n- Python\nLocation: Pune").location == ["Pune"]


# ---------------------------------------------------------------- skills
def test_the_hiring_companys_own_product_is_never_a_prerequisite():
    a = parse("Requirements:\n- Hands-on experience with Databricks and Python\n- Experience with Spark", title="Solutions Architect", company="Databricks")
    assert imp(a, "Databricks") == "preferred" and imp(a, "Python") == "mandatory"
    assert any("own product" in w for w in a.warnings)
    b = parse("Requirements:\n- Hands-on experience with Databricks and Python", company="Acme")
    assert imp(b, "Databricks") == "mandatory"                              # another company still treats it as a requirement


@pytest.mark.parametrize("sentence", ["Experience with Kafka is a strong plus.", "Kafka and Airflow are huge pluses.", "Kafka experience is a big plus"])
def test_plus_wording_is_preferred(sentence):
    assert imp(parse(f"Requirements:\n- Python\n- {sentence}"), "Kafka") == "preferred"


@pytest.mark.parametrize("sentence,skill", [
    ("Hands-on experience with at least three of the following: PyTorch, Kafka, Airflow, Spark", "Kafka"),
    ("Experience in one or more relevant domains: Computer Vision, Reinforcement Learning", "Reinforcement Learning"),
    ("Strong grounding in infrastructure, including Kubernetes and Terraform", "Terraform"),
    ("Proficiency in the ML ecosystem (PyTorch, MLflow, Spark)", "MLflow"),
    ("Working proficiency in Ruby on Rails, or a strong willingness to learn it", "Ruby"),
])
def test_choose_n_lists_examples_and_willing_to_learn_are_not_individually_mandatory(sentence, skill):
    assert imp(parse(f"Requirements:\n- Python\n- {sentence}"), skill) != "mandatory"


def test_a_degree_field_is_not_a_skill_requirement():
    a = parse("Requirements:\n- Python\n- Master's or Ph.D. in Statistics, Mathematics or Physics")
    assert imp(a, "Statistics") is None
    assert [e.level for e in a.education_requirements] == ["master"]


def test_skills_named_in_responsibilities_need_explicit_wording():
    a = parse("What you'll do:\n- Build Kafka pipelines using strong hands-on engineering\n- You must know Spark")
    assert imp(a, "Kafka") != "mandatory" and imp(a, "Spark") == "mandatory"


# ---------------------------------------------------------------- certifications / experience
def test_certifications_that_are_huge_pluses_or_obtained_after_hire_are_not_gates():
    a = parse("Requirements:\n- PMP and NCMA certifications are huge pluses.\n- Databricks Certification required within 6 months of hire.\n- AWS Certified Solutions Architect")
    by = {c.name: c.mandatory for c in a.certification_requirements}
    assert by["PMP"] is False
    assert all(not m for n, m in by.items() if "Databricks" in n)
    assert by["AWS Certified Solutions Architect"] is True


def test_alternative_experience_routes_use_the_lowest():
    a = parse("Requirements:\n- For M.S. holders: 12+ years of industry experience.\n- For Ph.D. holders: 8+ years of industry experience.")
    assert a.minimum_experience_years == 8
    b = parse("Requirements:\n- 7+ years of experience in sales.")
    assert b.minimum_experience_years == 7


def test_company_history_years_are_not_an_experience_requirement():
    from engines.matching.jd_parser import parse_jd
    from engines.matching.skill_extractor import get_catalog
    from ai.schemas.job_schema import JobInput
    text = ("About the firm\nPoint72 is a global investment firm. Building on more than 30 years of investing experience, Point72 seeks to deliver superior returns.\n"
            "What you will need\n- 3+ years of experience building machine learning systems in Python.\n- A 25-year track record is ours, not yours.")
    a = parse_jd(JobInput(job_description=text, job_title="Machine Learning Engineer"), get_catalog())
    assert a.minimum_experience_years == 3                                   # the real requirement, not the company's 30 years
    b = parse_jd(JobInput(job_description="Requirements:\n- With 5+ years of experience in data science.", job_title="Data Scientist"), get_catalog())
    assert b.minimum_experience_years == 5                                   # "with N years of experience" for the CANDIDATE still counts
