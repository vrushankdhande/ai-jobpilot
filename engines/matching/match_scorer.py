"""Transparent weighted match scoring (the source of truth for every number).

    category earned points = ratio (0..1) x category weight
    overall score = sum(earned) / sum(evaluated weights) x 100     (missing_data_policy = normalize)
                    or sum(earned)                                  (missing_data_policy = zero)

A category that cannot be evaluated because data is missing is reported as UNKNOWN (never 0 or full
marks) and lowers ``data_coverage``. Weights, thresholds and credits all come from
config/scoring_weights.yaml. Claude is never involved in these numbers.
"""
import re
from dataclasses import dataclass

from ai.schemas.job_schema import JobAnalysis, SENIORITY_LEVELS
from ai.schemas.match_schema import CategoryScore, Evidence, ProjectRelevance
from config.scoring_config import Preferences, ScoringConfig
from engines.matching.candidate_view import CandidateView, level_rank
from engines.matching.eligibility_checker import candidate_cities, location_matches
from engines.matching.role_fit import RoleFit, role_fit
from engines.matching.skill_analysis import SkillAnalysis
from engines.matching.skill_extractor import SkillCatalog

CLASSIFICATIONS = ["Excellent Match", "Strong Match", "Good Match", "Moderate Match", "Low Match"]


@dataclass
class ScoreResult:
    categories: list[CategoryScore]
    match_score: float
    data_coverage: float
    classification: str
    project_relevance: list[ProjectRelevance]
    responsibility_notes: list[str]
    role_fit: "RoleFit | None" = None
    score_before_role_fit: float | None = None


def classify(score: float, cfg: ScoringConfig) -> str:
    t = cfg.thresholds
    if score >= t.excellent:
        return "Excellent Match"
    if score >= t.strong:
        return "Strong Match"
    if score >= t.good:
        return "Good Match"
    if score >= t.moderate:
        return "Moderate Match"
    return "Low Match"


def _cat(name: str, cfg: ScoringConfig, ratio: float | None, notes: list[str], evidence: list[Evidence] | None = None) -> CategoryScore:
    w = cfg.weights[name]
    if ratio is None:
        return CategoryScore(name=name, weight=w, earned_points=0.0, status="UNKNOWN", ratio=None, notes=notes, evidence=evidence or [])
    ratio = max(0.0, min(1.0, ratio))
    return CategoryScore(name=name, weight=w, earned_points=round(ratio * w, 4), status="EVALUATED", ratio=round(ratio, 4),
                         notes=notes, evidence=evidence or [])


def _blend(parts: list[tuple[float | None, float, str]]):
    """[(ratio|None, share, label)] -> (ratio|None, notes). Unavailable parts hand their share to the others."""
    avail = [(r, s, l) for r, s, l in parts if r is not None]
    if not avail:
        return None, []
    total = sum(s for _, s, _ in avail)
    notes = [f"{l} not evaluated (data unavailable); its share was given to the other part(s)" for r, s, l in parts if r is None]
    return sum(r * s for r, s, _ in avail) / total, notes


# --------------------------------------------------------------------------- 1. technical skills
def score_skills(job: JobAnalysis, sk: SkillAnalysis, cfg: ScoringConfig) -> CategoryScore:
    m, p = sk.mandatory_ratio, sk.preferred_ratio
    if m is None and p is None:
        return _cat("technical_skills", cfg, None, ["The job lists no mandatory or preferred skills to compare."])
    ratio, notes = _blend([(m, cfg.skills.mandatory_share, "Mandatory skills"), (p, cfg.skills.preferred_share, "Preferred skills")])
    if m is not None:
        notes.append(f"Mandatory skills: {sum(r.credit for r in sk.mandatory):.1f}/{len(sk.mandatory)} credit "
                     f"({sum(1 for r in sk.mandatory if r.exact)} exact, {len(sk.partial)} partial overall)")
    if p is not None:
        notes.append(f"Preferred skills: {sum(r.credit for r in sk.preferred):.1f}/{len(sk.preferred)} credit")
    ev = [Evidence(jd=f"mandatory: {r.label}", candidate=(r.best.candidate_skill if r.credit else "not found"))
          for r in sk.mandatory[:6]]
    return _cat("technical_skills", cfg, ratio, notes, ev)


