"""Factuality validation: compare a profile against the source resume text.

The validator never edits a profile. It reports issues so a human can review:
unsupported claims, missing fields, uncertain dates, duplicates and
inconsistent experience calculations.
"""
import re
from collections import defaultdict
from datetime import date

from ai.schemas.candidate_profile import CandidateProfile, ValidationIssue

MAX_ISSUES = 200


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9+#@]+", " ", text.lower()).strip()


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


class SourceIndex:
    """Normalised views of the source text for fast containment checks."""

    def __init__(self, text: str):
        self.norm = " " + _norm(text) + " "
        self.digits = _digits(text)

    def supports(self, value: str | None, kind: str = "text") -> bool:
        if not value:
            return True
        if kind == "phone":
            return _digits(value) in self.digits
        if kind == "url":
            v = re.sub(r"^https?://(www\.)?", "", value.strip().lower()).rstrip("/")
            return _norm(v) in self.norm
        return _norm(value) in self.norm


def is_supported(value: str | None, source_text: str, kind: str = "text") -> bool:
    return SourceIndex(source_text).supports(value, kind)


def validate_profile(
    profile: CandidateProfile, source_text: str, today: date | None = None
) -> list[ValidationIssue]:
    from engines.resume.profile_extractor import calculate_total_experience, find_overlaps

    today = today or date.today()
    src = SourceIndex(source_text)
    issues: list[ValidationIssue] = []

    def add(field, code, message, severity="warning"):
        if len(issues) < MAX_ISSUES:
            issues.append(ValidationIssue(field=field, code=code, message=message, severity=severity))

    def check(field, value, kind="text"):
        if value and not src.supports(value, kind):
            shown = value if len(value) <= 60 else value[:57] + "..."
            add(field, "unsupported_claim",
                f"'{shown}' was not found in the source resume text (manually added, edited or AI-generated).")

    # 1. unsupported claims -------------------------------------------------
    c = profile.contact
    check("contact.full_name", c.full_name)
    check("contact.email", c.email)
    check("contact.phone", c.phone, "phone")
    check("contact.location", c.location)
    for f in ("linkedin_url", "github_url", "portfolio_url"):
        check(f"contact.{f}", getattr(c, f), "url")
    check("summary.current_job_title", profile.summary.current_job_title)
    for i, j in enumerate(profile.work_experience):
        p = f"work_experience[{i}]"
        check(f"{p}.company_name", j.company_name)
        check(f"{p}.job_title", j.job_title)
        for t in j.technologies_used:
            check(f"{p}.technologies_used", t)
        for t in j.achievements:
            check(f"{p}.achievements", t)
    for i, pr in enumerate(profile.projects):
        check(f"projects[{i}].project_name", pr.project_name)
        for t in pr.achievements:
            check(f"projects[{i}].achievements", t)
    for i, e in enumerate(profile.education):
        check(f"education[{i}].institution", e.institution)
        check(f"education[{i}].degree", e.degree)
    for i, cert in enumerate(profile.certifications):
        check(f"certifications[{i}].certification_name", cert.certification_name)
        check(f"certifications[{i}].credential_id", cert.credential_id)
    for cat, skill in profile.technical_skills.all_skills():
        check(f"technical_skills.{cat}", skill)

    # 2. missing fields -----------------------------------------------------
    for f in ("full_name", "email", "phone"):
        if not getattr(c, f):
            add(f"contact.{f}", "missing_field", f"{f.replace('_', ' ').capitalize()} was not found.", "warning")
    for sec in ("work_experience", "education", "technical_skills"):
        empty = not (profile.technical_skills.all_skills() if sec == "technical_skills" else getattr(profile, sec))
        if empty:
            add(sec, "missing_section", f"No {sec.replace('_', ' ')} were extracted.", "warning")

    # 3. uncertain dates ----------------------------------------------------
    for i, j in enumerate(profile.work_experience):
        p = f"work_experience[{i}]"
        if j.start_date and len(j.start_date) == 4:
            add(f"{p}.start_date", "uncertain_date", "Start date has year only.", "info")
        if j.end_date and len(j.end_date) == 4:
            add(f"{p}.end_date", "uncertain_date", "End date has year only.", "info")
        if j.start_date and j.end_date and j.end_date[:7] < j.start_date[:7]:
            add(f"{p}.end_date", "date_order", "End date is before start date.", "error")
        if j.start_date and int(j.start_date[:4]) > today.year:
            add(f"{p}.start_date", "future_date", "Start date is in the future.", "warning")
        if not j.start_date:
            add(f"{p}.start_date", "missing_date", "Start date is missing.", "warning")
        if j.is_current and j.end_date:
            add(f"{p}.end_date", "date_conflict", "Marked current but has an end date.", "warning")

    # 4. duplicate skills ---------------------------------------------------
    seen = defaultdict(list)
    for cat, skill in profile.technical_skills.all_skills():
        seen[_norm(skill)].append(cat)
    for key, cats in seen.items():
        if len(cats) > 1:
            add("technical_skills", "duplicate_skill", f"Skill '{key}' appears {len(cats)} times ({', '.join(cats)}).")

    # 5. duplicate experience ----------------------------------------------
    keys = defaultdict(list)
    for i, j in enumerate(profile.work_experience):
        keys[(_norm(j.company_name or ""), _norm(j.job_title or ""), j.start_date)].append(i + 1)
    for k, idxs in keys.items():
        if len(idxs) > 1 and (k[0] or k[1]):
            add("work_experience", "duplicate_experience", f"Entries {idxs} look like duplicates.")

    # 6. experience calculation --------------------------------------------
    for a, b in find_overlaps(profile.work_experience, today):
        add("work_experience", "overlapping_employment", f"Entries #{a + 1} and #{b + 1} overlap in time.")
    stated = profile.summary.total_experience_years
    calc = calculate_total_experience(profile.work_experience, today)
    if stated is not None:
        if calc is None:
            add("summary.total_experience_years", "unverifiable_experience",
                "Total experience is set but employment dates do not support a calculation.")
        elif abs(stated - calc) > 0.5:
            add("summary.total_experience_years", "inconsistent_experience",
                f"Stated {stated} years differs from {calc} years calculated from employment dates.")
        starts = [j.start_date for j in profile.work_experience if j.start_date]
        if starts and stated > (today.year - int(min(starts)[:4])) + 1:
            add("summary.total_experience_years", "implausible_experience",
                "Total experience is larger than the span of all employment dates.", "error")
    return issues


# Phase 5: factual validation of GENERATED documents lives in ai/validators/document_validator.py.
from ai.validators.document_validator import JobFacts, validate_document  # noqa: E402,F401
