"""The factuality validator for generated documents: every kind of unsupported claim must be caught."""
import copy

import pytest

from ai.schemas.candidate_profile import Certification
from ai.schemas.document_schema import PLACEHOLDER, content_hash
from ai.validators.document_validator import JobFacts, validate_document
from ai.schemas.job_schema import JobInput
from engines.generation.cover_letter import compose_cover_letter
from engines.generation.fact_pack import build_fact_pack
from engines.generation.resume_customizer import build_job_context
from engines.generation.screening_answers import compose_screening
from engines.matching.jd_parser import parse_jd
from engines.resume.resume_builder import build_resume
from config.scoring_config import Preferences
from tests.fixtures.candidate import synthetic_profile
from tests.fixtures.jd_dataset import JDS

JD = JDS[0]
JOB_SKILLS = {"Python", "PyTorch", "Machine Learning", "Retrieval-Augmented Generation", "LangChain", "MongoDB", "Docker", "Azure", "Kubernetes", "Java"}


@pytest.fixture
def env(catalog):
    p = synthetic_profile()
    a = parse_jd(JobInput(job_description=JD["text"], job_title=JD["title"], company_name=JD["company"]), catalog)
    ctx = build_job_context(a, catalog, company_name=JD["company"], job_title=JD["title"])
    return p, ctx, JobFacts(company="Northwind AI", title=JD["title"], skills=JOB_SKILLS)


@pytest.fixture
def resume(env, catalog):
    p, ctx, _ = env
    return build_resume(p, ctx, catalog).model_dump(mode="json")


def check(doc_type, content, env, catalog, **kw):
    p, _, jf = env
    return validate_document(doc_type, content, p, catalog, job=jf, **kw)


def codes(v, severity=None):
    return {i.code for i in v.issues if severity in (None, i.severity)}


# ------------------------------------------------------------------------------------ baseline
def test_untouched_generated_resume_passes_cleanly(env, catalog, resume):
    v = check("resume", resume, env, catalog)
    assert v.status == "passed" and v.issues == [] and v.claims_checked > 20 and v.content_hash == content_hash(resume)


@pytest.mark.parametrize("jd_id", ["JD01", "JD05", "JD15", "JD19", "JD30"])
def test_generated_documents_for_any_job_never_fail_validation(catalog, jd_id):
    jd = next(j for j in JDS if j["id"] == jd_id)
    p = synthetic_profile()
    a = parse_jd(JobInput(job_description=jd["text"], job_title=jd["title"], company_name=jd.get("company")), catalog)
    ctx = build_job_context(a, catalog, company_name=jd.get("company"), job_title=jd["title"])
    jf = JobFacts(company=jd.get("company"), title=jd["title"], skills={s.name for s in a.skills})
    pack = build_fact_pack(p, ctx, catalog)
    assert validate_document("resume", build_resume(p, ctx, catalog).model_dump(mode="json"), p, catalog, job=jf).status != "failed"
    assert validate_document("cover_letter", compose_cover_letter(pack).model_dump(mode="json"), p, catalog, job=jf).status != "failed"
    sc = compose_screening(pack, Preferences(notice_period="30 days", min_salary=2000000, salary_currency="INR", open_to_relocation=True, work_authorization=["India"]))
    v = validate_document("screening_answers", sc.model_dump(mode="json"), p, catalog, job=jf)
    # answers that still need the candidate's own words (e.g. no duties in the job to base a motivation on) block approval,
    # but a generated answer must never contain an unsupported CLAIM
    assert {i.code for i in v.issues if i.severity == "error"} <= {"input_required"}


# ------------------------------------------------------------------------------------ structured resume facts
def mutate(resume, fn):
    r = copy.deepcopy(resume)
    fn(r)
    return r