# --------------------------------------------------------------------------- 2. experience
def score_experience(job: JobAnalysis, cand: CandidateView, sk: SkillAnalysis, cfg: ScoringConfig, catalog: SkillCatalog) -> CategoryScore:
    notes: list[str] = []
    fit = None
    if job.minimum_experience_years is not None and cand.total_years is not None:
        mn = job.minimum_experience_years
        fit = 1.0 if mn <= 0 else min(1.0, cand.total_years / mn)
        if job.maximum_is_restrictive and job.maximum_experience_years is not None and cand.total_years > job.maximum_experience_years:
            fit, _ = 0.5, notes.append(f"Candidate exceeds the stated maximum of {job.maximum_experience_years:g} years")
        notes.append(f"Total experience {cand.total_years:g} years vs {mn:g}+ required ({cand.years_source})")
    elif job.minimum_experience_years is not None:
        notes.append("Candidate total experience is unknown (missing dates)")

    relevance = None
    if cand.profile.work_experience:
        targets = [g for g in job.mandatory_groups] or [[s] for s in job.preferred_skills]
        comps = []
        if targets:
            shown = sum(1 for g in targets if any(a in cand.demonstrated for a in g))
            comps.append(shown / len(targets))
            notes.append(f"{shown}/{len(targets)} of the job's key skills are demonstrated in work history/projects")
        yrs = []
        for se in job.skill_experience:
            y = cand.skill_years(se.skill, catalog)
            if y is None:
                notes.append(f"{se.skill}: used in a role without dates, duration unknown")
            else:
                yrs.append(min(1.0, y / se.years))
                notes.append(f"{se.skill}: {y:g} years in dated roles vs {se.years:g}+ required")
        if yrs:
            comps.append(sum(yrs) / len(yrs))
        relevance = sum(comps) / len(comps) if comps else None
    else:
        notes.append("No work experience entries in the profile")

    ratio, extra = _blend([(fit, cfg.experience.years_fit_share, "Years fit"), (relevance, cfg.experience.relevance_share, "Relevance")])
    return _cat("experience", cfg, ratio, notes + extra)


# --------------------------------------------------------------------------- 3. projects
def score_projects(job: JobAnalysis, cand: CandidateView, cfg: ScoringConfig, catalog: SkillCatalog):
    skills = job.all_scored_skills or job.mentioned_skills
    if not cand.profile.projects:
        return _cat("projects", cfg, None, ["No projects in the profile"]), []
    if not skills:
        return _cat("projects", cfg, None, ["The job lists no skills to compare projects against"]), []
    pcfg = cfg.projects
    results = []
    for p in cand.profile.projects:
        have = {catalog.normalize_or_raw(t) for t in p.technologies}
        blob = " ".join([p.description or "", *p.responsibilities, *p.achievements])
        have |= {m.canonical for m in catalog.find_in_text(blob)}
        credit, matched = 0.0, []
        for s in skills:
            r = catalog.match(s, have)
            credit += r.credit
            if r.kind == "exact":
                matched.append(s)
        rel = min(1.0, credit / min(len(skills), pcfg.skills_for_full_credit))
        if p.achievements:
            rel = min(1.0, rel + pcfg.outcome_bonus if rel > 0 else 0.0)
        results.append(ProjectRelevance(project=p.project_name or "Unnamed project", relevance=round(rel, 3), matched_skills=matched))
    top = sorted(results, key=lambda r: r.relevance, reverse=True)
    best = top[: pcfg.top_n]
    ratio = sum(r.relevance for r in best) / len(best)
    notes = [f"Mean relevance of the best {len(best)} project(s); a project covering {pcfg.skills_for_full_credit}+ JD skills is fully relevant"]
    ev = [Evidence(jd=", ".join(r.matched_skills) or None, candidate=r.project) for r in best if r.relevance > 0]
    return _cat("projects", cfg, ratio, notes, ev), top


# --------------------------------------------------------------------------- 4. responsibilities
_STOP = set("a an the and or of to in for with on at by from as is are be will can you your our we their this that these those it its "
            "into across using use used within including etc such new strong team teams work working role ability able".split())
_SYN = {"develop": "build", "create": "build", "implement": "build", "design": "build", "construct": "build", "engineer": "build",
        "productionize": "deploy", "release": "deploy", "ship": "deploy", "launch": "deploy", "analyse": "analyze",
        "optimise": "optimize", "improve": "optimize", "tune": "optimize", "collaborate": "partner", "cooperate": "partner",
        "mentor": "coach", "guide": "coach", "train": "coach", "monitor": "observe", "track": "observe", "own": "lead",
        "manage": "lead", "drive": "lead", "model": "ml", "models": "ml", "pipeline": "pipeline", "pipelines": "pipeline"}


