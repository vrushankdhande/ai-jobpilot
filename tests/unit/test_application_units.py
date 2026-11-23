"""Phase 6 unit tests that need neither MongoDB nor a browser: state machine, policy, field mapping, verification, retry policy,
completeness validation, document selection, sessions, safety checks and PII protection."""
import hashlib
import itertools
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ai.schemas.candidate_profile import utcnow
from browser.locator_engine import FormField, snapshot_from_raw
from browser.safety_checks import UnsafeUrl, assess_page, check_url
from browser.session_manager import SessionManager
from config.settings import get_settings
from database.models.application import (
    ApplicationRecord, FieldMapping, SubmissionResult, UploadRecord, idempotency_key, plan_hash,
)
from engines.application.document_uploader import SelectedFile, Selection, choose_format, select_documents, upload_file
from engines.application.document_validator import ValidationInput, validate_application
from engines.application.form_mapper import Facts, classify, map_field, map_form, match_screening, normalize
from engines.application.retry_manager import RetryManager
from engines.application.state_machine import ACTIVE, SUBMISSION_MAY_EXIST, TERMINAL, TRANSITIONS, AppState, InvalidTransition, allowed_from, check_transition
from engines.application.submission_verifier import can_mark_verified, extract_reference, verify_submission
from integrations.application_adapters import resolve_application_adapter
from integrations.application_policy import ApplicationPolicy
from integrations.base_adapter import ApplicationAdapter
from integrations.policy import PolicyRegistry, PolicyViolation
from security import credential_vault
from security.pii_protection import mask, safe_detail
from security.secrets_manager import SecretUnavailable, derive_fernet_key
from tests.fixtures.candidate import synthetic_profile

S = AppState


# ============================================================================================== state machine
ALL_PAIRS = list(itertools.product(AppState, AppState))


@pytest.mark.parametrize("src,dst", ALL_PAIRS, ids=[f"{a.value}->{b.value}" for a, b in ALL_PAIRS])
def test_every_state_pair_is_accepted_or_rejected_exactly_as_declared(src, dst):
    if dst in TRANSITIONS[src]:
        assert check_transition(src, dst) == dst
    else:
        with pytest.raises(InvalidTransition):
            check_transition(src, dst)


def test_state_machine_invariants():
    assert TRANSITIONS[S.VERIFIED] == frozenset() and TERMINAL == {S.VERIFIED}
    sources = {dst: {src for src in S if dst in TRANSITIONS[src]} for dst in S}
    assert sources[S.SUBMITTING] == {S.APPROVED}                                    # the only way to start submitting is an approval
    assert sources[S.APPROVED] == {S.READY_FOR_REVIEW}                              # approval only from review
    assert sources[S.SUBMITTED] == {S.SUBMITTING, S.VERIFICATION_PENDING, S.MANUAL_HANDOFF_REQUIRED}
    assert sources[S.VERIFIED] == {S.SUBMITTED, S.VERIFICATION_PENDING}             # never straight from a click or from review
    assert S.SUBMITTING not in sources[S.SUBMITTING] and S.SUBMITTING not in TRANSITIONS[S.SUBMITTED]
    assert S.VERIFICATION_PENDING not in TRANSITIONS[S.SUBMISSION_FAILED] and S.APPROVED not in TRANSITIONS[S.SUBMISSION_FAILED]
    assert S.SUBMITTING not in TRANSITIONS[S.VERIFICATION_PENDING]                  # an uncertain submission is never re-submitted
    assert S.SUBMITTING not in TRANSITIONS[S.MANUAL_HANDOFF_REQUIRED] and S.APPROVED not in TRANSITIONS[S.MANUAL_HANDOFF_REQUIRED]   # restricted: no automated submit
    assert {S.SUBMITTING, S.SUBMITTED, S.VERIFICATION_PENDING, S.VERIFIED} <= SUBMISSION_MAY_EXIST and S.SUBMISSION_FAILED not in ACTIVE
    assert "SUBMITTING" in allowed_from("APPROVED") and allowed_from("VERIFIED") == []
    for bad in ("nope", None, 5):
        with pytest.raises(InvalidTransition):
            check_transition(S.DISCOVERED, bad)


def test_the_spec_state_names_exist():
    assert {s.value for s in S} == {"DISCOVERED", "READY_FOR_REVIEW", "AWAITING_USER_INPUT", "APPROVED", "SUBMITTING", "SUBMITTED", "SUBMISSION_FAILED",
                                    "VERIFICATION_PENDING", "VERIFIED", "MANUAL_HANDOFF_REQUIRED"}


# ============================================================================================== application policy
def policy(**overrides):
    s = get_settings().model_copy(update=overrides)
    return ApplicationPolicy.load(s, PolicyRegistry.load(s))


@pytest.mark.parametrize("source,url", [("linkedin", "https://www.linkedin.com/jobs/view/1"), ("indeed", "https://www.indeed.com/viewjob?jk=1"),
                                        ("naukri", "https://www.naukri.com/x"), ("manual", None), ("unknown_source", "https://x.com/a")])
def test_manual_only_platforms_never_allow_automation_even_when_everything_is_switched_on(source, url):
    p = policy(application_automation_enabled=True, allow_local_test_forms=True)
    d = p.decide(source, None, url)
    assert d.automation_allowed is False and d.handoff_allowed is True


