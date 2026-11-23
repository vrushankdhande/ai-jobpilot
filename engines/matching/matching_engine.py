"""The matching pipeline: profile + parsed job -> eligibility + score + explanation."""
import hashlib
import json

from ai.schemas.candidate_profile import CandidateProfile
from ai.schemas.job_schema import JobAnalysis
from ai.schemas.match_schema import MatchReport
from config.scoring_config import Preferences, ScoringConfig, preferences_hash
from engines.matching.candidate_view import build_candidate_view
from engines.matching.eligibility_checker import check_eligibility
from engines.matching.explainability import build_report
from engines.matching.match_scorer import score_match
from engines.matching.skill_analysis import analyze_skills
from engines.matching.skill_extractor import SkillCatalog


def compute_input_hash(
    profile: CandidateProfile, job_id: str, analysis: JobAnalysis, cfg: ScoringConfig, prefs: Preferences, catalog_digest: str = ""
) -> str:
    """Identical inputs + scoring version + configuration -> identical hash (used to avoid duplicate matches)."""
    parts = {
        "profile": [profile.profile_id, profile.profile_version], "job": job_id,
        "parser": [analysis.parser, analysis.parser_version, analysis.jd_text_sha256],
        "scoring": [cfg.scoring_version, cfg.config_hash], "prefs": preferences_hash(prefs), "skills": catalog_digest,
    }
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()


def run_match(
    profile: CandidateProfile, analysis: JobAnalysis, job_id: str, cfg: ScoringConfig, prefs: Preferences,
    catalog: SkillCatalog, ai_model: str | None = None,
) -> MatchReport:
    cand = build_candidate_view(profile, catalog, cfg.seniority.bands)
    sk = analyze_skills(analysis, cand, catalog)
    eligibility = check_eligibility(analysis, cand, sk, prefs, cfg)
    scored = score_match(analysis, cand, sk, prefs, cfg, catalog)
    return build_report(
        job_id=job_id, job=analysis, cand=cand, sk=sk, elig=eligibility, scored=scored, cfg=cfg,
        input_hash=compute_input_hash(profile, job_id, analysis, cfg, prefs, catalog.digest), ai_model=ai_model,
    )