_IRREGULAR = {"built": "build", "led": "lead", "ran": "run", "wrote": "write", "written": "write", "led": "lead",
              "shipped": "ship", "mentored": "mentor", "trained": "train", "deployed": "deploy", "tuned": "tune"}


def _stem(w: str) -> str:
    if w in _IRREGULAR:
        w = _IRREGULAR[w]
    else:
        for suf in ("ing", "ed", "es", "s", "ion", "ly"):
            if w.endswith(suf) and len(w) - len(suf) >= 4:
                w = w[: -len(suf)]
                if len(w) > 4 and w[-1] == w[-2] and w[-1] not in "aeiouls":  # shipping -> shipp -> ship
                    w = w[:-1]
                break
    return _SYN.get(w, w)


def content_tokens(text: str, catalog: SkillCatalog) -> set[str]:
    toks = {f"skill:{m.canonical}" for m in catalog.find_in_text(text)}
    for w in re.findall(r"[a-z]{3,}", text.lower()):
        if w not in _STOP:
            toks.add(_stem(w))
    return toks


def score_responsibilities(job: JobAnalysis, cand: CandidateView, cfg: ScoringConfig, catalog: SkillCatalog):
    if not job.responsibilities:
        return _cat("responsibilities", cfg, None, ["The job lists no responsibilities"]), []
    if not cand.statements:
        return _cat("responsibilities", cfg, None, ["The profile has no responsibility/achievement statements"]), []
    rc = cfg.responsibilities
    cand_tokens = [(content_tokens(t, catalog), t, src) for t, src in cand.statements]
    credits, notes, ev = [], [], []
    for resp in job.responsibilities:
        jt = content_tokens(resp, catalog)
        if len(jt) < 2:
            continue
        best, best_stmt, best_src = 0.0, None, None
        for ct, stmt, src in cand_tokens:
            o = len(jt & ct) / len(jt)
            if o > best:
                best, best_stmt, best_src = o, stmt, src
        credit = 0.0 if best < rc.no_credit_overlap else min(1.0, (best - rc.no_credit_overlap) / (rc.full_credit_overlap - rc.no_credit_overlap))
        credits.append(credit)
        notes.append(f"{'Covered' if credit >= 0.8 else 'Partly covered' if credit > 0 else 'Not evidenced'} ({best:.0%} overlap): {resp[:90]}")
        if credit > 0:
            ev.append(Evidence(jd=resp[:160], candidate=f"{best_stmt[:160]} [{best_src}]"))
    if not credits:
        return _cat("responsibilities", cfg, None, ["No usable responsibilities to compare"]), []
    head = ["Lexical/skill overlap between job duties and the candidate's stated responsibilities and achievements (no embeddings)"]
    return _cat("responsibilities", cfg, sum(credits) / len(credits), head, ev[:6]), notes


# --------------------------------------------------------------------------- 5. education
def score_education(job: JobAnalysis, cand: CandidateView, cfg: ScoringConfig) -> CategoryScore:
    ec = cfg.education
    reqs = [e for e in job.education_requirements if e.level_rank]
    if not reqs:
        return _cat("education", cfg, ec.none_required_credit, ["No education requirement stated - nothing to penalise"])
    if cand.education_rank is None:
        return _cat("education", cfg, None, ["Candidate education level could not be determined"])
    mandatory = [e for e in reqs if e.mandatory]
    target = max(mandatory or reqs, key=lambda e: e.level_rank)
    diff = cand.education_rank - target.level_rank
    ev = [Evidence(jd=target.text[:160], candidate=cand.education_text)]
    if diff >= 0:
        return _cat("education", cfg, 1.0, [f"Meets the {target.level} requirement"], ev)
    if not target.mandatory:
        return _cat("education", cfg, ec.preferred_unmet_credit, [f"Preferred {target.level} not met (optional, small penalty)"], ev)
    credit = ec.one_level_below_credit if diff == -1 else ec.two_levels_below_credit
    return _cat("education", cfg, credit, [f"Below the required {target.level} level"], ev)