def test_default_configuration_automates_nothing():
    p = policy()
    for src, prov, url in [("career_pages", "greenhouse", "https://boards.greenhouse.io/a/jobs/1"), ("career_pages", "lever", "https://jobs.lever.co/a/b"),
                           ("career_pages", "workday", "https://a.myworkdayjobs.com/x"), ("career_pages", "generic_page", "https://careers.a.com/j"),
                           ("local_test", None, "http://127.0.0.1:1/x")]:
        assert p.decide(src, prov, url).automation_allowed is False


def test_greenhouse_lever_need_the_master_switch_and_an_authorised_site():
    p = policy(application_automation_enabled=True)
    assert not p.decide("career_pages", "greenhouse", "https://boards.greenhouse.io/acme/jobs/1").automation_allowed        # not authorised
    p._data["authorized_sites"] = [{"host": "jobs.lever.co", "path_prefix": "/acme", "provider": "lever"}]
    assert p.decide("career_pages", "lever", "https://jobs.lever.co/acme/123/apply").automation_allowed
    assert not p.decide("career_pages", "lever", "https://jobs.lever.co/other/1").automation_allowed
    assert not p.decide("career_pages", "greenhouse", "https://jobs.lever.co/acme/123").automation_allowed                  # wrong provider for that entry
    assert not p.decide("career_pages", "lever", "https://10.0.0.5/acme").automation_allowed and not p.decide("career_pages", "lever", "http://jobs.lever.co/acme/1").automation_allowed
    assert not policy(application_automation_enabled=False).decide("career_pages", "lever", "https://jobs.lever.co/acme/1").automation_allowed


def test_workday_is_manual_only_regardless_of_authorisation():
    p = policy(application_automation_enabled=True)
    p._data["authorized_sites"] = [{"host": "acme.wd1.myworkdayjobs.com"}]
    assert not p.decide("career_pages", "workday", "https://acme.wd1.myworkdayjobs.com/x").automation_allowed
    ad = resolve_application_adapter({"source": "career_pages", "job_url": "https://acme.wd1.myworkdayjobs.com/x/job/1"}, p)
    assert ad.automation_supported is False
    with pytest.raises(PolicyViolation):
        ad.require_automation()


def test_local_test_provider_only_with_flag_and_only_on_loopback_http():
    assert policy(application_automation_enabled=True, allow_local_test_forms=True).decide("local_test", None, "http://127.0.0.1:5000/f").automation_allowed
    p = policy(application_automation_enabled=True, allow_local_test_forms=True)
    for bad in ("https://example.com/f", "http://192.168.1.2/f", "http://evil.com/f", "http://127.0.0.2/f"):
        assert not p.decide("local_test", None, bad).automation_allowed, bad
    assert not policy(application_automation_enabled=True).decide("local_test", None, "http://127.0.0.1:5000/f").automation_allowed


def test_garbled_or_missing_policy_file_fails_closed(tmp_path):
    s = get_settings().model_copy(update={"application_automation_enabled": True, "allow_local_test_forms": True})
    bad = tmp_path / "p.yaml"
    bad.write_text("- just\n- a list\n", encoding="utf-8")
    assert not ApplicationPolicy.load(s, PolicyRegistry.load(s), path=bad).decide("local_test", None, "http://127.0.0.1:1/").automation_allowed
    assert not ApplicationPolicy.load(s, PolicyRegistry.load(s), path=tmp_path / "missing.yaml").decide("local_test", None, "http://127.0.0.1:1/").automation_allowed
    weird = ApplicationPolicy({"local_test": {"application_status": "whatever"}}, PolicyRegistry.load(s), s)
    assert not weird.decide("local_test", None, "http://127.0.0.1:1/").automation_allowed


def test_adapter_resolution_by_source_and_url():
    p = policy()
    cases = {("career_pages", "https://boards.greenhouse.io/a/jobs/1"): "greenhouse", ("career_pages", "https://jobs.lever.co/a/b"): "lever",
             ("career_pages", "https://a.myworkdayjobs.com/x"): "workday", ("career_pages", "https://careers.a.com/j"): "generic_page",
             ("linkedin", "https://www.linkedin.com/jobs/1"): "linkedin", ("manual_paste", None): "manual", ("local_test", "http://127.0.0.1:1/"): "local_test"}
    for (src, url), prov in cases.items():
        assert resolve_application_adapter({"source": src, "job_url": url}, p).provider == prov
    lever = resolve_application_adapter({"source": "career_pages", "job_url": "https://jobs.lever.co/a/b"}, p)
    assert lever.application_url("https://jobs.lever.co/a/b?x=1") == "https://jobs.lever.co/a/b/apply"
    assert isinstance(lever, ApplicationAdapter) and lever.metadata()["automation_allowed"] is False


# ============================================================================================== field mapping
PROFILE = synthetic_profile()


def facts(**kw):
    return Facts(profile=kw.pop("profile", PROFILE), **kw)


def F(label, kind="text", **kw):
    return FormField(id=kw.pop("id", "0:0"), kind=kind, label=label, **kw)


