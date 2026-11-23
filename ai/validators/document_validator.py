"""Factual-accuracy validation of GENERATED documents against the verified candidate profile.

Two kinds of checks:
* structured items (employers, titles, dates, skills, projects, education, certifications) must match the profile;
* free text (summary, bullets, letter, answers) is scanned for claims: skills, numbers/metrics, experience durations,
  leadership/ownership words, degrees, certifications, organisations and personal details.

Errors block approval; warnings must be acknowledged. Nothing is ever corrected silently - the validator only reports.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ai.schemas.candidate_profile import CandidateProfile, ValidationIssue
from ai.schemas.document_schema import (
    PLACEHOLDER, CoverLetterContent, DocumentValidation, ResumeContent, ScreeningContent, content_hash, parse_content,
)
from engines.matching.candidate_view import build_candidate_view, education_level
from engines.matching.skill_extractor import SkillCatalog

_FIRST_PERSON = re.compile(r"\b(i|i've|i have|i am|my|me|we)\b|\b(?:experienced|proficient|skilled|expert|expertise|hands-on|specialis\w+|worked|built|developed|"
                           r"designed|deployed|led|managed|implemented)\b", re.I)
_CLAIM_CUE = re.compile(r"\b(i have|i've|i am|i hold|holds?|earned|graduated|my (?:degree|phd|master\w*)|my experience|my background|i worked|i built|i developed|i used|i led|i managed|experienced in|"
                        r"experience (?:in|with)|proficient|skilled in|expertise|hands-on|specialis\w+|worked (?:with|on)|i design|i deploy|i implement)\b", re.I)
_LEAD_WORDS = {"led", "managed", "mentored", "supervised", "architected", "founded", "directed", "headed", "spearheaded", "owned", "pioneered"}
_YEARS = re.compile(r"(\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?)\b", re.I)
_NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?:\s*(?:%|x\b|k\b|ms\b))?", re.I)
_ORG = re.compile(r"\b(?:at|for|with|joined|joining)\s+([A-Z][A-Za-z0-9&.\-]*(?:\s+[A-Z][A-Za-z0-9&.\-]*){0,3})")
_NOT_ORG = {"I", "My", "The", "This", "That", "Our", "Your", "Their", "It", "We", "A", "An", "In", "On", "As", "To", "Dear", "Thank",
            "Python", "Machine", "Data", "Senior", "Junior", "Lead", "Hiring", "Sincerely", "Best", "Regards", "Please", "Team", "Teams", "Production"}
_CERT_WORD = re.compile(r"\b(certified|certification|certificate)\b", re.I)
_DEGREE = re.compile(r"\b(ph\.?d|doctorate|master'?s|masters|m\.?tech|mba|bachelor'?s|b\.?tech|b\.?e\b|degree)\b", re.I)
_SMALL = re.compile(r"^\d$|^10$")
MAX_ISSUES = 150


def _n(text: str | None) -> str:
    return re.sub(r"[^a-z0-9+#]+", " ", (text or "").lower()).strip()


def _nums(text: str) -> set[str]:
    out = set()
    for m in re.findall(r"\d[\d,]*(?:\.\d+)?", text or ""):
        m = m.replace(",", "")
        out.add(m)
        out.add(m.rstrip("0").rstrip(".") if "." in m else m)
    return out


@dataclass
class JobFacts:
    """What the job (not the candidate) says - allowed to be mentioned, never to be claimed as the candidate's experience."""

    company: str | None = None
    title: str | None = None
    skills: set[str] = field(default_factory=set)
    numbers: set[str] = field(default_factory=set)


@dataclass
class Facts:
    profile: CandidateProfile
    catalog: SkillCatalog
    text_norm: str = ""
    words: set[str] = field(default_factory=set)
    numbers: set[str] = field(default_factory=set)
    skills: set[str] = field(default_factory=set)         # canonical skills the candidate genuinely has
    orgs: set[str] = field(default_factory=set)
    jobs: list[dict] = field(default_factory=list)
    total_years: float | None = None
    edu_rank: int | None = None
    cert_tokens: list[set[str]] = field(default_factory=list)
    inputs: dict[str, str] = field(default_factory=dict)  # candidate-supplied personal details (notice, salary, ...)
    job: JobFacts = field(default_factory=JobFacts)
    view: object = None


