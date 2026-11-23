"""A matching-oriented view of the verified CandidateProfile.

Only facts present in the profile are used. Anything that cannot be derived (no dates,
no education, ...) is left as None so the scorer reports UNKNOWN instead of guessing.
"""
import re
from dataclasses import dataclass, field
from datetime import date

from ai.schemas.candidate_profile import CandidateProfile
from ai.schemas.job_schema import SENIORITY_LEVELS
from engines.matching.jd_parser import _EDU_PATTERNS, _TITLE_LEVEL, _level_from_years
from engines.matching.skill_extractor import SkillCatalog
from engines.resume.profile_extractor import _month_index, calculate_total_experience

_DEG_PATTERNS = _EDU_PATTERNS + [
    ("bachelor", 2, re.compile(r"\b(B\.?\s?Tech|B\.?\s?E\.?|B\.?\s?Sc\.?|BCA|B\.?\s?Com\.?|BS|B\.?S\.?)\b")),
    ("master", 3, re.compile(r"\b(M\.?\s?Tech|M\.?\s?E\.?|M\.?\s?Sc\.?|MCA|MBA|MS|M\.?S\.?)\b")),
]


def education_level(degree_text: str | None) -> tuple[str, int] | None:
    if not degree_text:
        return None
    best = None
    for name, rank, rx in _DEG_PATTERNS:
        if rx.search(degree_text) and (best is None or rank > best[1]):
            best = (name, rank)
    return best


@dataclass
class CandidateView:
    profile: CandidateProfile
    skills: dict[str, set[str]] = field(default_factory=dict)  # canonical -> where it appears
    demonstrated: set[str] = field(default_factory=set)  # used in work experience / projects
    total_years: float | None = None
    years_source: str = ""
    statements: list[tuple[str, str]] = field(default_factory=list)  # (text, source)
    education_rank: int | None = None
    education_text: str | None = None
    location: str | None = None
    certifications: list[str] = field(default_factory=list)
    seniority_level: str | None = None
    title_level: str | None = None
    reviewed: bool = False
    _job_skill_cache: dict = field(default_factory=dict)

    @property
    def skill_names(self) -> set[str]:
        return set(self.skills)

    def skill_years(self, canonical: str, catalog: SkillCatalog, today: date | None = None) -> float | None:
        """Years of *verified, dated* work history that mention the skill (overlaps counted once)."""
        today = today or date.today()
        spans = []
        for job in self.profile.work_experience:
            text = " ".join([job.description or "", *job.responsibilities, *job.achievements])
            techs = {catalog.normalize_or_raw(t) for t in job.technologies_used}
            used = canonical in techs or any(m.canonical == canonical for m in catalog.find_in_text(text))
            if used and job.start_date:
                s = _month_index(job.start_date, end=False)
                e = today.year * 12 + today.month if (job.is_current or not job.end_date) else _month_index(job.end_date, end=True)
                if e >= s:
                    spans.append((s, e))
        if not spans:
            return 0.0 if canonical not in self.demonstrated else None  # None = used, but dates missing
        spans.sort()
        total, cs, ce = 0, spans[0][0], spans[0][1]
        for s, e in spans[1:]:
            if s > ce + 1:
                total += ce - cs + 1
                cs, ce = s, e
            else:
                ce = max(ce, e)
        total += ce - cs + 1
        return round(total / 12, 1)


def build_candidate_view(profile: CandidateProfile, catalog: SkillCatalog, bands: dict[str, float]) -> CandidateView:
    v = CandidateView(profile=profile, reviewed=profile.review_status == "reviewed")

    def add(skill_text: str, source: str, demonstrated: bool = False):
        canon = catalog.normalize_or_raw(skill_text)
        if canon:
            v.skills.setdefault(canon, set()).add(source)
            if demonstrated:
                v.demonstrated.add(canon)

    for cat, s in profile.technical_skills.all_skills():
        add(s, "skills section")
    for job in profile.work_experience:
        label = f"experience: {job.company_name or job.job_title or 'role'}"
        for t in job.technologies_used:
            add(t, label, True)
        blob = " ".join([job.description or "", *job.responsibilities, *job.achievements])
        for m in catalog.find_in_text(blob):
            add(m.canonical, label, True)
        for text in [*job.responsibilities, *job.achievements, *([job.description] if job.description else [])]:
            v.statements.append((text, label))
    for p in profile.projects:
        label = f"project: {p.project_name or 'unnamed'}"
        for t in p.technologies:
            add(t, label, True)
        blob = " ".join([p.description or "", *p.responsibilities, *p.achievements])
        for m in catalog.find_in_text(blob):
            add(m.canonical, label, True)
        for text in [*p.responsibilities, *p.achievements, *([p.description] if p.description else [])]:
            v.statements.append((text, label))
    for a in profile.achievements:
        v.statements.append((a, "achievements"))

    # experience: use the reviewed/edited value, else calculate from dated roles. Never invent.
    if profile.summary.total_experience_years is not None:
        v.total_years = profile.summary.total_experience_years
        v.years_source = "profile (stated or calculated, as reviewed)"
    else:
        calc = calculate_total_experience(profile.work_experience)
        if calc is not None:
            v.total_years, v.years_source = calc, "calculated from dated roles"
    if v.total_years is not None:
        v.seniority_level = _level_from_years(v.total_years, bands)
    title = profile.summary.current_job_title or ""
    for rx, level in _TITLE_LEVEL:
        if title and rx.search(title):
            v.title_level = level
            break

    best = None
    for e in profile.education:
        lvl = education_level(" ".join(filter(None, [e.degree, e.specialization])))
        if lvl and (best is None or lvl[1] > best[1]):
            best, v.education_text = lvl, " ".join(filter(None, [e.degree, e.specialization, e.institution]))
    if best:
        v.education_rank = best[1]
    v.location = profile.contact.location
    v.certifications = [c.certification_name for c in profile.certifications if c.certification_name]
    return v


def level_rank(level: str | None) -> int | None:
    return SENIORITY_LEVELS.index(level) if level in SENIORITY_LEVELS else None