@pytest.mark.parametrize("label,canonical", [
    ("E-mail", "email"), ("Email Address *", "email"), ("Contact email", "email"), ("Mobile", "phone"), ("Phone number", "phone"), ("Contact number", "phone"),
    ("First Name", "first_name"), ("Given name", "first_name"), ("Last Name", "last_name"), ("Surname", "last_name"), ("Full name", "full_name"), ("Your name", "full_name"),
    ("LinkedIn URL", "linkedin_url"), ("LinkedIn profile", "linkedin_url"), ("GitHub", "github_url"), ("Portfolio / Website", "portfolio_url"),
    ("Current company", "current_company"), ("Current employer", "current_company"), ("Current job title", "current_title"), ("Designation", "current_title"),
    ("Total years of experience", "years_experience"), ("Technical skills", "skills"), ("Key skills", "skills"), ("Work experience", "work_history"),
    ("Notice period", "notice_period"), ("How soon can you join?", "notice_period"), ("Expected CTC", "salary_expectation"), ("Salary expectations", "salary_expectation"),
    ("Are you willing to relocate?", "relocation"), ("Are you legally authorised to work in India?", "work_authorization"), ("Right to work", "work_authorization"),
    ("Gender", "demographic"), ("Veteran status", "demographic"), ("Disability status", "demographic"), ("Do you require visa sponsorship?", "sponsorship"),
    ("Current location", "location"), ("City", "location"), ("Cover letter", "cover_letter_text"),
])
def test_label_variants_are_normalised_to_the_right_canonical_field(label, canonical):
    got, conf, _ = classify(F(label))
    assert got == canonical and conf in ("high", "medium")


def test_consent_only_applies_to_checkboxes_and_confirm_email_is_not_a_consent():
    assert classify(F("I agree to the privacy policy", "checkbox"))[0] == "consent"
    assert classify(F("Confirm email", "email"))[0] == "email"
    assert classify(F("Resume/CV", "file"))[0] == "resume_upload" and classify(F("Cover letter", "file"))[0] == "cover_letter_upload"
    assert classify(F("Transcript", "file"))[0] is None


def test_profile_values_come_with_their_source_and_missing_values_are_never_invented():
    m = map_field(F("Email", "email", required=True), facts())
    assert (m.value, m.source, m.status) == ("jane.sample@example.com", "contact.email", "ready")
    no_phone = map_field(F("Phone", "tel", required=True), facts())
    assert no_phone.value is None and no_phone.status == "needs_input"                       # profile has no phone: required -> asks, never guesses
    assert map_field(F("Phone", "tel"), facts()).status == "optional_skipped"                # optional -> left blank
    assert map_field(F("LinkedIn"), facts()).value is None


def test_derived_values_are_flagged_for_review():
    first = map_field(F("First name"), facts())
    last = map_field(F("Last name"), facts())
    assert (first.value, last.value) == ("Jane", "Sample") and first.derived and last.derived and "check" in (first.note or "").lower()
    one_name = PROFILE.model_copy(deep=True)
    one_name.contact.full_name = "Madonna"
    assert map_field(F("First name", required=True), facts(profile=one_name)).status == "needs_input"


def test_sensitive_and_legal_fields_are_never_auto_filled():
    for label, kind in [("Gender", "select"), ("Do you require visa sponsorship?", "radio"), ("I agree to the terms and conditions", "checkbox"), ("Veteran status", "select")]:
        req = map_field(F(label, kind, required=True, options=["Yes", "No", "Male", "Female"]), facts(personal={"work_authorization": "Yes"}))
        assert req.status == "manual_required" and req.value is None and req.sensitive, label
        opt = map_field(F(label, kind, options=["Yes", "No"]), facts())
        assert opt.status == "optional_skipped" and opt.value is None, label
    # ... unless the candidate answers this exact field themselves
    typed = map_field(F("Gender", "select", options=["Male", "Female", "Prefer not to say"]), facts(candidate_inputs={"gender": "Prefer not to say"}))
    assert typed.value == "Prefer not to say" and typed.source == "candidate_input" and typed.status == "ready"
    assert map_field(F("Gender", "select", options=["Male", "Female"]), facts(candidate_inputs={"gender": "Other"})).status == "needs_input"


def test_salary_notice_authorisation_relocation_only_from_provided_values():
    f0 = facts()
    for label in ("Expected salary", "Notice period", "Are you authorised to work in India?", "Are you willing to relocate?"):
        m = map_field(F(label, required=True), f0)
        assert m.status == "needs_input" and m.value is None and m.source is None, label
    given = facts(personal={"notice_period": "30 days", "salary_expectation": "INR 3,000,000 per year", "relocation": "yes", "work_authorization": "Yes, citizen"})
    assert map_field(F("Notice period"), given).value == "30 days" and map_field(F("Expected salary"), given).value.startswith("INR")
    assert map_field(F("Relocate?", "radio", options=["Yes", "No"]), given).value == "Yes"
    assert map_field(F("Are you authorised to work?", "select", options=["Select", "Yes", "No"]), given).value == "Yes"


def test_dropdowns_radios_and_checkboxes_never_guess_an_option():
    given = facts(personal={"relocation": "maybe later"})
    assert map_field(F("Are you willing to relocate?", "select", required=True, options=["Select", "Yes", "No"]), given).status == "needs_input"
    amb = facts(personal={"relocation": "yes"})
    assert map_field(F("Relocate?", "select", options=["Yes - within India", "Yes - abroad"]), amb).status == "needs_input"      # ambiguous: two matches
    loc = map_field(F("Country", "select", options=["India", "Indonesia"]), facts(candidate_inputs={"country": "india"}))
    assert loc.value == "India" and loc.status == "ready"
    cb = map_field(F("Subscribe to newsletter", "checkbox"), facts())
    assert cb.status == "optional_skipped" and cb.value is None                                  # unknown optional checkbox: never ticked


