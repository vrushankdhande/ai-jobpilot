"""Build the explainable match report from the deterministic results.

Every sentence comes from computed facts (scores, matched/missing skills, check results, evidence
snippets taken from the job description and the profile). The optional Claude summary is extra wording
only: it is rejected if it contains numbers that are not in the facts, and it never changes a score.
"""
import json
import re

from ai.claude_client import AIResponseError, AIUnavailable, ClaudeClient, load_prompt
from ai.schemas.job_schema import JobAnalysis
from ai.schemas.match_schema import AIExplanation, Eligibility, MatchReport, RoleFitInfo
from config.scoring_config import ScoringConfig
from engines.matching.candidate_view import CandidateView
from engines.matching.match_scorer import ScoreResult
from engines.matching.skill_analysis import SkillAnalysis

_LABELS = {
    "technical_skills": "Technical skills", "experience": "Relevant experience", "projects": "Projects",
    "responsibilities": "Responsibilities", "education": "Education", "location": "Location & work mode",
    "seniority": "Seniority",
}


def _recommendation(score: float, elig: Eligibility, coverage: float, cfg: ScoringConfig):
    if elig.status == "FAIL":
        return "NOT_ELIGIBLE", ("Not eligible: " + "; ".join(elig.failed_checks) +
                                f". The match score ({score:g}/100) is shown separately and does not override this.")
    if elig.status == "UNKNOWN" and not elig.can_proceed:
        return "ELIGIBILITY_UNRESOLVED", ("Eligibility is unresolved (" + ", ".join(elig.unresolved_checks) +
                                          "). Confirm these manually before applying; automatic application is blocked.")
    if coverage < cfg.min_data_coverage:
        return "INSUFFICIENT_DATA", (f"Only {coverage:g}% of the scoring weight could be evaluated "
                                     f"(minimum {cfg.min_data_coverage:g}%). Add missing profile/job details.")
    if score >= cfg.recommendation_threshold:
        return "RECOMMENDED", f"Recommended: score {score:g}/100 meets the {cfg.recommendation_threshold:g} threshold and eligibility passed."
    return "BELOW_THRESHOLD", f"Not recommended: score {score:g}/100 is below the {cfg.recommendation_threshold:g} threshold."


