"""Form-completeness validation: may this application be approved / submitted? Any CRITICAL failure blocks submission.

Pure function over plain data (mapping, uploads, form snapshot, policy decision, approval) so it is unit-tested without a browser and the
backend can re-run it right before submitting. (Not to be confused with ai/validators/document_validator.py, which fact-checks the
generated documents themselves.)
"""
from dataclasses import dataclass, field

from ai.schemas.candidate_profile import CandidateProfile
from database.models.application import ApplicationValidation, CheckResult, FieldMapping, UploadRecord


@dataclass
class ValidationInput:
    stage: str                                        # "prepare" (ready for approval?) | "submit" (final gate)
    job_id: str
    expected_job_id: str                              # the job the candidate selected
    form_job_id: str | None                           # the job the loaded page belongs to (url recorded at prepare)
    profile: CandidateProfile
    mapping: list[FieldMapping]
    uploads: list[UploadRecord]
    form_errors: list[str] = field(default_factory=list)
    form_automatable: bool = False
    form_issues: list[str] = field(default_factory=list)
    has_resume_field: bool = False
    has_cover_letter_field: bool = False
    cover_letter_required: bool = False
    resume_approved: bool = False
    cover_letter_approved: bool = False
    automation_allowed: bool = False
    policy_reason: str = ""
    approval_plan_hash: str | None = None
    current_plan_hash: str | None = None
    job_active: bool = True
    problems: list[str] = field(default_factory=list)   # e.g. missing approved documents (from the uploader)
    eligibility_acknowledged: bool = True


def C(name, ok, message="", critical=True, warn=False) -> CheckResult:
    return CheckResult(name=name, status="pass" if ok else ("warn" if warn else "fail"), critical=critical and not warn, message=message if not ok else "")


def validate_application(v: ValidationInput) -> ApplicationValidation:
    checks: list[CheckResult] = []
    contact = v.profile.contact
    by_canon = {m.canonical: m for m in v.mapping if m.canonical}

    checks.append(C("platform_authorized", v.automation_allowed, v.policy_reason or "Automation is not permitted for this platform."))
    checks.append(C("application_matches_job", v.job_id == v.expected_job_id and v.job_active and (v.form_job_id in (None, v.expected_job_id)),
                    "The application does not belong to the selected job, or the job is no longer active."))
    checks.append(C("eligibility_acknowledged", v.eligibility_acknowledged, "The job failed the eligibility check; you must explicitly acknowledge that first."))
    checks.append(C("form_detected", v.form_automatable, "; ".join(v.form_issues) or "The application form was not recognised."))

    # every REQUIRED field has a usable value (nothing optional is forced)
    missing = [m.label for m in v.mapping if m.required and m.status in ("needs_input", "manual_required")]
    checks.append(C("mandatory_fields_complete", not missing, f"Missing or needs your input: {', '.join(missing[:10])}"))

    # personal information must equal the verified profile (never edited behind the candidate's back)
    expected = {"full_name": contact.full_name, "email": contact.email, "phone": contact.phone}
    wrong = [k for k, exp in expected.items() if k in by_canon and by_canon[k].source and by_canon[k].source.startswith("contact.")
             and exp and by_canon[k].value != exp]
    checks.append(C("personal_information_correct", not wrong, f"Values differ from your verified profile: {', '.join(wrong)}"))
    checks.append(C("identity_present", bool(contact.full_name and contact.email), "Your verified profile has no name/email; complete it in Resume Manager."))

    resume_up = next((u for u in v.uploads if u.role == "resume"), None)
    if v.has_resume_field:
        checks.append(C("documents_approved", v.resume_approved, "; ".join(p for p in v.problems if "resume" in p) or "No approved resume."))
        checks.append(C("resume_uploaded", bool(resume_up and resume_up.verified), (resume_up.detail if resume_up else None) or "The resume was not uploaded."))
    cl_up = next((u for u in v.uploads if u.role == "cover_letter"), None)
    if v.has_cover_letter_field:
        need = v.cover_letter_required
        ok = bool(cl_up and cl_up.verified)
        checks.append(C("cover_letter_attached", ok or not need, (cl_up.detail if cl_up else None) or "A cover letter is required but none was attached (approve one in Document Generation).",
                        critical=need, warn=not need and not ok))

    # answers: only approved screening answers / profile facts / the candidate's own input may be used
    bad_src = [m.label for m in v.mapping if m.status in ("ready", "filled") and m.canonical == "screening_question"
               and not (m.source or "").startswith(("screening_answers.", "candidate_input"))]
    checks.append(C("screening_answers_approved", not bad_src, f"Answers without an approved source: {', '.join(bad_src[:5])}"))

    checks.append(C("no_validation_errors", not v.form_errors, "The form shows errors: " + "; ".join(v.form_errors[:3])))

    # softer checks the candidate should look at (acknowledged at approval)
    review = [m.label for m in v.mapping if m.status in ("ready", "filled") and (m.derived or m.confidence == "medium")]
    if review:
        checks.append(C("review_suggested", False, f"Check these values: {', '.join(review[:8])}", critical=False, warn=True))
    skipped = [m.label for m in v.mapping if m.status == "optional_skipped"]
    if skipped:
        checks.append(C("optional_fields_blank", False, f"Optional fields left blank: {', '.join(skipped[:8])}", critical=False, warn=True))

    if v.stage == "submit":
        approved = bool(v.approval_plan_hash and v.approval_plan_hash == v.current_plan_hash)
        checks.append(C("candidate_approval_recorded", approved, "No valid approval: nothing may be submitted without your explicit approval of exactly this application."))

    out = ApplicationValidation(checks=checks, missing_fields=missing, plan_hash=v.current_plan_hash)
    out.can_submit = not out.failed_critical
    return out
