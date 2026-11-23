"""Resume customisation, cover letters, screening answers, AI writer fallbacks. Synthetic candidate + sample JDs only."""
import json

import pytest

from ai.schemas.candidate_profile import CandidateProfile, ProfessionalSummary, Project, WorkExperience
from ai.schemas.document_schema import PLACEHOLDER, ResumeContent, parse_content
from ai.schemas.job_schema import JobInput
from ai.validators.document_validator import JobFacts, validate_document
from config.scoring_config import Preferences
from engines.generation import ai_writer, document_engine
from engines.generation.cover_letter import compose_cover_letter, first_person
from engines.generation.fact_pack import build_fact_pack
from engines.generation.resume_customizer import build_job_context, skill_relevance, text_relevance
from engines.generation.screening_answers import DEFAULT_QUESTIONS, compose_screening, effective_inputs
from engines.matching.jd_parser import parse_jd
from engines.resume.resume_builder import ResumeOptions, build_resume, compose_summary
from tests.fixtures.candidate import synthetic_profile
from tests.fixtures.fake_ai import make_client
from tests.fixtures.jd_dataset import JDS

JD = {j["id"]: j for j in JDS}


def ctx_for(jd_id, catalog):
    jd = JD[jd_id]
    a = parse_jd(JobInput(job_description=jd["text"], job_title=jd["title"], company_name=jd.get("company")), catalog)
    return a, build_job_context(a, catalog, company_name=jd.get("company"), job_title=jd["title"])


def profile_texts(p: CandidateProfile) -> set[str]:
    out = set()
    for j in p.work_experience:
        out |= {*j.responsibilities, *j.achievements}
        out.add(j.description or "")
    for pr in p.projects:
        out |= {*pr.responsibilities, *pr.achievements, pr.description or ""}
    out |= set(p.achievements)
    return out


# ============================================================ resume customisation
def test_every_role_is_kept_with_original_title_company_and_dates(catalog):
    p = synthetic_profile()
    _, ctx = ctx_for("JD15", catalog)  # a data-analyst JD: unrelated to most of the work
    r = build_resume(p, ctx, catalog)
    assert [(e.company_name, e.job_title, e.start_date, e.end_date, e.is_current) for e in r.experience] == [
        ("Acme Analytics", "Senior ML Engineer", "2022-01", None, True), ("Beta Labs", "Data Scientist", "2019-06", "2021-12", False)]
    assert r.education[0].degree == "Bachelor of Technology" and r.education[0].graduation_year == 2019
    assert [c.certification_name for c in r.certifications] == ["Azure AI Engineer Associate"]


def test_bullets_are_verbatim_and_never_invented(catalog):
    p = synthetic_profile()
    texts = profile_texts(p)
    for jd_id in ("JD01", "JD06", "JD15", "JD19", "JD30"):
        r = build_resume(p, ctx_for(jd_id, catalog)[1], catalog)
        for e in r.experience:
            assert all(b.text in texts for b in e.bullets)
        for pr in r.projects:
            assert all(b.text in texts for b in pr.bullets)
        assert all(a.text in texts for a in r.achievements)


def test_skills_are_a_reordering_of_the_profile_only(catalog):
    p = synthetic_profile()
    own = {s for _, s in p.technical_skills.all_skills()}
    for jd_id in ("JD01", "JD19", "JD30"):
        r = build_resume(p, ctx_for(jd_id, catalog)[1], catalog)
        shown = {s for g in r.skills for s in g.items}
        assert shown <= own and shown == own  # nothing added, nothing silently dropped at the default cap


def test_relevant_skills_and_groups_come_first(catalog):
    p = synthetic_profile()
    r = build_resume(p, ctx_for("JD01", catalog)[1], catalog)  # wants Python, PyTorch, RAG, LangChain, MongoDB ...
    first = r.skills[0]
    assert first.label in ("Deep Learning", "Generative AI", "LLM Frameworks", "Programming Languages")
    flat = [s for g in r.skills for s in g.items]
    assert flat.index("PyTorch") < flat.index("TensorFlow") and flat.index("MongoDB") < flat.index("PostgreSQL")
    assert flat.index("LangChain") < flat.index("Git")
    other = build_resume(p, ctx_for("JD05", catalog)[1], catalog)  # python/fastapi/mongodb/git job: different emphasis
    assert [g.label for g in other.skills] != [g.label for g in r.skills] or [s for g in other.skills for s in g.items] != flat