# --------------------------------------------------------------------------- 6. location / work mode
def score_location(job: JobAnalysis, cand: CandidateView, prefs: Preferences, cfg: ScoringConfig):
    lc = cfg.location
    notes: list[str] = []
    mode = job.work_mode
    if mode is None and not job.location:
        return _cat("location", cfg, None, ["The job gives no location or work mode"]), ["No location or work mode in the job."]
    if mode == "remote":
        if prefs.remote is False:
            return _cat("location", cfg, 0.3, ["Remote job, but the candidate prefers not to work remotely"]), ["Remote role; candidate prefers otherwise."]
        n = "Remote role - location independent" + ("" if prefs.remote else " (candidate remote preference not set)")
        return _cat("location", cfg, 1.0, [n]), [n]
    cities = candidate_cities(cand, prefs)
    if not job.location:
        return _cat("location", cfg, None, [f"{mode} role without a stated city"]), [f"{mode} role without a city."]
    if not cities:
        return _cat("location", cfg, None, ["Candidate location/preference unknown"]), ["Candidate location unknown."]
    mode_pref = {"hybrid": prefs.hybrid, "onsite": prefs.onsite}.get(mode)
    if location_matches(job.location, cities):
        if mode_pref is False:
            return _cat("location", cfg, 0.3, [f"City matches but candidate prefers not to work {mode}"]), [f"City matches; {mode} not preferred."]
        n = f"Job city {', '.join(job.location)} matches the candidate ({', '.join(cities)})"
        return _cat("location", cfg, 1.0, [n]), [n]
    if prefs.open_to_relocation is True:
        return _cat("location", cfg, lc.city_mismatch_open_to_relocation, ["Different city; candidate is open to relocation"]), ["Relocation required (candidate open)."]
    if prefs.open_to_relocation is False:
        return _cat("location", cfg, lc.city_mismatch_not_open, ["Different city; candidate not open to relocation"]), ["Different city; not open to relocation."]
    return _cat("location", cfg, None, ["Different city and relocation preference unknown"]), ["Different city; relocation preference unknown."]


# --------------------------------------------------------------------------- 7. seniority
def score_seniority(job: JobAnalysis, cand: CandidateView, cfg: ScoringConfig) -> CategoryScore:
    jr, cr = level_rank(job.seniority_level), level_rank(cand.seniority_level)
    if jr is None:
        return _cat("seniority", cfg, None, ["The job states no seniority level"])
    if cr is None:
        return _cat("seniority", cfg, None, ["Candidate seniority cannot be derived (experience unknown)"])
    diff = max(-2, min(2, cr - jr))
    credit = cfg.seniority.credit_by_difference.get(str(diff), 0.0)
    notes = [f"Job level: {job.seniority_level} (from {job.seniority_source}); candidate level: {cand.seniority_level} "
             f"(from {cand.total_years:g} years of verified experience, not from job titles)"]
    if cand.title_level and cand.title_level != cand.seniority_level:
        notes.append(f"Current title suggests '{cand.title_level}', which differs from the experience-based level; the title was not used")
    return _cat("seniority", cfg, credit, notes)


# --------------------------------------------------------------------------- overall
def score_match(
    job: JobAnalysis, cand: CandidateView, sk: SkillAnalysis, prefs: Preferences, cfg: ScoringConfig, catalog: SkillCatalog
) -> ScoreResult:
    projects_cat, project_rel = score_projects(job, cand, cfg, catalog)
    resp_cat, resp_notes = score_responsibilities(job, cand, cfg, catalog)
    loc_cat, _ = score_location(job, cand, prefs, cfg)
    cats = [
        score_skills(job, sk, cfg),
        score_experience(job, cand, sk, cfg, catalog),
        projects_cat, resp_cat,
        score_education(job, cand, cfg),
        loc_cat,
        score_seniority(job, cand, cfg),
    ]
    earned = sum(c.earned_points for c in cats)
    evaluated = sum(c.weight for c in cats if c.status == "EVALUATED")
    total_w = sum(c.weight for c in cats)
    if cfg.missing_data_policy == "normalize":
        score = earned / evaluated * 100 if evaluated else 0.0
    else:
        score = earned
    score = round(max(0.0, min(100.0, score)), 1)
    fit = role_fit(job.job_title, cand.profile, cfg.role_fit)
    before = score
    score = round(score * fit.factor, 1)                     # function mismatch lowers the score; it is never hidden (see the report's role_fit)
    coverage = round(evaluated / total_w * 100, 1) if total_w else 0.0
    return ScoreResult(cats, score, coverage, classify(score, cfg), project_rel, resp_notes, fit, before)