def test_unknown_questions_are_flagged_not_answered():
    q = map_field(F("What is your favourite colour?", required=True), facts())
    assert q.status == "needs_input" and q.value is None and q.canonical is None
    assert map_field(F("Anything else?"), facts()).status == "optional_skipped"


def test_screening_answers_are_matched_and_reworded_questions_are_flagged():
    answers = {"tell_me_about_yourself": {"question": "Tell us about yourself.", "answer": "I am an ML engineer.", "status": "answered"},
               "why_company": {"question": "Why do you want to join our company?", "answer": "Because.", "status": "draft"}}
    exact = map_field(F("Tell us about yourself"), facts(answers=answers))
    assert exact.value == "I am an ML engineer." and exact.source == "screening_answers.tell_me_about_yourself" and exact.confidence == "high" and exact.note is None
    reworded = map_field(F("Please introduce yourself briefly", "textarea"), facts(answers=answers))
    assert reworded.source == "screening_answers.tell_me_about_yourself" and reworded.confidence == "medium" and "check" in reworded.note.lower()
    assert map_field(F("Why us?", "textarea"), facts(answers=answers)).source == "screening_answers.why_company"
    unrelated = map_field(F("Describe your experience with Kubernetes at scale", "textarea", required=True), facts(answers=answers))
    assert unrelated.status == "needs_input" and unrelated.value is None
    assert match_screening("", answers) == (None, "none")


def test_candidate_input_overrides_and_is_marked_as_such():
    m = map_field(F("Favourite colour", required=True), facts(candidate_inputs={normalize("Favourite colour"): "Blue"}))
    assert m.value == "Blue" and m.source == "candidate_input" and m.status == "ready"
    assert map_field(F("Favourite colour", required=True, id="0:4"), facts(candidate_inputs={"0:4": "Green"})).value == "Green"


def test_unidentified_fields_and_passwords_are_never_filled():
    assert map_field(FormField(id="0:1", kind="text", name="x1", identified=False, required=True), facts()).status == "manual_required"
    pw = map_field(F("Password", "password", required=True), facts())
    assert pw.status == "manual_required" and pw.value is None


def test_upload_fields_only_map_to_approved_documents():
    r = map_field(F("Resume/CV", "file", required=True), facts())
    assert r.canonical == "resume_upload" and r.source == "approved document"
    other = map_field(F("Transcript", "file", required=True), facts())
    assert other.status == "manual_required" and map_field(F("Transcript", "file"), facts()).status == "optional_skipped"


def test_map_form_keeps_one_mapping_per_field_in_order():
    fs = [F("Email", "email", id="0:0"), F("Full name", id="0:1"), F("Gender", "select", id="0:2", options=["M", "F"])]
    assert [m.field_id for m in map_form(fs, facts())] == ["0:0", "0:1", "0:2"]


# ============================================================================================== verification
class _Ad(ApplicationAdapter):
    provider = "t"
    source = "t"


AD = _Ad(None)


def test_only_a_confirmation_with_the_form_gone_is_confirmed():
    ok = verify_submission(text="Thank you for applying! Your application has been submitted. Application reference: APP-77", url="https://x.com/confirmation",
                           form_still_present=False, errors=[], adapter=AD)
    assert ok.outcome == "confirmed" and ok.reference == "APP-77" and {"confirmation message", "application form no longer shown", "confirmation page URL", "reference number"} <= set(ok.evidence)
    msg_but_form = verify_submission(text="Application submitted", url="https://x.com/a", form_still_present=True, errors=[], adapter=AD)
    assert msg_but_form.outcome == "unconfirmed"                           # confirmation-like words next to the live form prove nothing


@pytest.mark.parametrize("kw,expected", [
    (dict(text="", url=None, form_still_present=True, errors=[]), "unconfirmed"),                           # nothing happened / unknown
    (dict(text="Welcome", url=None, form_still_present=False, errors=[]), "unconfirmed"),                    # form vanished, no message
    (dict(text="x", url=None, form_still_present=True, errors=["Email is required"]), "failed"),             # rejected by the site
    (dict(text="Thank you for applying", url=None, form_still_present=False, errors=[], captcha=True), "unconfirmed"),
    (dict(text="Thank you for applying", url=None, form_still_present=False, errors=[], navigation_error=True), "unconfirmed"),
])
def test_ambiguous_or_error_outcomes_are_never_confirmed(kw, expected):
    assert verify_submission(adapter=AD, **kw).outcome == expected


def test_reference_extraction_ignores_words_without_digits():
    pats = AD.reference_patterns
    assert extract_reference("Application reference: APP-2026-0042", pats) == "APP-2026-0042"
    assert extract_reference("Your confirmation number is 884213", pats) == "884213"
    assert extract_reference("Application received: thanks", pats) is None and extract_reference("Reference: received", pats) is None


def test_verified_requires_reliable_evidence():
    page_ref = SubmissionResult(outcome="confirmed", evidence=["confirmation message", "application form no longer shown", "reference number"], reference="A-1", verified_by="page")
    page_noref = SubmissionResult(outcome="confirmed", evidence=["confirmation message"], verified_by="page")
    user_noevidence = SubmissionResult(outcome="user_reported", verified_by="user")
    user_ref = SubmissionResult(outcome="user_reported", reference="R-9", verified_by="user")
    unconfirmed = SubmissionResult(outcome="unconfirmed", reference="A-1")
    assert can_mark_verified(page_ref) and can_mark_verified(user_ref)
    assert not any(can_mark_verified(x) for x in (page_noref, user_noevidence, unconfirmed, SubmissionResult()))