def test_relevant_bullets_are_selected_and_ordered_for_the_job(catalog):
    p = synthetic_profile()
    rag = build_resume(p, ctx_for("JD01", catalog)[1], catalog, ResumeOptions(bullets_recent=1))
    assert "RAG pipeline" in rag.experience[0].bullets[0].text            # RAG/LangChain job -> the RAG bullet leads
    sales = build_resume(p, ctx_for("JD15", catalog)[1], catalog, ResumeOptions(bullets_recent=1, recent_roles=2))
    assert "SQL" in sales.experience[1].bullets[0].text or "Analyzed" in sales.experience[1].bullets[0].text


def test_limits_are_respected(catalog):
    p = synthetic_profile()
    r = build_resume(p, ctx_for("JD01", catalog)[1], catalog, ResumeOptions(max_projects=1, recent_roles=1, bullets_recent=2, bullets_older=1, max_achievements=1))
    assert len(r.projects) == 1 and len(r.experience[0].bullets) == 2 and len(r.experience[1].bullets) == 1 and len(r.achievements) <= 1
    assert any("left out" in n for n in r.notes)
    assert build_resume(p, ctx_for("JD01", catalog)[1], catalog, ResumeOptions(max_projects=0)).projects == []


def test_projects_are_prioritised_by_relevance(catalog):
    p = synthetic_profile()
    ml = build_resume(p, ctx_for("JD05", catalog)[1], catalog)       # python/fastapi/mongodb job
    assert ml.projects[0].project_name == "Resume Matcher"
    bi = build_resume(p, ctx_for("JD15", catalog)[1], catalog)       # tableau/sql analyst job
    assert bi.projects[0].project_name == "Churn Dashboard"


def test_evidence_paths_point_at_the_exact_profile_text(catalog):
    p = synthetic_profile()
    r = build_resume(p, ctx_for("JD01", catalog)[1], catalog)
    for e in r.experience:
        for b in e.bullets:
            assert eval("p." + b.source.replace("work_experience", "work_experience")) == b.text


def test_summary_is_made_only_of_verified_facts(catalog):
    p = synthetic_profile()
    _, ctx = ctx_for("JD01", catalog)
    text, sources = compose_summary(p, ctx, catalog)
    assert "7+ years" in text and "Senior ML Engineer" in text and "Acme Analytics" in text
    assert "7.4" not in text and "8+" not in text                       # years are never rounded up
    assert sources and any(s.startswith("summary.") for s in sources)
    assert validate_document("resume", build_resume(p, ctx, catalog).model_dump(), p, catalog,
                             job=JobFacts(skills=set(ctx.must | ctx.preferred))).status == "passed"


def test_summary_mentions_only_job_matching_skills_the_candidate_has(catalog):
    p = synthetic_profile()
    _, ctx = ctx_for("JD19", catalog)  # Java/Spring/Kafka job: the candidate has none of them
    text, _ = compose_summary(p, ctx, catalog)
    for missing in ("Java", "Spring Boot", "Kafka", "Microservices"):
        assert missing not in text


def test_unknown_years_and_title_are_not_invented(catalog):
    p = synthetic_profile(summary=ProfessionalSummary(), work_experience=[], projects=[])
    text, _ = compose_summary(p, ctx_for("JD01", catalog)[1], catalog)
    assert "year" not in text and "Senior" not in text and "ML Engineer" not in text
    assert text.startswith("Skilled in")


def test_sub_one_year_is_not_claimed_as_years(catalog):
    p = synthetic_profile(summary=ProfessionalSummary(current_job_title="Data Analyst", total_experience_years=0.6))
    text, _ = compose_summary(p, ctx_for("JD15", catalog)[1], catalog)
    assert "year" not in text and "Data Analyst" in text