@pytest.mark.parametrize("name,fn,code", [
    ("fake employer", lambda r: r["experience"][0].update(company_name="Google"), "unknown_employer"),
    ("inflated title", lambda r: r["experience"][0].update(job_title="Chief AI Officer"), "title_changed"),
    ("changed start date", lambda r: r["experience"][1].update(start_date="2015-01"), "date_changed"),
    ("changed end date", lambda r: r["experience"][1].update(end_date="2023-06"), "date_changed"),
    ("current flag flipped", lambda r: r["experience"][1].update(is_current=True), "date_changed"),
    ("contact changed", lambda r: r["header"].update(email="other@example.com"), "contact_mismatch"),
    ("invented phone", lambda r: r["header"].update(phone="+1 555 0100"), "contact_mismatch"),
    ("headline changed", lambda r: r["header"].update(headline="Principal Architect"), "unsupported_title"),
    ("fake certification", lambda r: r["certifications"].append({"certification_name": "AWS Solutions Architect Professional", "issuing_organization": "Amazon"}), "unsupported_certification"),
    ("fake degree", lambda r: r["education"][0].update(degree="PhD"), "education_mismatch"),
    ("changed grad year", lambda r: r["education"][0].update(graduation_year=2012), "education_mismatch"),
    ("fake institution", lambda r: r["education"][0].update(institution="MIT"), "education_mismatch"),
    ("fake project", lambda r: r["projects"].append({"project_name": "Skynet", "bullets": []}), "unknown_project"),
    ("fake project tech", lambda r: r["projects"][0]["technologies"].append("Kubernetes"), "unsupported_technology"),
    ("fake skill", lambda r: r["skills"][0]["items"].append("Terraform"), "unsupported_skill"),
])
def test_structured_fabrications_are_errors(env, catalog, resume, name, fn, code):
    v = check("resume", mutate(resume, fn), env, catalog)
    assert v.status == "failed" and code in codes(v, "error"), (name, [(i.code, i.severity) for i in v.issues])


def test_reordering_and_dropping_content_is_allowed_but_omitting_a_role_is_warned(env, catalog, resume):
    r = copy.deepcopy(resume)
    r["skills"].reverse()
    r["experience"][0]["bullets"].reverse()
    assert check("resume", r, env, catalog).status == "passed"
    r["experience"].pop()
    v = check("resume", r, env, catalog)
    assert v.status == "passed_with_warnings" and "employment_omitted" in codes(v)


def test_duplicate_role_is_flagged(env, catalog, resume):
    r = copy.deepcopy(resume)
    r["experience"].append(copy.deepcopy(r["experience"][0]))
    assert "duplicate_role" in codes(check("resume", r, env, catalog))


# ------------------------------------------------------------------------------------ bullets and free text
def test_bullet_edits_are_warned_and_fabrications_are_errors(env, catalog, resume):
    ok = mutate(resume, lambda r: r["experience"][0]["bullets"][0].update(text="Built a RAG pipeline with LangChain and MongoDB."))
    v = check("resume", ok, env, catalog)
    assert v.status == "passed_with_warnings" and "edited_wording" in codes(v)

    cases = {
        "fabricated metric": "Reduced inference latency by 80% by migrating models to ONNX.",
        "invented team size": "Led a team of 12 engineers.",
        "unsupported tech": "Built a streaming pipeline using Kafka and Spark.",
        "wrong role tech": "Deployed models with Docker and Kubernetes.",
        "years claim": "Delivered 20 years of production machine learning.",
    }
    for name, text in cases.items():
        v = check("resume", mutate(resume, lambda r, t=text: r["experience"][0]["bullets"].append({"text": t, "source": None})), env, catalog)
        assert v.status == "failed", name


def test_metric_from_the_profile_but_other_role_is_still_supported_text_but_tech_misuse_warned(env, catalog, resume):
    # Python is in the profile and in the Acme role's technologies; "Scikit-learn" is in the profile but not used at Acme
    r = mutate(resume, lambda x: x["experience"][0]["bullets"].append({"text": "Trained models with Scikit-learn.", "source": None}))
    v = check("resume", r, env, catalog)
    assert v.status == "passed_with_warnings" and "skill_not_used_in_role" in codes(v)


def test_dates_do_not_make_metrics_look_supported(env, catalog, resume):
    # '12' only exists as the month of '2021-12'; it must not support a claimed team size
    r = mutate(resume, lambda x: x["experience"][0]["bullets"].append({"text": "Managed a team of 12 engineers.", "source": None}))
    v = check("resume", r, env, catalog)
    assert "unsupported_number" in codes(v, "error")


