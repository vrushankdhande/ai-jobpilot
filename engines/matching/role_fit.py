"""Role fit: does the job's FUNCTION (engineering, sales, finance ...) match the candidate's?

Why this exists: the weighted categories measure skills, years, projects, education and seniority, so a sales or finance posting that merely mentions
"machine learning" once would score well for an ML engineer (one generic skill matched = full skills marks, years and education add generic points).
Role fit is a small, deterministic, configurable factor on the final score:

    same function -> x same_factor (1.0)   adjacent function -> x adjacent_factor (0.8)   different function -> x different_factor (0.4)
    unknown function (a title no family recognises) or unknown candidate function -> no change

The function is read from job titles only (the candidate's titles come from the verified profile). Families, keywords and adjacency live in
config/scoring_weights.yaml (``role_fit``). It never rejects a job: eligibility stays a separate, hard-requirement check.
"""
import re
from dataclasses import dataclass, field

from ai.schemas.candidate_profile import CandidateProfile
from config.scoring_config import RoleFitCfg

# A strong technical noun makes a title technical even when another family's word appears ("Data Scientist, Marketing"),
# except for sales ("Sales Engineer" is a sales role).
_TECH_NOUNS = re.compile(r"(?<![a-z])(engineer|engineers|developer|scientist|architect|researcher|programmer)(?![a-z])", re.I)


@dataclass
class RoleFit:
    factor: float = 1.0
    relation: str = "unknown"                    # same | adjacent | different | unknown
    job_family: str | None = None
    candidate_families: list[str] = field(default_factory=list)
    note: str = ""


def _patterns(cfg: RoleFitCfg) -> list[tuple[str, re.Pattern]]:
    out = []
    for family, words in cfg.families.items():
        body = "|".join(re.escape(w.lower()).replace(r"\ ", r"[\s\-/]+") for w in words)
        out.append((family, re.compile(rf"(?<![a-z])(?:{body})(?![a-z])", re.I)))
    return out


def family_of(title: str | None, cfg: RoleFitCfg) -> str | None:
    """First family (in config order) whose keywords occur in the title; None if no family recognises it."""
    if not title:
        return None
    pats = _patterns(cfg)
    hit = next((f for f, rx in pats if rx.search(title)), None)
    if hit and hit not in ("sales", "technical") and "technical" in cfg.families and _TECH_NOUNS.search(title):
        return "technical"
    return hit


def candidate_families(profile: CandidateProfile, cfg: RoleFitCfg) -> list[str]:
    titles = [profile.summary.current_job_title, *[j.job_title for j in profile.work_experience]]
    fams: list[str] = []
    for t in titles:
        f = family_of(t, cfg)
        if f and f not in fams:
            fams.append(f)
    return fams


def role_fit(job_title: str | None, profile: CandidateProfile, cfg: RoleFitCfg) -> RoleFit:
    if not cfg.enabled:
        return RoleFit(note="Role-fit check is switched off.")
    jf = family_of(job_title, cfg)
    cf = candidate_families(profile, cfg)
    if jf is None:
        return RoleFit(candidate_families=cf, note="The job title does not identify a function, so role fit was not applied.")
    if not cf:
        return RoleFit(job_family=jf, note="The candidate's function could not be read from the profile's job titles, so role fit was not applied.")
    if jf in cf:
        return RoleFit(cfg.same_factor, "same", jf, cf, f"The job's function ({jf}) matches the candidate's ({', '.join(cf)}).")
    if any(jf in g and any(c in g for c in cf) for g in cfg.adjacent_groups):
        return RoleFit(cfg.adjacent_factor, "adjacent", jf, cf,
                       f"The job is in an adjacent function ({jf}) to the candidate's ({', '.join(cf)}); the score is reduced to x{cfg.adjacent_factor:g}.")
    return RoleFit(cfg.different_factor, "different", jf, cf,
                   f"The job is in a different function ({jf}) from the candidate's ({', '.join(cf)}); the score is reduced to x{cfg.different_factor:g}, "
                   "because skills, years and education alone cannot show a fit across functions.")