def test_empty_profile_gives_an_empty_summary_not_a_made_up_one(catalog):
    p = synthetic_profile(summary=ProfessionalSummary(), technical_skills=type(synthetic_profile().technical_skills)(), work_experience=[],
                          projects=[], education=[], certifications=[])
    r = build_resume(p, ctx_for("JD01", catalog)[1], catalog)
    assert r.summary == "" and r.experience == [] and r.skills == [] and any("No summary" in n for n in r.notes)


def test_candidates_own_summary_is_used_when_nothing_can_be_composed(catalog):
    p = synthetic_profile(summary=ProfessionalSummary(professional_summary="My own words."), technical_skills=type(synthetic_profile().technical_skills)(),
                          work_experience=[])
    assert build_resume(p, ctx_for("JD01", catalog)[1], catalog).summary == "My own words."


def test_roles_without_dates_keep_unknown_dates(catalog):
    p = synthetic_profile(work_experience=[WorkExperience(company_name="X Corp", job_title="Dev", responsibilities=["Built things with Python."])])
    r = build_resume(p, ctx_for("JD01", catalog)[1], catalog)
    assert (r.experience[0].start_date, r.experience[0].end_date) == (None, None)


def test_contact_header_comes_only_from_the_profile(catalog):
    p = synthetic_profile()
    h = build_resume(p, ctx_for("JD01", catalog)[1], catalog).header
    assert (h.full_name, h.email, h.location, h.phone, h.linkedin_url) == ("Jane Sample", "jane.sample@example.com", "Pune, India", None, None)


def test_relevance_helpers(catalog):
    _, ctx = ctx_for("JD01", catalog)
    assert skill_relevance("Python", ctx, catalog) == 1.0 and skill_relevance("Docker", ctx, catalog) == 0.7
    assert skill_relevance("Terraform", ctx, catalog) == 0.0
    assert 0 < skill_relevance("TensorFlow", ctx, catalog) < 0.5          # related to PyTorch: partial only
    assert text_relevance("Built a RAG pipeline using LangChain", ctx, catalog) > text_relevance("Organised the office party", ctx, catalog) == 0


# ============================================================ cover letter
def pack_for(jd_id, catalog, p=None):
    _, ctx = ctx_for(jd_id, catalog)
    return build_fact_pack(p or synthetic_profile(), ctx, catalog), ctx


def test_cover_letter_is_specific_and_grounded(catalog):
    pack, ctx = pack_for("JD01", catalog)
    c = compose_cover_letter(pack)
    text = " ".join(p.text for p in c.paragraphs)
    assert "Northwind AI" in text and "Senior Machine Learning Engineer" in text and c.signature == "Jane Sample"
    assert "RAG pipeline" in text and "7+ years" in text and "Sample Institute of Technology" in text
    assert c.paragraphs[-1].text.startswith("I would welcome") and all(p.sources or p is c.paragraphs[-1] for p in c.paragraphs)
    v = validate_document("cover_letter", c.model_dump(), synthetic_profile(), catalog, job=JobFacts(company="Northwind AI", skills=ctx.must | ctx.preferred))
    assert v.status in ("passed", "passed_with_warnings") and not v.errors if hasattr(v, "errors") else v.status != "failed"


def test_letters_differ_between_jobs(catalog):
    a = " ".join(p.text for p in compose_cover_letter(pack_for("JD01", catalog)[0]).paragraphs)
    b = " ".join(p.text for p in compose_cover_letter(pack_for("JD15", catalog)[0]).paragraphs)
    assert a != b and "Northwind AI" not in b and "Retail Insights" in b


def test_letter_never_claims_skills_the_candidate_lacks(catalog):
    pack, _ = pack_for("JD19", catalog)  # Java/Spring/Kafka
    text = " ".join(p.text for p in compose_cover_letter(pack).paragraphs).replace(pack.job_title, "")  # the target title is not a claim
    for s in ("Java", "Spring", "Kafka", "Microservices", "Terraform"):
        assert s not in text


