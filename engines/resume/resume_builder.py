"""Job-specific resume customisation.

The verified candidate profile is the only source of content. For one job this module:
  * writes a short summary from verified facts (years, title, matched skills, latest role, a measurable result);
  * orders skill groups and skills by relevance to the job (nothing is added);
  * keeps EVERY role with its title, company and dates unchanged, and picks/orders the most relevant bullets
    (bullets are the candidate's own text, verbatim - never rewritten);
  * ranks projects and surfaces the relevant ones; keeps education and certifications as they are.
Every element records the profile path it came from (``source``).
"""
import re
from dataclasses import dataclass

from ai.schemas.candidate_profile import CandidateProfile
from ai.schemas.document_schema import (
    Bullet, CertificationItem, EducationItem, ExperienceItem, ProjectItem, ResumeContent, ResumeHeader, SkillGroup,
)
from engines.generation.resume_customizer import JobContext, rank, skill_relevance, text_relevance
from engines.matching.skill_extractor import SkillCatalog

CATEGORY_LABELS = {
    "programming_languages": "Programming Languages", "machine_learning": "Machine Learning", "deep_learning": "Deep Learning",
    "generative_ai": "Generative AI", "llm_frameworks": "LLM Frameworks", "libraries": "Libraries", "databases": "Databases",
    "cloud_platforms": "Cloud Platforms", "developer_tools": "Developer Tools", "other_skills": "Other Skills",
}


@dataclass
class ResumeOptions:
    max_projects: int = 3
    bullets_recent: int = 5       # for the most recent roles
    bullets_older: int = 3
    recent_roles: int = 2
    max_project_bullets: int = 3
    max_achievements: int = 3
    max_skills_per_group: int = 14
    use_summary: bool = True


def _skill_display_map(profile: CandidateProfile, catalog: SkillCatalog) -> dict[str, str]:
    """canonical skill -> the candidate's own wording (first spelling wins)."""
    out: dict[str, str] = {}
    for _cat, s in profile.technical_skills.all_skills():
        out.setdefault(catalog.normalize_or_raw(s), s)
    return out


def _bullets(texts: list[tuple[str, str]], ctx: JobContext, catalog: SkillCatalog, limit: int) -> list[Bullet]:
    ranked = rank(texts, lambda t: text_relevance(t[0], ctx, catalog))
    return [Bullet(text=t, source=src) for t, src in ranked[:limit]]


def _role_texts(job, idx: int) -> list[tuple[str, str]]:
    out = [(t, f"work_experience[{idx}].responsibilities[{j}]") for j, t in enumerate(job.responsibilities)]
    out += [(t, f"work_experience[{idx}].achievements[{j}]") for j, t in enumerate(job.achievements)]
    if not out and job.description:
        out.append((job.description, f"work_experience[{idx}].description"))
    return out


def ordered_roles(profile: CandidateProfile) -> list[tuple[int, object]]:
    """Reverse-chronological (current role first). Dates are never changed."""
    by_start = sorted(enumerate(profile.work_experience), key=lambda t: t[1].start_date or "", reverse=True)
    return sorted(by_start, key=lambda t: not t[1].is_current)