# ============================================================================================== retry policy
def test_retry_decisions():
    rm = RetryManager(None, max_retries=2)
    assert rm.decide(transient=True, submit_clicked=False, attempts=0).allowed and rm.decide(transient=True, submit_clicked=False, attempts=1).allowed
    assert not rm.decide(transient=True, submit_clicked=False, attempts=2).allowed                 # limit
    assert not rm.decide(transient=False, submit_clicked=False, attempts=0).allowed                # not transient
    assert not rm.decide(transient=True, submit_clicked=True, attempts=0).allowed                  # NEVER after the click
    assert rm.may_prepare_again("SUBMISSION_FAILED", "before_submit") and rm.may_prepare_again(S.SUBMISSION_FAILED, "rejected")
    assert not rm.may_prepare_again("SUBMISSION_FAILED", "after_submit") and not rm.may_prepare_again("SUBMISSION_FAILED", None)
    assert not rm.may_prepare_again("VERIFICATION_PENDING", "before_submit")


def test_retry_run_retries_transient_errors_only_before_the_click():
    from browser.browser_manager import BrowserError
    sleeps, calls = [], []
    rm = RetryManager(None, max_retries=2, base_delay=1.0, sleep=sleeps.append)

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise BrowserError("slow", transient=True)
        return "ok"
    assert rm.run(flaky) == "ok" and len(calls) == 3 and sleeps == [1.0, 2.0]                       # bounded exponential back-off
    calls.clear()
    with pytest.raises(BrowserError):
        rm.run(lambda: (calls.append(1), (_ for _ in ()).throw(BrowserError("slow", transient=True)))[1], is_clicked=lambda: True)
    assert len(calls) == 1                                                                           # clicked: raised straight away
    calls.clear()
    with pytest.raises(ValueError):
        rm.run(lambda: (calls.append(1), (_ for _ in ()).throw(ValueError("x")))[1])
    assert len(calls) == 1


def test_idempotency_key_is_stable_and_pair_specific():
    assert idempotency_key("r1", "j1") == idempotency_key("r1", "j1") != idempotency_key("r1", "j2") != idempotency_key("r2", "j1")


# ============================================================================================== completeness validation
def mapping(**over):
    base = [FieldMapping(field_id="0:0", label="Full name", canonical="full_name", value="Jane Sample", source="contact.full_name", confidence="high", status="filled", required=True),
            FieldMapping(field_id="0:1", label="Email", canonical="email", value="jane.sample@example.com", source="contact.email", confidence="high", status="filled", required=True)]
    return base


def vinput(**over):
    up = [UploadRecord(field_id="0:7", role="resume", document_id="d" * 32, document_version=1, file_name="r.pdf", sha256="a" * 64, verified=True)]
    args = dict(stage="prepare", job_id="j", expected_job_id="j", form_job_id="j", profile=PROFILE, mapping=mapping(), uploads=up, form_automatable=True,
                has_resume_field=True, resume_approved=True, automation_allowed=True, current_plan_hash="p" * 64)
    args.update(over)
    return ValidationInput(**args)


def failing(v):
    return {c.name for c in v.failed_critical}


def test_a_complete_application_passes_and_each_defect_blocks_it():
    assert validate_application(vinput()).can_submit is True
    assert "platform_authorized" in failing(validate_application(vinput(automation_allowed=False)))
    assert "application_matches_job" in failing(validate_application(vinput(expected_job_id="other")))
    assert "application_matches_job" in failing(validate_application(vinput(job_active=False)))
    assert "form_detected" in failing(validate_application(vinput(form_automatable=False, form_issues=["multi-step"])))
    assert "no_validation_errors" in failing(validate_application(vinput(form_errors=["Email is invalid"])))
    assert "documents_approved" in failing(validate_application(vinput(resume_approved=False)))
    assert "resume_uploaded" in failing(validate_application(vinput(uploads=[])))
    bad_up = [UploadRecord(role="resume", document_id="d" * 32, document_version=1, file_name="r.pdf", sha256="a" * 64, verified=False, detail="no")]
    assert "resume_uploaded" in failing(validate_application(vinput(uploads=bad_up)))
    assert "eligibility_acknowledged" in failing(validate_application(vinput(eligibility_acknowledged=False)))
    missing = mapping() + [FieldMapping(field_id="0:5", label="Notice period", canonical="notice_period", status="needs_input", required=True)]
    v = validate_application(vinput(mapping=missing))
    assert "mandatory_fields_complete" in failing(v) and v.missing_fields == ["Notice period"] and not v.can_submit
    optional_missing = mapping() + [FieldMapping(field_id="0:5", label="Phone", canonical="phone", status="optional_skipped", required=False)]
    assert validate_application(vinput(mapping=optional_missing)).can_submit                       # optional fields never block


def test_personal_information_must_equal_the_verified_profile():
    tampered = mapping()
    tampered[1].value = "someone.else@example.com"
    assert "personal_information_correct" in failing(validate_application(vinput(mapping=tampered)))


def test_cover_letter_required_only_when_the_form_requires_it():
    base = dict(has_cover_letter_field=True)
    assert validate_application(vinput(**base)).can_submit                                         # optional + missing -> a warning only
    assert "cover_letter_attached" in failing(validate_application(vinput(cover_letter_required=True, **base)))
    cl = [UploadRecord(role="cover_letter", document_id="c" * 32, document_version=1, file_name="c.pdf", sha256="b" * 64, verified=True)]
    assert validate_application(vinput(cover_letter_required=True, uploads=vinput().uploads + cl, **base)).can_submit