def test_letter_handles_unknown_company_title_and_name(catalog):
    p = synthetic_profile()
    p.contact.full_name = None
    a = parse_jd(JobInput(job_description="Requirements:\n- Python and SQL\n"), catalog)
    pack = build_fact_pack(p, build_job_context(a, catalog), catalog)
    c = compose_cover_letter(pack)
    text = " ".join(x.text for x in c.paragraphs)
    assert "the open position" in text and "your team" in text and PLACEHOLDER in c.signature
    assert any("name" in m.lower() for m in c.missing) and any("Company name is unknown" in m for m in c.missing)


def test_letter_with_no_experience_does_not_make_any_up(catalog):
    p = synthetic_profile(work_experience=[], projects=[], education=[], achievements=[])
    pack, _ = pack_for("JD01", catalog, p)
    c = compose_cover_letter(pack)
    text = " ".join(x.text for x in c.paragraphs)
    assert "In my" not in text and "Previously" not in text and any("No work experience" in m for m in c.missing)


@pytest.mark.parametrize("bullet,expected", [("Built a RAG pipeline.", "I built a RAG pipeline"), ("Led a team.", "I led a team"),
                                             ("Developed models in Python.", "I developed models in Python"),
                                             ("Responsible for deployments.", "my work included: responsible for deployments"),
                                             ("Matches resumes using embeddings.", "my work included: matches resumes using embeddings")])
def test_first_person(bullet, expected):
    assert first_person(bullet) == expected


# ============================================================ screening answers
def answers(catalog, prefs=None, jd="JD01", **kw):
    pack, _ = pack_for(jd, catalog)
    return {a.question_id: a for a in compose_screening(pack, prefs or Preferences(), **kw).answers}


def test_missing_personal_details_are_never_invented(catalog):
    a = answers(catalog)  # no preferences at all
    for qid in ("notice_period", "salary_expectation", "relocation", "work_authorization"):
        assert a[qid].status == "needs_input" and PLACEHOLDER in a[qid].answer and a[qid].note
    assert not any(ch.isdigit() for ch in a["notice_period"].answer + a["relocation"].answer + a["work_authorization"].answer)


def test_provided_details_are_used_verbatim(catalog):
    prefs = Preferences(notice_period="30 days", min_salary=2000000, salary_currency="INR", open_to_relocation=False, work_authorization=["India"])
    a = answers(catalog, prefs)
    assert a["notice_period"].answer == "My notice period is 30 days." and a["notice_period"].status == "answered"
    assert "INR 2,000,000 per year" in a["salary_expectation"].answer
    assert a["relocation"].answer == "I am not looking to relocate." and a["work_authorization"].answer == "I am authorised to work in India."
    b = answers(catalog, prefs, candidate_inputs={"notice_period": "60 days", "relocation": "yes"})  # explicit input wins over preferences
    assert "60 days" in b["notice_period"].answer and b["relocation"].answer == "Yes, I am open to relocating."


def test_candidate_inputs_ignore_unknown_keys():
    assert effective_inputs(Preferences(), {"notice_period": "15 days", "favourite_colour": "blue", "salary_expectation": "  "}) == {"notice_period": "15 days"}


def test_answer_lengths_differ_and_stay_factual(catalog):
    short, medium, long_ = (answers(catalog, length=n)["tell_me_about_yourself"].answer for n in ("short", "medium", "long"))
    assert len(short) < len(medium) < len(long_)
    assert "Acme Analytics" in medium and "Bachelor of Technology" in long_ and "Acme" not in short


def test_motivation_and_project_answers_are_drafts_for_review(catalog):
    a = answers(catalog)
    assert a["why_company"].status == "draft" and "personal reasons" in a["why_company"].note and "Northwind AI" in a["why_company"].answer
    assert a["challenging_project"].status == "draft" and "Resume Matcher" in a["challenging_project"].answer and a["challenging_project"].note