def build_facts(profile: CandidateProfile, catalog: SkillCatalog, job: JobFacts | None = None, inputs: dict | None = None,
                bands: dict | None = None) -> Facts:
    f = Facts(profile=profile, catalog=catalog, job=job or JobFacts(), inputs={k: v for k, v in (inputs or {}).items() if v})
    view = build_candidate_view(profile, catalog, bands or {"entry": 0, "junior": 1, "mid": 3, "senior": 5, "lead": 8, "principal": 12})
    f.view, f.total_years, f.edu_rank = view, view.total_years, view.education_rank
    chunks: list[str] = [profile.contact.full_name or "", profile.summary.professional_summary or "", profile.summary.current_job_title or "",
                         profile.summary.career_objective or ""]
    years: set[str] = set()  # whole years from dates may be mentioned ("since 2019"), but month parts must not make metrics look supported
    for jb in profile.work_experience:
        texts = [jb.company_name or "", jb.job_title or "", jb.location or "", jb.description or "", *jb.responsibilities,
                 *jb.achievements, *jb.technologies_used]
        jt = " ".join(texts)
        years |= {d[:4] for d in (jb.start_date, jb.end_date) if d}
        f.jobs.append({"company": _n(jb.company_name), "title": _n(jb.job_title), "start": jb.start_date, "end": jb.end_date,
                       "current": jb.is_current, "texts": {_n(t) for t in [*jb.responsibilities, *jb.achievements, jb.description or ""] if t},
                       "numbers": _nums(jt), "skills": {catalog.normalize_or_raw(t) for t in jb.technologies_used}
                       | {m.canonical for m in catalog.find_in_text(jt)}})
        chunks.append(jt)
        f.orgs.add(_n(jb.company_name))
    for p in profile.projects:
        chunks.append(" ".join([p.project_name or "", p.description or "", p.role or "", *p.technologies, *p.responsibilities, *p.achievements]))
        f.orgs.add(_n(p.project_name))
    for e in profile.education:
        chunks.append(" ".join(str(x) for x in [e.degree, e.specialization, e.institution, e.grade] if x))
        years |= {str(y) for y in (e.start_year, e.graduation_year) if y}
        f.orgs.add(_n(e.institution))
    for c in profile.certifications:
        chunks.append(" ".join(filter(None, [c.certification_name, c.issuing_organization, c.credential_id])))
        years |= {c.issue_date[:4]} if c.issue_date else set()
        f.orgs.add(_n(c.issuing_organization))
        f.cert_tokens.append(set(_n(c.certification_name).split()))
    chunks += profile.achievements
    blob = "\n".join(chunks)
    f.text_norm = " " + _n(blob) + " "
    f.words = set(f.text_norm.split())
    f.numbers = _nums(blob) | years
    f.skills = {s for _, s in ((None, catalog.normalize_or_raw(x)) for _, x in profile.technical_skills.all_skills())}
    f.skills |= {s for j in f.jobs for s in j["skills"]} | {m.canonical for m in catalog.find_in_text(blob)}
    if f.total_years is not None:  # derived numbers a summary may legitimately use
        f.numbers |= {str(int(f.total_years)), f"{f.total_years:g}", str(round(f.total_years))}
    for v in f.inputs.values():
        f.numbers |= _nums(v)
    f.orgs.discard("")
    return f


# ----------------------------------------------------------------------------------------------- scanning
class _Collector:
    def __init__(self):
        self.issues: list[ValidationIssue] = []
        self.claims = 0

    def add(self, field_: str, code: str, message: str, severity: str = "warning"):
        if len(self.issues) < MAX_ISSUES and not any(i.field == field_ and i.code == code and i.message == message for i in self.issues):
            self.issues.append(ValidationIssue(field=field_, code=code, message=message, severity=severity))


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|[;\n]+", text or "") if s.strip()]