def test_screening_answers_need_an_approved_source():
    bad = mapping() + [FieldMapping(field_id="0:9", label="About", canonical="screening_question", value="x", source="made_up", status="filled")]
    assert "screening_answers_approved" in failing(validate_application(vinput(mapping=bad)))
    good = mapping() + [FieldMapping(field_id="0:9", label="About", canonical="screening_question", value="x", source="screening_answers.why_company", status="filled")]
    assert validate_application(vinput(mapping=good)).can_submit


def test_submit_stage_needs_an_approval_bound_to_the_current_plan():
    assert "candidate_approval_recorded" in failing(validate_application(vinput(stage="submit")))
    assert "candidate_approval_recorded" in failing(validate_application(vinput(stage="submit", approval_plan_hash="x" * 64)))
    assert validate_application(vinput(stage="submit", approval_plan_hash="p" * 64)).can_submit
    assert "candidate_approval_recorded" not in {c.name for c in validate_application(vinput()).checks}   # prepare stage: not yet required


def test_review_warnings_do_not_block_but_are_reported():
    m = mapping()
    m[0].derived = True
    v = validate_application(vinput(mapping=m))
    assert v.can_submit and any(c.name == "review_suggested" and c.status == "warn" for c in v.checks)


def test_plan_hash_changes_with_any_approved_value():
    m, up = mapping(), vinput().uploads
    h = plan_hash(m, up, "j", "fp", "http://x")
    assert h == plan_hash(mapping(), vinput().uploads, "j", "fp", "http://x")
    m[0].value = "Someone"
    assert h != plan_hash(m, up, "j", "fp", "http://x")
    assert h != plan_hash(mapping(), up, "j2", "fp", "http://x") and h != plan_hash(mapping(), up, "j", "fp2", "http://x") and h != plan_hash(mapping(), up, "j", "fp", "http://y")
    up2 = vinput().uploads
    up2[0].sha256 = "c" * 64
    assert h != plan_hash(mapping(), up2, "j", "fp", "http://x")
    filled, ready = mapping(), mapping()
    ready[0].status = "ready"
    assert plan_hash(filled, up, "j", "fp") == plan_hash(ready, up, "j", "fp")                       # filled == ready


# ============================================================================================== document selection (approved only)
class FakeDocs:
    def __init__(self, groups, validations=None, downloads=None):
        self.groups, self.validations, self.downloads = groups, validations or {}, downloads or {}
        self.docs = self
        self.exported = []

    @staticmethod
    def group_id(resume_id, job_id, doc_type):
        return f"{resume_id}|{job_id}|{doc_type}"

    def group(self, gid, include_content=False):
        return self.groups.get(gid, [])

    def validate(self, did):
        return {**next(d for g in self.groups.values() for d in g if d["document_id"] == did), "validation": {"status": self.validations.get(did, "passed")}}

    def export(self, did, fmts):
        self.exported.append(did)
        d = self.validate(did)
        d["exports"] = [{"format": f, "from_approved": True} for f in fmts]
        return d

    def download(self, did, fmt):
        if did in self.downloads and self.downloads[did] is None:
            raise FileNotFoundError("tampered")
        return (b"%PDF-1.4 approved " + did.encode(), f"Jane_{did[:4]}.{fmt}", "application/pdf")


def doc(did, status, version=1, exports=True, content=None):
    return {"document_id": did, "version": version, "review_status": status, "content": content or {"answers": []}, "validation": {"status": "passed"},
            "exports": [{"format": "pdf", "from_approved": True}] if exports else []}


def test_only_the_approved_version_is_selected_never_a_draft_or_superseded(tmp_path):
    g = {"r|j|resume": [doc("aaaa" * 8, "superseded", 1), doc("bbbb" * 8, "approved", 2), doc("cccc" * 8, "draft", 3)]}
    sel = select_documents(FakeDocs(g), "r", "j", tmp_root=tmp_path)
    try:
        assert sel.resume.document_id == "bbbb" * 8 and sel.resume.version == 2 and sel.cover_letter is None
        assert any("No approved cover letter" in p for p in sel.problems) and sel.resume.path.read_bytes().startswith(b"%PDF")
        assert sel.document_ids == {"resume": "bbbb" * 8}
    finally:
        sel.cleanup()
    assert not sel.resume.path.exists()                                                              # temp copy removed; generated documents untouched


def test_unapproved_only_means_nothing_is_uploadable(tmp_path):
    g = {"r|j|resume": [doc("aaaa" * 8, "draft")], "r|j|cover_letter": [doc("bbbb" * 8, "superseded")]}
    sel = select_documents(FakeDocs(g), "r", "j", tmp_root=tmp_path)
    assert sel.resume is None and sel.cover_letter is None and len(sel.problems) == 2
    sel.cleanup()


def test_an_approved_document_that_no_longer_passes_or_lost_its_file_is_excluded(tmp_path):
    g = {"r|j|resume": [doc("aaaa" * 8, "approved")], "r|j|cover_letter": [doc("bbbb" * 8, "approved")]}
    sel = select_documents(FakeDocs(g, validations={"aaaa" * 8: "failed"}, downloads={"bbbb" * 8: None}), "r", "j", tmp_root=tmp_path)
    assert sel.resume is None and sel.cover_letter is None
    assert any("no longer passes the fact check" in p for p in sel.problems) and any("intact exported file" in p for p in sel.problems)
    sel.cleanup()