def test_no_projects_or_experience_means_needs_input(catalog):
    pack, _ = pack_for("JD01", catalog, synthetic_profile(projects=[], work_experience=[]))
    a = {x.question_id: x for x in compose_screening(pack, Preferences()).answers}
    assert a["challenging_project"].status == "needs_input" and a["relevant_experience"].status == "needs_input"


def test_custom_questions_are_never_guessed(catalog):
    a = answers(catalog, questions=[{"text": "What is your favourite programming paradigm?"}, {"id": "notice_period", "text": "Notice?"},
                                    {"text": "Tell us about yourself."}])
    assert a["custom_1"].status == "needs_input" and a["notice_period"].status == "needs_input" and a["tell_me_about_yourself"].status == "answered"


def test_default_questions_cover_the_requested_topics():
    ids = {i for i, _ in DEFAULT_QUESTIONS}
    assert {"tell_me_about_yourself", "why_company", "why_suitable", "relevant_experience", "strongest_skills", "challenging_project",
            "notice_period", "salary_expectation", "relocation"} <= ids


def test_fact_pack_for_ai_has_no_contact_details(catalog):
    pack, _ = pack_for("JD01", catalog)
    blob = json.dumps(pack.for_ai())
    for pii in ("Jane", "jane.sample@example.com", "Pune"):
        assert pii not in blob
    assert "Acme Analytics" in blob  # career facts are needed to write, but never the person's name or contact details


# ============================================================ AI wording (fake Claude) with validation fallback
def validator_for(catalog, p, ctx):
    return lambda t, c: validate_document(t, c, p, catalog, job=JobFacts(company="Northwind AI", skills=ctx.must | ctx.preferred))


def test_ai_summary_accepted_when_supported(catalog):
    p, (a, ctx) = synthetic_profile(), ctx_for("JD01", catalog)
    pack = build_fact_pack(p, ctx, catalog)
    ai = make_client([json.dumps({"summary": "Senior ML Engineer with 7+ years of experience building RAG systems with Python and LangChain."})])
    g = document_engine.generate_resume(p, ctx, pack, catalog, validator_for(catalog, p, ctx), ai=ai)
    assert g.generator == "claude+deterministic" and g.ai_model and "RAG systems" in g.content["summary"]


@pytest.mark.parametrize("claim", ["Expert in Kubernetes and Terraform with 12 years of experience.", "Led a team of 40 engineers at Google.",
                                   "Senior ML Engineer, certified AWS Solutions Architect with 15+ years of experience."])
def test_ai_summary_with_unsupported_claims_is_rejected(catalog, claim):
    p, (a, ctx) = synthetic_profile(), ctx_for("JD01", catalog)
    pack = build_fact_pack(p, ctx, catalog)
    g = document_engine.generate_resume(p, ctx, pack, catalog, validator_for(catalog, p, ctx), ai=make_client([json.dumps({"summary": claim})]))
    assert g.generator == "deterministic" and g.ai_model is None and "Kubernetes" not in g.content["summary"] and "40" not in g.content["summary"]
    assert any("rejected" in n for n in g.notes)


@pytest.mark.parametrize("script", [["not json"], [RuntimeError("network down")], ['{"summary": 5}']])
def test_ai_failures_fall_back_without_error(catalog, script):
    p, (a, ctx) = synthetic_profile(), ctx_for("JD01", catalog)
    g = document_engine.generate_resume(p, ctx, build_fact_pack(p, ctx, catalog), catalog, validator_for(catalog, p, ctx), ai=make_client(script))
    assert g.generator == "deterministic" and g.content["summary"].startswith("Senior ML Engineer")
    assert any("unavailable" in n for n in g.notes)