def _mask_target(sent: str, f: Facts) -> str:
    """The job title and company the document is written for are not claims about the candidate."""
    for target in (f.job.title, f.job.company):
        if target:
            sent = re.sub(re.escape(target), " ", sent, flags=re.I)
    return sent


def scan_text(text: str, field_: str, f: Facts, out: _Collector, *, extra_numbers: set[str] = frozenset(),
              extra_skills: set[str] = frozenset(), context_skills: set[str] = frozenset(), strict: bool = False) -> None:
    """Check the claims in free text. ``extra_*`` widen what is supported (for example one role's own technologies).

    ``strict``: every sentence is a claim about the candidate (resume bullets/achievements), so a skill that is not in
    the profile is always an error - even when the job description mentions it."""
    if not text or not text.strip():
        return
    if PLACEHOLDER in text:
        out.add(field_, "input_required", "This text still contains a placeholder that needs the candidate's own input.", "error")
    supported_numbers = f.numbers | set(extra_numbers)
    for original in _sentences(text):
        out.claims += 1
        sent = _mask_target(original, f)
        first_person = strict or bool(_CLAIM_CUE.search(original))
        # --- durations
        covered = set()
        for m in _YEARS.finditer(sent):
            claim = float(m.group(1))
            covered.add(m.group(1))
            tail = re.match(r"\s*(?:of\s+)?(?:[\w\-]+\s+){0,2}?(?:experience\s+)?(?:in|with|using|on)\s+([^,;.]{1,60})", sent[m.end():], re.I)
            skill = next((x.canonical for x in f.catalog.find_in_text(tail.group(1))), None) if tail else None  # "N years ... in <skill>"
            if skill:
                actual = f.view.skill_years(skill, f.catalog) if skill in f.skills else 0.0
                total = f.total_years
                if skill not in f.skills:
                    out.add(field_, "unsupported_skill", f"'{skill}' is not in the verified profile, so a duration claim for it cannot be true.", "error")
                elif total is not None and claim > total + 0.5:
                    out.add(field_, "duration_overstated", f"Claims {claim:g} years of {skill}, more than the whole career ({total:g} years).", "error")
                elif actual is None or claim > actual + 0.5:
                    shown = "cannot be established from the dated roles" if actual is None else f"the roles that mention it cover about {actual:g}"
                    out.add(field_, "duration_unverified", f"{claim:g} years of {skill}: {shown}. Make sure the figure is accurate.")
            elif f.total_years is None:
                out.add(field_, "duration_unverified", f"Cannot verify '{m.group(0)}': total experience is not established in the profile.")
            elif claim > f.total_years + 0.5:
                out.add(field_, "duration_overstated", f"Claims {claim:g} years of experience; the profile supports {f.total_years:g}.", "error")
        # --- numbers / metrics
        for m in _NUMBER.finditer(sent):
            raw = m.group(0)
            num = re.sub(r"[^\d.]", "", raw.replace(",", ""))
            if num in covered or not num:
                continue
            bare_small = _SMALL.match(num) and not re.search(r"%|x|k|ms", raw, re.I)
            if bare_small:
                continue
            if num in supported_numbers or num.rstrip("0").rstrip(".") in supported_numbers:
                continue
            if num in f.job.numbers:
                out.add(field_, "jd_number_unverified", f"'{raw.strip()}' comes from the job description, not from your profile - make sure it is true.")
            else:
                out.add(field_, "unsupported_number", f"The figure '{raw.strip()}' does not appear in the verified profile (possible fabricated metric).", "error")
        # --- skills / technologies
        for m in f.catalog.find_in_text(sent):
            canon = m.canonical
            if canon in f.skills or canon in extra_skills:
                continue
            if canon in f.job.skills and not first_person and canon not in context_skills:
                out.add(field_, "jd_skill_mentioned", f"'{canon}' is mentioned from the job description; it is not in your profile, so make sure the text does not claim it.")
            else:
                out.add(field_, "unsupported_skill", f"'{canon}' is not supported by the verified profile.", "error")
        # --- leadership / ownership claims
        for w in set(re.findall(r"[a-z]+", sent.lower())) & _LEAD_WORDS:
            if w not in f.words:
                out.add(field_, "unsupported_responsibility", f"'{w}' (leadership/ownership) does not appear anywhere in the profile.")
        # --- certifications and degrees
        if _CERT_WORD.search(sent):
            matches = any(t and t <= set(_n(sent).split()) for t in f.cert_tokens)
            if not matches and re.search(r"\bcertified\b", sent, re.I):
                out.add(field_, "unsupported_certification", "Claims to be certified, but no matching certification is in the verified profile.", "error")
            elif not matches and f.cert_tokens:
                out.add(field_, "certification_unverified", "A certification is mentioned that does not match any certification in the profile.")
        for d in _DEGREE.finditer(sent):
            lvl = education_level(d.group(0))
            if lvl and f.edu_rank is not None and lvl[1] > f.edu_rank and first_person:
                out.add(field_, "unsupported_education", f"Mentions a {lvl[0]} degree, which is above the highest degree in the profile.", "error")
        # --- organisations
        allowed = f.orgs | {_n(f.job.company), _n(f.job.title)}
        for m in _ORG.finditer(sent):
            name = m.group(1).strip().rstrip(".,")
            first = name.split()[0]
            if first in _NOT_ORG or f.catalog.normalize(name) or f.catalog.normalize(first):
                continue
            if _n(name) and not any(_n(name) in o or o in _n(name) for o in allowed if o):
                out.add(field_, "unrecognised_organization", f"'{name}' is not an employer, school, project or the target company in the profile/job.")