@pytest.mark.parametrize("summary,code,sev", [
    ("Engineer with 15+ years of experience.", "duration_overstated", "error"),
    ("Engineer with 7+ years of experience in Python.", None, None),
    ("Engineer with 7 years of experience in Kubernetes.", "unsupported_skill", "error"),
    ("Engineer with 9 years of experience with PyTorch.", "duration_overstated", "error"),
    ("Certified AWS Solutions Architect and Python developer.", "unsupported_certification", "error"),
    ("Holds a PhD in machine learning and works with Python.", "unsupported_education", "error"),
    ("I hold a PhD in machine learning.", "unsupported_education", "error"),
    ("Bachelor of Technology graduate in Computer Science.", None, None),      # matches the profile
    ("Experienced Kafka and Java developer.", "unsupported_skill", "error"),
    ("Achieved 99.9% accuracy across production models.", "unsupported_number", "error"),
    ("Spearheaded the migration of the data platform.", "unsupported_responsibility", "warning"),
    ("Worked at Initech on payments.", "unrecognised_organization", "warning"),
    ("Senior ML Engineer at Acme Analytics focused on RAG.", None, None),
])
def test_summary_claims(env, catalog, resume, summary, code, sev):
    v = check("resume", mutate(resume, lambda r: r.update(summary=summary)), env, catalog)
    if code is None:
        assert not [i for i in v.issues if i.field == "summary" and i.severity == "error"], summary
    else:
        assert any(i.code == code and i.severity == sev and i.field == "summary" for i in v.issues), (summary, [(i.code, i.severity) for i in v.issues])


def test_duration_claims_per_skill(env, catalog, resume):
    ok = check("resume", mutate(resume, lambda r: r.update(summary="Engineer with 3 years of experience in Python.")), env, catalog)
    assert "duration_overstated" not in codes(ok)
    unprovable = check("resume", mutate(resume, lambda r: r.update(summary="Engineer with 7 years of experience in machine learning.")), env, catalog)
    assert "duration_unverified" in codes(unprovable, "warning") and unprovable.status != "failed"   # plausible but not proven by dated roles
    absurd = check("resume", mutate(resume, lambda r: r.update(summary="Engineer with 12 years of experience in machine learning.")), env, catalog)
    assert "duration_overstated" in codes(absurd, "error")


def test_unknown_total_experience_makes_duration_claims_unverifiable(catalog, env):
    p, ctx, jf = env
    p2 = synthetic_profile()
    p2.summary.total_experience_years = None
    for j in p2.work_experience:
        j.start_date = None
    r = build_resume(p2, ctx, catalog).model_dump(mode="json")
    r["summary"] = "Engineer with 5+ years of experience."
    v = validate_document("resume", r, p2, catalog, job=jf)
    assert "duration_unverified" in codes(v) and v.status != "failed"


def test_jd_skills_mentioned_vs_claimed(env, catalog, resume):
    mention = check("resume", mutate(resume, lambda r: r.update(summary="Seeking a role centred on Kubernetes.")), env, catalog)
    assert "jd_skill_mentioned" in codes(mention, "warning") and "unsupported_skill" not in codes(mention)
    claim = check("resume", mutate(resume, lambda r: r.update(summary="Experienced in Kubernetes and Docker.")), env, catalog)
    assert "unsupported_skill" in codes(claim, "error")


def test_placeholders_block_approval(env, catalog, resume):
    v = check("resume", mutate(resume, lambda r: r.update(summary=f"{PLACEHOLDER}: write a summary]")), env, catalog)
    assert v.status == "failed" and "input_required" in codes(v, "error")


def test_malformed_content_is_an_error_not_an_exception(env, catalog):
    v = check("resume", {"experience": "not a list"}, env, catalog)
    assert v.status == "failed" and codes(v) == {"malformed_content"}
    assert check("cover_letter", {"paragraphs": [1, 2]}, env, catalog).status == "failed"


# ------------------------------------------------------------------------------------ cover letter
@pytest.fixture
def letter(env, catalog):
    p, ctx, _ = env
    return compose_cover_letter(build_fact_pack(p, ctx, catalog)).model_dump(mode="json")


