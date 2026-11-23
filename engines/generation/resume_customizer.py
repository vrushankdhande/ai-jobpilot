"""Relevance ranking: which of the candidate's VERIFIED facts matter most for one job.

Nothing is created here: skills, bullets, projects and certifications are only *ranked*. Ties keep the candidate's
original order, so an unrelated job leaves the resume in its natural order.
"""
import re
from dataclasses import dataclass, field

from ai.schemas.job_schema import JobAnalysis
from engines.matching.match_scorer import content_tokens
from engines.matching.skill_extractor import SkillCatalog

_METRIC = re.compile(r"\d+(?:[.,]\d+)?\s*(?:%|x\b|k\b|ms\b)|[$₹€£]\s?\d|\b\d[\d,]{3,}\b", re.I)


@dataclass
class JobContext:
    """What this job asks for (from the Phase 3 analysis) - used only to rank the candidate's facts."""

    job_title: str | None
    company_name: str | None
    must: set[str] = field(default_factory=set)
    preferred: set[str] = field(default_factory=set)
    mentioned: set[str] = field(default_factory=set)
    responsibilities: list[str] = field(default_factory=list)
    resp_tokens: set[str] = field(default_factory=set)
    title_tokens: set[str] = field(default_factory=set)
    matched_skills: list[str] = field(default_factory=list)  # exact matches from the match report (if any)

    @property
    def all_skills(self) -> set[str]:
        return self.must | self.preferred | self.mentioned


def build_job_context(analysis: JobAnalysis, catalog: SkillCatalog, matched_skills: list[str] | None = None,
                      company_name: str | None = None, job_title: str | None = None) -> JobContext:
    resp = list(analysis.responsibilities)
    tokens: set[str] = set()
    for r in resp:
        tokens |= content_tokens(r, catalog)
    title = job_title or analysis.job_title
    return JobContext(
        job_title=title, company_name=company_name or analysis.company_name,
        must=set(analysis.mandatory_skills), preferred=set(analysis.preferred_skills), mentioned=set(analysis.mentioned_skills),
        responsibilities=resp, resp_tokens=tokens, title_tokens=content_tokens(title or "", catalog),
        matched_skills=list(matched_skills or []),
    )


def skill_relevance(skill: str, ctx: JobContext, catalog: SkillCatalog) -> float:
    """1.0 required, 0.7 preferred, 0.4 merely mentioned, partial for documented related skills, else 0."""
    canon = catalog.normalize_or_raw(skill)
    if canon in ctx.must:
        return 1.0
    if canon in ctx.preferred:
        return 0.7
    if canon in ctx.mentioned:
        return 0.4
    best = 0.0
    for need, weight in [(n, 1.0) for n in ctx.must] + [(n, 0.7) for n in ctx.preferred]:
        hit = catalog.edges.get((canon, need))
        if hit:
            best = max(best, hit[0] * weight * 0.5)  # related, never equal to an exact match
    return best


def text_relevance(text: str, ctx: JobContext, catalog: SkillCatalog) -> float:
    """Skills named in the text (weighted by how much the job wants them) + overlap with the job's duties/title."""
    score = 0.0
    for m in catalog.find_in_text(text):
        score += skill_relevance(m.canonical, ctx, catalog)
    toks = content_tokens(text, catalog)
    if toks and ctx.resp_tokens:
        score += 1.5 * len(toks & ctx.resp_tokens) / max(4, len(toks))
    if toks and ctx.title_tokens:
        score += 0.5 * len(toks & ctx.title_tokens) / max(2, len(ctx.title_tokens))
    if score > 0 and _METRIC.search(text):
        score += 0.25  # a measurable result is worth surfacing - but only if the bullet is relevant at all
    return score


def rank(items: list, key) -> list:
    """Stable sort by descending relevance (ties keep the original order)."""
    return [x for _, _, x in sorted(((-key(x), i, x) for i, x in enumerate(items)), key=lambda t: (t[0], t[1]))]