# ----------------------------------------------------------------------------------------- resume checks
def _same_job(item, f: Facts) -> dict | None:
    return next((j for j in f.jobs if j["company"] == _n(item.company_name) and j["title"] == _n(item.job_title)), None)


def _check_bullets(bullets, field_: str, f: Facts, out: _Collector, job: dict | None, scope_texts: set[str]):
    for i, b in enumerate(bullets):
        where = f"{field_}.bullets[{i}]"
        if _n(b.text) in scope_texts:
            out.claims += 1
            if PLACEHOLDER in b.text:
                out.add(where, "input_required", "Placeholder text must be replaced.", "error")
            continue  # verbatim from the profile = fully supported
        extra_n = job["numbers"] if job else set()
        extra_s = job["skills"] if job else set()
        before = len(out.issues)
        scan_text(b.text, where, f, out, extra_numbers=extra_n, extra_skills=set(), context_skills=set(), strict=True)
        if job:  # a technology claimed for THIS role must belong to this role
            for m in f.catalog.find_in_text(b.text):
                if m.canonical in f.skills and m.canonical not in extra_s:
                    out.add(where, "skill_not_used_in_role", f"'{m.canonical}' is in your profile but not listed for this role - check that it applies here.")
        if len(out.issues) == before or all(i.severity != "error" for i in out.issues[before:]):
            out.add(where, "edited_wording", "This bullet is not word-for-word from your profile; confirm it says only what is true.")