def test_letter_checks(env, catalog, letter):
    assert check("cover_letter", letter, env, catalog).status in ("passed", "passed_with_warnings")
    bad_sig = mutate(letter, lambda l: l.update(signature="John Smith"))
    assert "signature_mismatch" in codes(check("cover_letter", bad_sig, env, catalog), "error")
    lie = mutate(letter, lambda l: l["paragraphs"].append({"text": "I have hands-on experience with Kubernetes and Java.", "sources": []}))
    assert "unsupported_skill" in codes(check("cover_letter", lie, env, catalog), "error")
    inflate = mutate(letter, lambda l: l["paragraphs"].append({"text": "I bring 25 years of experience.", "sources": []}))
    assert "duration_overstated" in codes(check("cover_letter", inflate, env, catalog), "error")
    empty = mutate(letter, lambda l: l.update(paragraphs=[]))
    assert "empty_document" in codes(check("cover_letter", empty, env, catalog), "error")
    nameless = mutate(letter, lambda l: l.update(signature=f"{PLACEHOLDER}: your name]"))
    assert "input_required" in codes(check("cover_letter", nameless, env, catalog), "error")


def test_letter_mentioning_the_job_s_own_stack_is_only_a_warning_when_not_claimed(env, catalog, letter):
    r = mutate(letter, lambda l: l["paragraphs"].append({"text": "The team works with Kubernetes and Java every day.", "sources": []}))
    v = check("cover_letter", r, env, catalog)
    assert "jd_skill_mentioned" in codes(v, "warning") and v.status == "passed_with_warnings"


# ------------------------------------------------------------------------------------ screening answers
def test_screening_checks(env, catalog):
    p, ctx, jf = env
    pack = build_fact_pack(p, ctx, catalog)
    sc = compose_screening(pack, Preferences()).model_dump(mode="json")
    v = check("screening_answers", sc, env, catalog)
    assert v.status == "failed" and "input_required" in codes(v, "error")                # placeholders still present

    full = compose_screening(pack, Preferences(notice_period="30 days", min_salary=2000000, salary_currency="INR", open_to_relocation=True,
                                               work_authorization=["India"])).model_dump(mode="json")
    assert check("screening_answers", full, env, catalog).status != "failed"

    def set_answer(content, qid, text):
        c = copy.deepcopy(content)
        next(a for a in c["answers"] if a["question_id"] == qid).update(answer=text, status="answered")
        return c

    # personal details must match what the candidate provided
    assert "unsupported_personal_detail" in codes(check("screening_answers", set_answer(full, "notice_period", "My notice period is 90 days."), env, catalog), "error")
    assert "unsupported_personal_detail" in codes(check("screening_answers", set_answer(full, "salary_expectation", "I expect INR 5,000,000 per year."), env, catalog), "error")
    # an answer for a personal detail nobody provided is fabrication
    blank = set_answer(sc, "notice_period", "My notice period is 15 days.")
    assert "unsupported_personal_detail" in codes(check("screening_answers", blank, env, catalog), "error")
    # ordinary answers are scanned like any other text
    assert "unsupported_skill" in codes(check("screening_answers", set_answer(full, "strongest_skills", "I am an expert in Kafka and Spark."), env, catalog), "error")


def test_candidate_inputs_stored_in_the_document_are_honoured(env, catalog):
    p, ctx, jf = env
    pack = build_fact_pack(p, ctx, catalog)
    sc = compose_screening(pack, Preferences(), candidate_inputs={"notice_period": "45 days"}).model_dump(mode="json")
    v = check("screening_answers", sc, env, catalog)
    assert not any(i.field == "answers[6]" for i in v.issues)   # notice period answer matches the stored input


def test_validation_counts_claims_and_hashes_content(env, catalog, resume):
    v = check("resume", resume, env, catalog)
    assert v.claims_checked > 0 and v.content_hash == content_hash(resume) and v.validated_at
    changed = mutate(resume, lambda r: r.update(summary=r["summary"] + " Extra."))
    assert content_hash(changed) != v.content_hash
