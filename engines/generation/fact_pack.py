"""A compact pack of the candidate's VERIFIED facts that are most relevant to one job.

Both the deterministic writers and the (optional) Claude writer start from this pack, so neither can mention
anything that is not in the verified profile.
"""
import re
from dataclasses import dataclass, field

from ai.schemas.candidate_profile import CandidateProfile
from engines.generation.render import format_period
from engines.generation.resume_customizer import JobContext, rank, skill_relevance, text_relevance
from engines.matching.skill_extractor import SkillCatalog
from engines.resume.resume_builder import _role_texts, _skill_display_map, ordered_roles


@dataclass
class RoleFact:
    index: int
    job_title: str | None
    company_name: str | None
    period: str
    is_current: bool
    relevance: float
    bullets: list[tuple[str, str]] = field(default_factory=list)  # (verbatim text, profile path)


@dataclass
class ProjectFact:
    index: int
    name: str | None
    description: str | None
    technologies: list[str]
    bullets: list[tuple[str, str]]
    relevance: float


@dataclass
class FactPack:
    name: str | None
    title: str | None
    years: float | None
    job_title: str | None
    company: str | None
    matched_skills: list[str]            # JD-required/preferred skills the candidate genuinely has (their wording)
    top_skills: list[str]                # the candidate's most job-relevant skills (their wording)
    roles: list[RoleFact]                # most relevant first
    projects: list[ProjectFact]          # most relevant first
    achievements: list[tuple[str, str]]  # metric-bearing facts, most relevant first
    best_responsibility: str | None      # the JD duty that best matches the candidate's work
    education: str | None
    education_source: str | None
    jd_must: list[str]
    jd_responsibilities: list[str]

    def for_ai(self) -> dict:
        """What may be sent to an external model: facts only - NO name, email, phone, links or location."""
        return {
            "target_job_title": self.job_title, "target_company": self.company, "current_title": self.title, "years_of_experience": self.years,
            "skills_matching_the_job": self.matched_skills, "other_top_skills": [s for s in self.top_skills if s not in self.matched_skills],
            "roles": [{"title": r.job_title, "company": r.company_name, "period": r.period, "facts": [t for t, _ in r.bullets]} for r in self.roles[:3]],
            "projects": [{"name": p.name, "description": p.description, "technologies": p.technologies, "facts": [t for t, _ in p.bullets]} for p in self.projects[:2]],
            "measurable_results": [t for t, _ in self.achievements[:3]], "education": self.education,
            "job_requirements": self.jd_must[:8], "job_responsibilities": self.jd_responsibilities[:5],
        }


def _short(text: str, n: int = 110) -> str:
    text = re.sub(r"\s+", " ", text).strip().rstrip(".")
    return text if len(text) <= n else text[: n - 1].rsplit(" ", 1)[0] + "..."


def build_fact_pack(profile: CandidateProfile, ctx: JobContext, catalog: SkillCatalog, bullets_per_role: int = 3) -> FactPack:
    display = _skill_display_map(profile, catalog)
    ranked = rank(list(display), lambda s: skill_relevance(s, ctx, catalog))
    wanted = ctx.must | ctx.preferred
    matched = [display[s] for s in ranked if s in wanted]
    top = [display[s] for s in ranked][:8]

    roles = []
    for idx, job in ordered_roles(profile):
        texts = _role_texts(job, idx)
        rel = rank(texts, lambda t: text_relevance(t[0], ctx, catalog))
        score = sum(text_relevance(t, ctx, catalog) for t, _ in texts)
        roles.append(RoleFact(idx, job.job_title, job.company_name, format_period(job.start_date, job.end_date, job.is_current),
                              job.is_current, score, rel[:bullets_per_role]))
    roles = rank(roles, lambda r: r.relevance)

    projects = []
    for idx, p in enumerate(profile.projects):
        texts = [(t, f"projects[{idx}].responsibilities[{j}]") for j, t in enumerate(p.responsibilities)]
        texts += [(t, f"projects[{idx}].achievements[{j}]") for j, t in enumerate(p.achievements)]
        score = sum(skill_relevance(t, ctx, catalog) for t in p.technologies) + sum(text_relevance(t, ctx, catalog) for t, _ in texts)
        projects.append(ProjectFact(idx, p.project_name, p.description, list(p.technologies),
                                    rank(texts, lambda t: text_relevance(t[0], ctx, catalog))[:2], score))
    projects = rank(projects, lambda p: p.relevance)

    metric = re.compile(r"\d")
    ach = [t for r in roles for t in r.bullets if metric.search(t[0])]
    ach += [(a, f"achievements[{i}]") for i, a in enumerate(profile.achievements)]
    ach += [t for p in projects for t in p.bullets if metric.search(t[0])]
    ach = rank(ach, lambda t: text_relevance(t[0], ctx, catalog))

    best_resp = None
    if ctx.responsibilities and roles:
        from engines.matching.match_scorer import content_tokens
        role_tokens = set()
        for r in roles[:2]:
            for t, _ in r.bullets:
                role_tokens |= content_tokens(t, catalog)
        scored = [(len(content_tokens(r, catalog) & role_tokens) / max(1, len(content_tokens(r, catalog))), r) for r in ctx.responsibilities]
        top_r = max(scored, key=lambda s: s[0])
        best_resp = _short(top_r[1]) if top_r[0] >= 0.2 else None

    edu = next(iter(sorted(enumerate(profile.education), key=lambda t: t[1].graduation_year or 0, reverse=True)), None)
    edu_text = None
    if edu and edu[1].degree and edu[1].institution:
        edu_text = f"{edu[1].degree}{' in ' + edu[1].specialization if edu[1].specialization else ''} from {edu[1].institution}"
    return FactPack(
        name=profile.contact.full_name, title=profile.summary.current_job_title, years=profile.summary.total_experience_years,
        job_title=ctx.job_title, company=ctx.company_name, matched_skills=matched, top_skills=top, roles=roles, projects=projects,
        achievements=ach, best_responsibility=best_resp, education=edu_text, education_source=f"education[{edu[0]}]" if edu and edu_text else None,
        jd_must=sorted(ctx.must), jd_responsibilities=[_short(r, 140) for r in ctx.responsibilities[:6]],
    )