def compose_summary(profile: CandidateProfile, ctx: JobContext, catalog: SkillCatalog) -> tuple[str, list[str]]:
    """Deterministic summary from verified facts only. Returns (text, evidence paths)."""
    sources: list[str] = []
    title = profile.summary.current_job_title
    years = profile.summary.total_experience_years
    display = _skill_display_map(profile, catalog)
    wanted = ctx.must | ctx.preferred
    matched = [s for s in rank(list(display), lambda s: skill_relevance(s, ctx, catalog)) if s in wanted]
    skills = [display[s] for s in matched[:4]] or [display[s] for s in list(display)[:3]]

    head = None
    if title and years is not None and years >= 1:
        head = f"{title} with {int(years)}+ years of experience"
        sources += ["summary.current_job_title", "summary.total_experience_years"]
    elif title:
        head = title
        sources.append("summary.current_job_title")
    elif years is not None and years >= 1:
        head = f"Professional with {int(years)}+ years of experience"
        sources.append("summary.total_experience_years")
    sentences = []
    if head and skills:
        sentences.append(f"{head}, specialising in {_join(skills)}.")
        sources += [f"technical_skills ({s})" for s in skills]
    elif head:
        sentences.append(head + ".")
    elif skills:
        sentences.append(f"Skilled in {_join(skills)}.")
        sources += [f"technical_skills ({s})" for s in skills]

    roles = ordered_roles(profile)
    for idx, job in roles:
        if job.job_title and job.company_name:
            sentences.append(f"Most recently {job.job_title} at {job.company_name}.")
            sources.append(f"work_experience[{idx}]")
            break
    best = None
    for idx, job in roles:
        for t, src in _role_texts(job, idx):
            if re.search(r"\d", t):
                rel = text_relevance(t, ctx, catalog)
                if rel > 0 and (best is None or rel > best[0]):
                    best = (rel, t, src)
    if best:
        sentences.append(f"Track record includes: {best[1].rstrip('.')}.")
        sources.append(best[2])
    text = " ".join(sentences)
    if not text and profile.summary.professional_summary:  # nothing to compose from: use the candidate's own words
        return profile.summary.professional_summary, ["summary.professional_summary"]
    return text, sources


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def build_resume(profile: CandidateProfile, ctx: JobContext, catalog: SkillCatalog, options: ResumeOptions | None = None,
                 summary: str | None = None, summary_sources: list[str] | None = None) -> ResumeContent:
    opt = options or ResumeOptions()
    c = profile.contact
    header = ResumeHeader(full_name=c.full_name, headline=profile.summary.current_job_title, email=c.email, phone=c.phone,
                          location=c.location, linkedin_url=c.linkedin_url, github_url=c.github_url, portfolio_url=c.portfolio_url)
    notes: list[str] = []

    if summary is None and opt.use_summary:
        summary, summary_sources = compose_summary(profile, ctx, catalog)
    if not summary:
        notes.append("No summary: the profile has no facts to build one from.")

    # skills: reorder groups and items by relevance; add nothing
    groups = []
    for pos, (cat, label) in enumerate(CATEGORY_LABELS.items()):
        items = getattr(profile.technical_skills, cat)
        if not items:
            continue
        ranked = rank(items, lambda s: skill_relevance(s, ctx, catalog))
        groups.append((sum(skill_relevance(s, ctx, catalog) for s in items), pos, SkillGroup(
            label=label, items=ranked[: opt.max_skills_per_group], source=f"technical_skills.{cat}")))
    groups.sort(key=lambda g: (-g[0], g[1]))
    skills = [g for _, _, g in groups]

    # experience: every role stays, with its own title/company/dates; bullets are selected and ordered
    experience = []
    for n, (idx, job) in enumerate(ordered_roles(profile)):
        limit = opt.bullets_recent if n < opt.recent_roles else opt.bullets_older
        experience.append(ExperienceItem(
            company_name=job.company_name, job_title=job.job_title, location=job.location, start_date=job.start_date,
            end_date=job.end_date, is_current=job.is_current, source=f"work_experience[{idx}]",
            bullets=_bullets(_role_texts(job, idx), ctx, catalog, limit)))

    # projects: most relevant first
    def proj_rel(p) -> float:
        score = sum(skill_relevance(t, ctx, catalog) for t in p[1].technologies)
        texts = [p[1].description or "", *p[1].responsibilities, *p[1].achievements]
        return score + sum(text_relevance(t, ctx, catalog) for t in texts if t)

    projects = []
    for idx, p in rank(list(enumerate(profile.projects)), proj_rel)[: opt.max_projects]:
        texts = [(t, f"projects[{idx}].responsibilities[{j}]") for j, t in enumerate(p.responsibilities)]
        texts += [(t, f"projects[{idx}].achievements[{j}]") for j, t in enumerate(p.achievements)]
        if not texts and p.description:
            texts = [(p.description, f"projects[{idx}].description")]
        projects.append(ProjectItem(
            project_name=p.project_name, project_url=p.project_url, source=f"projects[{idx}]",
            technologies=rank(p.technologies, lambda t: skill_relevance(t, ctx, catalog)),
            bullets=_bullets(texts, ctx, catalog, opt.max_project_bullets)))
    if len(profile.projects) > opt.max_projects:
        notes.append(f"{len(profile.projects) - opt.max_projects} lower-relevance project(s) left out for length.")

    education = [EducationItem(degree=e.degree, specialization=e.specialization, institution=e.institution, start_year=e.start_year,
                               graduation_year=e.graduation_year, grade=e.grade, source=f"education[{i}]")
                 for i, e in sorted(enumerate(profile.education), key=lambda t: t[1].graduation_year or 0, reverse=True)]
    certs = [CertificationItem(certification_name=x.certification_name, issuing_organization=x.issuing_organization,
                               issue_date=x.issue_date, source=f"certifications[{i}]")
             for i, x in rank(list(enumerate(profile.certifications)),
                              lambda t: text_relevance(t[1].certification_name or "", ctx, catalog))]
    achievements = [Bullet(text=a, source=f"achievements[{i}]") for i, a in
                    rank(list(enumerate(profile.achievements)), lambda t: text_relevance(t[1], ctx, catalog))[: opt.max_achievements]]

    return ResumeContent(header=header, summary=summary or "", summary_sources=summary_sources or [], skills=skills,
                         experience=experience, projects=projects, education=education, certifications=certs,
                         achievements=achievements, notes=notes)