def build_report(
    *, job_id: str, job: JobAnalysis, cand: CandidateView, sk: SkillAnalysis, elig: Eligibility,
    scored: ScoreResult, cfg: ScoringConfig, input_hash: str = "", ai_model: str | None = None,
) -> MatchReport:
    cats = {c.name: c for c in scored.categories}
    profile = cand.profile

    def headline(name: str) -> str:
        c = cats[name]
        if name in ("responsibilities", "projects"):  # first note is a methodology note, so quote evidence instead
            ev = next((e for e in c.evidence if e.candidate), None)
            tail = f" (e.g. {ev.candidate[:90]})" if ev else ""
            return f"{_LABELS[name]}: {c.ratio:.0%} fit{tail}"
        return f"{_LABELS[name]}: {c.notes[0]}" if c.notes else f"{_LABELS[name]}: {c.ratio:.0%} fit"

    strengths: list[str] = []
    if sk.matched_skills:
        total = len(sk.mandatory) + len(sk.preferred)
        strengths.append(f"Exactly matches {len(sk.matched_skills)} of {total} required/preferred skills: {', '.join(sk.matched_skills[:8])}")
    for name in ("experience", "seniority", "education", "responsibilities", "projects", "location"):
        c = cats[name]
        if c.status == "EVALUATED" and (c.ratio or 0) >= 0.8:
            strengths.append(headline(name))
    best_projects = [p for p in scored.project_relevance if p.relevance >= 0.5]
    if best_projects:
        strengths.append("Relevant projects: " + ", ".join(f"{p.project} ({', '.join(p.matched_skills[:4]) or 'related work'})" for p in best_projects[:3]))

    weaknesses: list[str] = []
    if sk.missing_mandatory:
        weaknesses.append("Missing mandatory skills: " + ", ".join(sk.missing_mandatory))
    for pm in sk.partial:
        weaknesses.append(f"Only a related (not equivalent) skill for {pm.required_skill}: {pm.candidate_skill} - {pm.reason}")
    if sk.missing_preferred:
        weaknesses.append("Missing preferred skills: " + ", ".join(sk.missing_preferred))
    for name in ("experience", "seniority", "education", "responsibilities", "projects", "location", "technical_skills"):
        c = cats[name]
        if c.status == "EVALUATED" and (c.ratio or 0) <= 0.5:
            weaknesses.append(headline(name))
    fit = scored.role_fit
    if fit is not None and fit.relation in ("adjacent", "different"):
        weaknesses.insert(0, "Role fit: " + fit.note)
    weaknesses += [f"Preference mismatch - {c.name}: {c.detail}" for c in elig.checks if c.status == "FAIL" and not c.hard]

    rejection = [c.detail or f"{c.name} failed" for c in elig.checks if c.status == "FAIL" and c.hard]
    rejection += [f"Unresolved: {c.name} - {c.detail}" for c in elig.checks if c.name in elig.unresolved_checks]

    limitations: list[str] = []
    for c in scored.categories:
        if c.status == "UNKNOWN":
            limitations.append(f"{_LABELS[c.name]} could not be scored: {c.notes[0] if c.notes else 'data missing'}")
    limitations += [f"Not confirmed ({c.name}): {c.detail}" for c in elig.checks
                    if c.status == "UNKNOWN" and c.name not in elig.unresolved_checks]
    if scored.data_coverage < 100:
        limitations.append(f"Score is based on {scored.data_coverage:g}% of the total weighting (data coverage).")
    n_skills = len(sk.mandatory) + len(sk.preferred)
    if 0 < n_skills < 3:
        limitations.append(f"Only {n_skills} skill(s) could be identified in the job description, so the technical-skills score rests on very little evidence.")
    if job.parser == "deterministic":
        limitations.append("The job description was parsed with rules only (no Claude); skill importance is inferred from wording.")
    limitations += [f"Job parsing: {w}" for w in job.warnings[:4]]
    if not cand.reviewed:
        limitations.append("The candidate profile has not been marked as reviewed.")
    limitations.append("Responsibility matching is lexical (skills and shared terms), not semantic embeddings.")

    code, rec = _recommendation(scored.match_score, elig, scored.data_coverage, cfg)

    label = job.job_title or "This job"
    where = f" at {job.company_name}" if job.company_name else ""
    parts = [f"{label}{where}: match score {scored.match_score:g}/100 ({scored.classification}), based on {scored.data_coverage:g}% data coverage.",
             f"Eligibility: {elig.status}" + (f" ({', '.join(elig.failed_checks)})" if elig.failed_checks else "") + "."]
    if strengths:
        parts.append("Strengths: " + strengths[0] + ".")
    if weaknesses:
        parts.append("Main concern: " + weaknesses[0] + ".")
    explanation = " ".join(parts)

    exp_notes = list(cats["experience"].notes)
    edu_notes = list(cats["education"].notes) + [c.detail for c in elig.checks if c.name == "education" and c.detail]
    loc_notes = list(cats["location"].notes)

    return MatchReport(
        job_id=job_id, profile_id=profile.profile_id, profile_version=profile.profile_version, resume_id=profile.resume_id,
        job_title=job.job_title, company_name=job.company_name,
        match_score=scored.match_score, classification=scored.classification, eligibility=elig,
        score_breakdown={c.name: (round(c.earned_points, 2) if c.status == "EVALUATED" else None) for c in scored.categories},
        category_details=scored.categories, data_coverage=scored.data_coverage, missing_data_policy=cfg.missing_data_policy,
        role_fit=(RoleFitInfo(relation=fit.relation, factor=fit.factor, job_family=fit.job_family, candidate_families=fit.candidate_families,
                              score_before_role_fit=scored.score_before_role_fit, note=fit.note) if fit is not None else None),
        matched_skills=sk.matched_skills, missing_mandatory_skills=sk.missing_mandatory,
        missing_preferred_skills=sk.missing_preferred, partial_matches=sk.partial, irrelevant_candidate_skills=sk.irrelevant[:15],
        experience_analysis=exp_notes, project_relevance=scored.project_relevance[:5],
        responsibility_analysis=scored.responsibility_notes[:8], education_analysis=edu_notes, location_analysis=loc_notes,
        strengths=strengths, weaknesses=weaknesses, rejection_reasons=rejection, limitations=limitations,
        explanation=explanation, recommendation=rec, recommendation_code=code,
        scoring_version=cfg.scoring_version, config_hash=cfg.config_hash, jd_parser=job.parser,
        ai_used=job.parser != "deterministic", ai_model=ai_model, input_hash=input_hash,
    )


# ---- optional Claude wording --------------------------------------------------------------------
def _facts(report: MatchReport) -> dict:
    """Facts sent to Claude: scores and skills only - no name, email, phone or free-text resume content."""
    return {
        "job_title": report.job_title, "match_score": report.match_score, "classification": report.classification,
        "eligibility": report.eligibility.status, "failed_checks": report.eligibility.failed_checks,
        "unresolved_checks": report.eligibility.unresolved_checks, "recommendation": report.recommendation,
        "matched_skills": report.matched_skills, "missing_mandatory_skills": report.missing_mandatory_skills,
        "missing_preferred_skills": report.missing_preferred_skills,
        "partial_matches": [f"{p.candidate_skill} (related to {p.required_skill})" for p in report.partial_matches],
        # category ratios only: the free-text strengths/weaknesses quote resume evidence (employers, projects)
        "category_scores": {c.name: (round(c.ratio, 2) if c.status == "EVALUATED" else "unknown") for c in report.category_details},
        "data_coverage": report.data_coverage,
    }


def ai_summary(report: MatchReport, client: ClaudeClient) -> str | None:
    facts = json.dumps(_facts(report), ensure_ascii=False)
    try:
        result = client.complete_json(load_prompt("resume_matching"), f"<facts>\n{facts}\n</facts>", AIExplanation)
    except (AIUnavailable, AIResponseError):
        return None
    allowed = set(re.findall(r"\d+(?:\.\d+)?", facts))
    if any(n not in allowed for n in re.findall(r"\d+(?:\.\d+)?", result.summary)):
        return None  # contains a number that is not in the facts -> reject
    if report.eligibility.status != "PASS" and re.search(r"\b(is|are) eligible\b|\bpasses? (?:all )?eligibility\b", result.summary, re.I):
        return None  # must not call an ineligible/unresolved candidate eligible
    return result.summary.strip()
