"""Compare a job's skill requirements with the candidate's skills (exact / partial / missing)."""
from dataclasses import dataclass, field

from ai.schemas.job_schema import JobAnalysis
from ai.schemas.match_schema import PartialMatch
from engines.matching.candidate_view import CandidateView
from engines.matching.skill_extractor import SkillCatalog, SkillMatch


@dataclass
class RequirementResult:
    label: str  # "Python" or "PyTorch or TensorFlow"
    alternatives: list[str]
    best: SkillMatch
    required_skill: str  # the alternative that gave the best match

    @property
    def credit(self) -> float:
        return self.best.credit

    @property
    def exact(self) -> bool:
        return self.best.kind == "exact"


@dataclass
class SkillAnalysis:
    mandatory: list[RequirementResult] = field(default_factory=list)
    preferred: list[RequirementResult] = field(default_factory=list)
    matched_skills: list[str] = field(default_factory=list)
    missing_mandatory: list[str] = field(default_factory=list)
    missing_preferred: list[str] = field(default_factory=list)
    partial: list[PartialMatch] = field(default_factory=list)
    irrelevant: list[str] = field(default_factory=list)

    @property
    def mandatory_ratio(self) -> float | None:
        return sum(r.credit for r in self.mandatory) / len(self.mandatory) if self.mandatory else None

    @property
    def preferred_ratio(self) -> float | None:
        return sum(r.credit for r in self.preferred) / len(self.preferred) if self.preferred else None


def _best(alternatives: list[str], have: set[str], catalog: SkillCatalog) -> tuple[SkillMatch, str]:
    best, req = SkillMatch("none"), alternatives[0]
    for alt in alternatives:
        m = catalog.match(alt, have)
        if m.credit > best.credit or (m.kind == "exact" and best.kind != "exact"):
            best, req = m, alt
    return best, req


def analyze_skills(job: JobAnalysis, cand: CandidateView, catalog: SkillCatalog) -> SkillAnalysis:
    have = cand.skill_names
    out = SkillAnalysis()

    def resolve(alternatives: list[str]) -> RequirementResult:
        best, req = _best(alternatives, have, catalog)
        return RequirementResult(" or ".join(alternatives), alternatives, best, req)

    out.mandatory = [resolve(g) for g in job.mandatory_groups]
    out.preferred = [resolve([s]) for s in job.preferred_skills if s not in job.mandatory_skills]

    for r in out.mandatory + out.preferred:
        if r.exact:
            name = r.best.candidate_skill
            if name not in out.matched_skills:
                out.matched_skills.append(name)
        elif r.credit > 0:
            out.partial.append(PartialMatch(
                candidate_skill=r.best.candidate_skill, required_skill=r.required_skill,
                credit=round(r.credit, 2), reason=r.best.reason or "Related, but not equivalent"))
    out.missing_mandatory = [r.label for r in out.mandatory if r.credit == 0]
    out.missing_preferred = [r.label for r in out.preferred if r.credit == 0]

    jd_all = {s.name for s in job.skills}
    related_to_jd = set()
    for (src, dst) in catalog.edges:
        if dst in jd_all:
            related_to_jd.add(src)
    explicit = {s for s, srcs in cand.skills.items() if "skills section" in srcs}
    out.irrelevant = sorted(s for s in explicit if s not in jd_all and s not in related_to_jd)
    return out