def test_ai_letter_validated_and_fallback(catalog):
    p, (a, ctx) = synthetic_profile(), ctx_for("JD01", catalog)
    pack, validate = build_fact_pack(p, ctx, catalog), validator_for(catalog, p, ctx)
    good = json.dumps({"paragraphs": ["I am applying for the Senior Machine Learning Engineer position at Northwind AI.",
                                      "At Acme Analytics I built a RAG pipeline using LangChain and MongoDB for internal search.",
                                      "I would welcome the chance to talk."]})
    g = document_engine.generate_cover_letter(pack, validate, ai=make_client([good]))
    assert g.generator == "claude+deterministic" and g.content["signature"] == "Jane Sample" and g.content["paragraphs"][0]["sources"] == ["ai"]
    bad = json.dumps({"paragraphs": ["I have 10 years of Kubernetes experience.", "I won a Nobel prize at Google.", "Thanks."]})
    g2 = document_engine.generate_cover_letter(pack, validate, ai=make_client([bad]))
    assert g2.generator == "deterministic" and "Kubernetes" not in json.dumps(g2.content) and any("rejected" in n for n in g2.notes)
    assert document_engine.generate_cover_letter(pack, validate, ai=make_client(["junk"])).generator == "deterministic"


def test_ai_never_writes_personal_details_or_fills_needs_input(catalog):
    p, (a, ctx) = synthetic_profile(), ctx_for("JD01", catalog)
    pack, validate = build_fact_pack(p, ctx, catalog), validator_for(catalog, p, ctx)
    reply = json.dumps({"answers": [{"question_id": "notice_period", "answer": "My notice period is 15 days."},
                                    {"question_id": "salary_expectation", "answer": "I expect 50 lakh."},
                                    {"question_id": "tell_me_about_yourself", "answer": "I am a Senior ML Engineer with 7+ years of experience working with Python."},
                                    {"question_id": "challenging_project", "answer": "I built Resume Matcher using Python and FastAPI."}]})
    g = document_engine.generate_screening(pack, Preferences(), validate, ai=make_client([reply]))
    by = {a["question_id"]: a for a in g.content["answers"]}
    assert by["notice_period"]["status"] == "needs_input" and by["salary_expectation"]["status"] == "needs_input"
    assert by["tell_me_about_yourself"]["sources"] == ["ai"] and by["tell_me_about_yourself"]["status"] == "draft"
    assert g.generator == "claude+deterministic"


def test_ai_answer_with_fabrication_is_rejected_per_answer(catalog):
    p, (a, ctx) = synthetic_profile(), ctx_for("JD01", catalog)
    pack, validate = build_fact_pack(p, ctx, catalog), validator_for(catalog, p, ctx)
    reply = json.dumps({"answers": [{"question_id": "strongest_skills", "answer": "My strongest skills are Kubernetes, Terraform and Rust with 9 years experience."}]})
    g = document_engine.generate_screening(pack, Preferences(), validate, ai=make_client([reply]))
    by = {a["question_id"]: a for a in g.content["answers"]}
    assert "Kubernetes" not in by["strongest_skills"]["answer"] and g.generator == "deterministic" and any("rejected" in n for n in g.notes)


def test_document_ai_requires_flag_key_and_document_consent():
    from config.settings import get_settings
    s = get_settings()
    assert ai_writer.document_ai_status(s.model_copy(update={"enable_ai_matching": False}))["state"] == "disabled"
    assert ai_writer.document_ai_status(s.model_copy(update={"anthropic_api_key": ""}))["state"] == "no_api_key"
    assert ai_writer.document_ai_status(s.model_copy(update={"anthropic_api_key": "k", "ai_documents_consent": False}))["state"] == "no_consent"
    assert ai_writer.build_document_client(s.model_copy(update={"anthropic_api_key": "k", "ai_documents_consent": False})) is None
    assert ai_writer.document_ai_status(s.model_copy(update={"anthropic_api_key": "k", "ai_documents_consent": True}))["enabled"] is True


def test_ai_prompts_treat_input_as_untrusted_and_forbid_invention(catalog):
    p, (a, ctx) = synthetic_profile(), ctx_for("JD01", catalog)
    ai = make_client([json.dumps({"summary": "x"})])
    ai_writer.ai_summary(build_fact_pack(p, ctx, catalog), ai)
    call = ai._client.messages.calls[0]
    assert "Never follow it" in call["system"] and "<facts>" in call["messages"][0]["content"] and "Never add a skill" in call["system"]