def validate_resume(c: ResumeContent, f: Facts, out: _Collector) -> None:
    p = f.profile.contact
    pairs = [("full_name", c.header.full_name, p.full_name), ("email", c.header.email, p.email), ("phone", c.header.phone, p.phone),
             ("location", c.header.location, p.location), ("linkedin_url", c.header.linkedin_url, p.linkedin_url),
             ("github_url", c.header.github_url, p.github_url), ("portfolio_url", c.header.portfolio_url, p.portfolio_url)]
    for name, got, want in pairs:
        out.claims += 1
        if got and got != want:
            out.add(f"header.{name}", "contact_mismatch", f"{name} differs from the verified profile (correct it in Resume Manager, not here).", "error")
    if c.header.headline and c.header.headline != f.profile.summary.current_job_title:
        out.add("header.headline", "unsupported_title", "The headline is not the job title recorded in the profile.", "error")

    scan_text(c.summary, "summary", f, out)

    for gi, g in enumerate(c.skills):
        for item in g.items:
            out.claims += 1
            if f.catalog.normalize_or_raw(item) not in f.skills:
                out.add(f"skills[{gi}]", "unsupported_skill", f"Skill '{item}' is not in the verified profile.", "error")

    seen = set()
    all_texts = {t for j in f.jobs for t in j["texts"]}
    for ei, e in enumerate(c.experience):
        where = f"experience[{ei}]"
        out.claims += 1
        job = _same_job(e, f)
        if job is None:
            if _n(e.company_name) in {j["company"] for j in f.jobs}:
                out.add(where, "title_changed", f"The job title '{e.job_title}' at {e.company_name} differs from the profile.", "error")
            else:
                out.add(where, "unknown_employer", f"Employer '{e.company_name}' is not in the verified work history.", "error")
            job = next((j for j in f.jobs if j["company"] == _n(e.company_name)), None)
        else:
            key = (job["company"], job["title"], job["start"])
            if key in seen:
                out.add(where, "duplicate_role", "This role appears more than once.")
            seen.add(key)
            if (e.start_date, e.end_date, bool(e.is_current)) != (job["start"], job["end"], bool(job["current"])):
                out.add(where, "date_changed", f"Dates for {e.company_name} differ from the profile "
                        f"({job['start']} to {job['end'] or 'present'}).", "error")
        _check_bullets(e.bullets, where, f, out, job, job["texts"] if job else all_texts)
    represented = {(_n(e.company_name), _n(e.job_title)) for e in c.experience}
    for j in f.jobs:
        if (j["company"], j["title"]) not in represented:
            out.add("experience", "employment_omitted", f"A role at '{j['company']}' is missing from the resume (employment history should stay complete).")

    ptexts = {_n(t) for pr in f.profile.projects for t in [pr.description or "", *pr.responsibilities, *pr.achievements] if t}
    for pi, pr in enumerate(c.projects):
        where = f"projects[{pi}]"
        out.claims += 1
        src = next((x for x in f.profile.projects if _n(x.project_name) == _n(pr.project_name)), None)
        if src is None:
            out.add(where, "unknown_project", f"Project '{pr.project_name}' is not in the verified profile.", "error")
            continue
        have = {f.catalog.normalize_or_raw(t) for t in src.technologies} | {m.canonical for m in f.catalog.find_in_text(
            " ".join([src.description or "", *src.responsibilities, *src.achievements]))}
        for t in pr.technologies:
            if f.catalog.normalize_or_raw(t) not in have:
                out.add(where, "unsupported_technology", f"'{t}' is not listed for the project '{pr.project_name}'.", "error")
        if pr.project_url and pr.project_url != src.project_url:
            out.add(where, "project_url_mismatch", "The project URL differs from the profile.", "error")
        _check_bullets(pr.bullets, where, f, out, {"numbers": _nums(" ".join([src.description or "", *src.responsibilities, *src.achievements])),
                                                 "skills": have}, ptexts)

    for ei, e in enumerate(c.education):
        out.claims += 1
        if not any(_n(e.degree) == _n(x.degree) and _n(e.institution) == _n(x.institution) and e.graduation_year == x.graduation_year
                   and e.start_year == x.start_year and _n(e.specialization) == _n(x.specialization) for x in f.profile.education):
            out.add(f"education[{ei}]", "education_mismatch", "This education entry does not match the verified profile.", "error")
    for ci, cert in enumerate(c.certifications):
        out.claims += 1
        if not any(_n(cert.certification_name) == _n(x.certification_name) for x in f.profile.certifications):
            out.add(f"certifications[{ci}]", "unsupported_certification", f"Certification '{cert.certification_name}' is not in the verified profile.", "error")
    ach = {_n(a) for a in f.profile.achievements} | all_texts
    for ai, a in enumerate(c.achievements):
        if _n(a.text) not in ach:
            scan_text(a.text, f"achievements[{ai}]", f, out, strict=True)
            out.add(f"achievements[{ai}]", "edited_wording", "This achievement is not word-for-word from your profile; confirm it is accurate.")
        else:
            out.claims += 1