def test_missing_export_is_created_from_the_approved_content_only(tmp_path):
    g = {"r|j|resume": [doc("aaaa" * 8, "approved", exports=False)]}
    fake = FakeDocs(g)
    sel = select_documents(fake, "r", "j", tmp_root=tmp_path)
    assert fake.exported == ["aaaa" * 8] and sel.resume is not None
    sel.cleanup()


def test_screening_answers_exclude_placeholders():
    sel = Selection(screening={"answers": [{"question_id": "a", "question": "Q1", "answer": "Real", "status": "answered"},
                                           {"question_id": "b", "question": "Q2", "answer": "[CANDIDATE INPUT REQUIRED: x]", "status": "needs_input"},
                                           {"question_id": "c", "question": "Q3", "answer": "  ", "status": "draft"}]})
    assert set(sel.answers) == {"a"}


def test_choose_format_respects_the_inputs_accept_list(tmp_path):
    pdf = SelectedFile("resume", "d" * 32, 1, tmp_path / "r.pdf", "r.pdf", "a" * 64, 10, "pdf")
    docx = SelectedFile("resume", "d" * 32, 1, tmp_path / "r.docx", "r.docx", "b" * 64, 12, "docx")
    pdf.alt = {"docx": docx}
    assert choose_format(pdf, "") is pdf and choose_format(pdf, ".pdf,.doc") is pdf and choose_format(pdf, ".docx") is docx
    assert choose_format(pdf, "application/pdf") is pdf and choose_format(pdf, ".txt") is None
    assert choose_format(SelectedFile("resume", "d" * 32, 1, tmp_path / "r.pdf", "r.pdf", "a" * 64, 1, "pdf"), ".docx") is None


class FakeLocator:
    def __init__(self, files):
        self._files, self.set = files, []

    def set_input_files(self, path):
        self.set.append(path)

    def evaluate(self, js):
        return self._files


def test_upload_is_only_reported_verified_when_the_browser_holds_the_exact_file(tmp_path):
    sf = SelectedFile("resume", "d" * 32, 1, tmp_path / "r.pdf", "r.pdf", "a" * 64, 10, "pdf")
    assert upload_file(FakeLocator([["r.pdf", 10]]), sf).verified is True
    for attached in ([], [["other.pdf", 10]], [["r.pdf", 99]], [["r.pdf", 10], ["x", 1]]):
        rec = upload_file(FakeLocator(attached), sf)
        assert rec.verified is False and rec.detail
    boom = FakeLocator([])
    boom.set_input_files = lambda p: (_ for _ in ()).throw(RuntimeError("disk"))
    assert upload_file(boom, sf).verified is False


# ============================================================================================== locator (pure interpretation)
def raw_form(fields=None, buttons=None, **kw):
    fields = fields if fields is not None else [{"type": "text", "label": "Full name *", "required": True, "visible": True, "mark": "0:0", "name": "n"},
                                               {"type": "email", "label": "Email", "required": False, "visible": True, "mark": "0:1", "name": "e"},
                                               {"type": "file", "label": "Resume", "required": True, "visible": True, "mark": "0:2", "name": "r"}]
    buttons = buttons if buttons is not None else [{"mark": "0:0", "text": "Submit application", "type": "submit"}]
    return {"forms": [{"index": 0, "isForm": True, "title": "Apply now", "fields": fields, "buttons": buttons, "alerts": [], "classErrors": [], "formText": "Apply with your resume"}],
            "pageText": "Apply", **kw}


def test_snapshot_identifies_fields_required_flags_and_one_submit():
    snap = snapshot_from_raw(raw_form())
    assert snap.automatable and [f.label for f in snap.fields] == ["Full name", "Email", "Resume"] and [f.required for f in snap.fields] == [True, False, True]
    assert [f.kind for f in snap.fields] == ["text", "email", "file"] and [s.text for s in snap.submit] == ["Submit application"] and snap.fingerprint
    assert snapshot_from_raw(raw_form()).fingerprint == snap.fingerprint


@pytest.mark.parametrize("kw,needle", [
    (dict(captcha=True), "CAPTCHA"), (dict(password=True), "sign in"), (dict(buttons=[{"mark": "0:0", "text": "Next", "type": "button"}]), "multi-step"),
    (dict(buttons=[]), "No submit"), (dict(buttons=[{"mark": "0:0", "text": "Submit", "type": "submit"}, {"mark": "0:1", "text": "Apply now", "type": "submit"}]), "More than one submit"),
    (dict(fields=[{"type": "text", "label": "", "name": "x1", "required": True, "visible": True, "mark": "0:0"}]), "could not be identified"),
])
def test_snapshot_reports_why_a_form_is_not_automatable(kw, needle):
    snap = snapshot_from_raw(raw_form(**kw))
    assert not snap.automatable and any(needle in i for i in snap.issues), snap.issues


def test_no_form_or_login_only_page_is_not_found():
    assert not snapshot_from_raw({"forms": [], "pageText": "Hello"}).found
    login = snapshot_from_raw({"forms": [{"index": 0, "fields": [{"type": "text", "label": "Email", "visible": True}, {"type": "password", "label": "Password", "visible": True}],
                                          "buttons": [], "alerts": [], "classErrors": [], "formText": "Sign in"}], "pageText": "Sign in to continue", "password": True})
    assert not login.found and login.has_login_wall


def test_validation_errors_are_collected():
    raw = raw_form()
    raw["forms"][0]["alerts"] = ["Please check your answers"]
    raw["forms"][0]["fields"][0]["error"] = "required"
    snap = snapshot_from_raw(raw)
    assert "Please check your answers" in snap.errors and any("Full name" in e for e in snap.errors)


# ============================================================================================== safety checks + sessions + PII
@pytest.mark.parametrize("url", ["", None, "ftp://x.com/a", "http://example.com/a", "https://user:pw@example.com/", "https://localhost/a", "https://10.0.0.1/a",
                                 "http://127.0.0.1:80/a", "https://169.254.169.254/latest", "javascript:alert(1)", "file:///etc/passwd"])
def test_check_url_refuses_unsafe_targets(url):
    with pytest.raises(UnsafeUrl):
        check_url(url)


def test_check_url_allows_https_public_hosts_and_loopback_only_when_enabled():
    assert check_url("https://boards.greenhouse.io/a", allowed_hosts=["boards.greenhouse.io"])
    with pytest.raises(UnsafeUrl):
        check_url("https://evil.com/a", allowed_hosts=["boards.greenhouse.io"])
    assert check_url("http://127.0.0.1:5000/a", allow_local=True)
    with pytest.raises(UnsafeUrl):
        check_url("http://10.0.0.1/a", allow_local=True)


def test_assess_page_flags_stops_but_never_acts():
    assert assess_page("Please verify you are human", has_captcha_dom=False).captcha
    assert assess_page("", has_captcha_dom=True).must_stop and assess_page("Sign in to continue").login_required and assess_page("", has_password=True).must_stop
    assert assess_page("Access denied", status=403).blocked_status == 403 and assess_page("x", status=429).must_stop
    ok = assess_page("Apply for Senior Engineer", status=200)
    assert not ok.must_stop and ok.reason() == ""
    assert "never solved or bypassed" in assess_page("recaptcha").reason()


class FakeCtx:
    def __init__(self, state):
        self._s = state

    def storage_state(self):
        return self._s


def sess_settings(tmp_path, **kw):
    return get_settings().model_copy(update={"persist_browser_sessions": True, "secret_key": "k" * 32, "browser_sessions_dir": str(tmp_path / "s"), **kw})


def test_sessions_are_encrypted_expire_and_need_a_secret(tmp_path):
    secret_cookie = "SESSIONCOOKIEVALUE123"
    state = {"cookies": [{"name": "sid", "value": secret_cookie, "domain": "x.com"}], "origins": []}
    clock = {"t": datetime(2026, 1, 1, tzinfo=timezone.utc)}
    sm = SessionManager(sess_settings(tmp_path), now=lambda: clock["t"])
    assert sm.save(FakeCtx(state), "x.com") is True
    raw = next((tmp_path / "s").glob("*.session")).read_bytes()
    assert secret_cookie.encode() not in raw and b"cookies" not in raw                                # encrypted at rest
    assert sm.load("x.com") == state and sm.load("other.com") is None
    assert SessionManager(sess_settings(tmp_path, secret_key="z" * 32), now=lambda: clock["t"]).load("x.com") is None   # wrong key cannot read it
    clock["t"] += timedelta(hours=13)
    assert sm.load("x.com") is None and not list((tmp_path / "s").glob("*.session"))                    # expired -> deleted
    sm.save(FakeCtx(state), "x.com")
    assert sm.forget("x.com") and sm.load("x.com") is None


def test_sessions_are_off_by_default_or_without_a_secret(tmp_path):
    for s in (sess_settings(tmp_path, persist_browser_sessions=False), sess_settings(tmp_path, secret_key=""), sess_settings(tmp_path, secret_key="short")):
        sm = SessionManager(s)
        assert sm.persistence_enabled is False and sm.save(FakeCtx({"cookies": []}), "x.com") is False and sm.load("x.com") is None
    assert not (tmp_path / "s").exists()
    with pytest.raises(SecretUnavailable):
        derive_fernet_key("")


def test_manual_login_needs_a_visible_window_and_never_takes_credentials(tmp_path):
    import inspect
    class HeadlessBM:
        headless = True
    with pytest.raises(PermissionError):
        SessionManager(sess_settings(tmp_path)).manual_login(HeadlessBM(), "https://x.com/login", "x.com")
    params = inspect.signature(SessionManager.manual_login).parameters
    assert not any(p in params for p in ("username", "password", "email", "credentials", "token"))
    for fn in (credential_vault.store_password, credential_vault.get_password, credential_vault.fill_password):
        with pytest.raises(PermissionError):
            fn("x")
    assert credential_vault.STORES_PASSWORDS is False


def test_audit_details_never_carry_personal_data():
    d = safe_detail({"email": "a@b.com", "full_name": "Jane", "value": "x", "answer": "y", "phone": "+91 98765 43210", "count": 3, "reason": "Contact jane@x.com or +91 98765 43210",
                     "evidence": ["confirmation message", "mail me@x.org"], "nested": {"password": "p", "n": 1}, "obj": object()})
    assert "email" not in d and "full_name" not in d and "value" not in d and "answer" not in d and "phone" not in d and "obj" not in d
    assert d["count"] == 3 and "[email]" in d["reason"] and "[phone]" in d["reason"] and "jane@x.com" not in str(d) and "me@x.org" not in str(d)
    assert d["nested"] == {"n": 1} and "98765" not in str(d)
    assert mask("https://user:secret@x.com/a") == "https://***@x.com/a"