# ------------------------------------------------------------------------------- letter / answers checks
def validate_cover_letter(c: CoverLetterContent, f: Facts, out: _Collector) -> None:
    name = f.profile.contact.full_name
    if c.signature and name and c.signature.strip() != name:
        out.add("signature", "signature_mismatch", "The signature does not match the name in the verified profile.", "error")
    if c.signature and PLACEHOLDER in c.signature:
        out.add("signature", "input_required", "Your name is missing from the profile; add it there or type it here.", "error")
    if not c.paragraphs:
        out.add("paragraphs", "empty_document", "The letter has no content.", "error")
    for i, p in enumerate(c.paragraphs):
        scan_text(p.text, f"paragraphs[{i}]", f, out)


_PERSONAL = {"notice_period": "notice period", "salary_expectation": "salary expectation", "relocation": "relocation preference",
             "work_authorization": "work authorisation"}


def validate_screening(c: ScreeningContent, f: Facts, out: _Collector) -> None:
    if not c.answers:
        out.add("answers", "empty_document", "There are no answers.", "error")
    inputs = {**f.inputs, **c.candidate_inputs}
    for i, a in enumerate(c.answers):
        where = f"answers[{i}]"
        if a.status == "needs_input" or PLACEHOLDER in a.answer or not a.answer.strip():
            out.add(where, "input_required", f"'{a.question[:60]}' still needs your own answer.", "error")
            continue
        if a.question_id in _PERSONAL:
            given = inputs.get(a.question_id)
            if not given:
                out.add(where, "unsupported_personal_detail", f"Your {_PERSONAL[a.question_id]} is not in your profile or preferences, so it "
                        "cannot be stated - enter it yourself.", "error")
                out.claims += 1
                continue
            extra = _nums(given)
            for num in _nums(a.answer):
                if num not in extra and not _SMALL.match(num):
                    out.add(where, "unsupported_personal_detail", f"The figure '{num}' differs from the value you provided ('{given}').", "error")
            if a.question_id in ("notice_period", "salary_expectation") and _n(given) not in _n(a.answer):
                out.add(where, "provided_value_missing", f"The answer does not state the {_PERSONAL[a.question_id]} you provided ('{given}').")
            out.claims += 1
            continue
        scan_text(a.answer, where, f, out, extra_numbers=_nums(" ".join(inputs.values())))


# ------------------------------------------------------------------------------------------------ entry
def validate_document(doc_type: str, content: dict, profile: CandidateProfile, catalog: SkillCatalog, *, job: JobFacts | None = None,
                      inputs: dict | None = None, bands: dict | None = None) -> DocumentValidation:
    """Validate generated/edited content. Never raises for bad content: malformed content is itself an error issue."""
    out = _Collector()
    try:
        parsed = parse_content(doc_type, content)
    except Exception:
        out.add("content", "malformed_content", "The document content does not have the expected structure.", "error")
        return DocumentValidation(status="failed", issues=out.issues, validated_at=datetime.now(timezone.utc), content_hash=content_hash(content))
    f = build_facts(profile, catalog, job, inputs, bands)
    {"resume": validate_resume, "cover_letter": validate_cover_letter, "screening_answers": validate_screening}[doc_type](parsed, f, out)
    errors = any(i.severity == "error" for i in out.issues)
    warnings = any(i.severity == "warning" for i in out.issues)
    status = "failed" if errors else "passed_with_warnings" if warnings else "passed"
    return DocumentValidation(status=status, issues=out.issues, validated_at=datetime.now(timezone.utc),
                              content_hash=content_hash(content), claims_checked=out.claims)
