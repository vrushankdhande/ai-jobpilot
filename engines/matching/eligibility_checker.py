"""Hard eligibility checks - kept completely separate from the numeric match score.

Each check returns PASS / FAIL / UNKNOWN / NOT_APPLICABLE:
* missing candidate information is UNKNOWN - never FAIL and never silently PASS;
* preferred (nice-to-have) requirements are never checked here;
* only ``hard`` checks can reject; preference-based checks (salary, employment type) only warn.
"""
import re

from ai.schemas.job_schema import JobAnalysis
from ai.schemas.match_schema import Eligibility, EligibilityCheck
from config.scoring_config import Preferences, ScoringConfig
from engines.matching.candidate_view import CandidateView
from engines.matching.skill_analysis import SkillAnalysis

_COUNTRIES = ["india", "united states", "usa", "u.s.", "us", "united kingdom", "uk", "canada", "germany", "singapore",
              "australia", "uae", "netherlands", "ireland", "france"]


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9+#]{2,}", text.lower())) - {"the", "and", "of", "for", "certified", "certification"}


def _city(text: str) -> str:
    return re.split(r"[,(]", text)[0].strip().lower()


def candidate_cities(cand: CandidateView, prefs: Preferences) -> list[str]:
    """Cities used for location checks: stated preferences first, else the resume's location."""
    if prefs.locations:
        return [_city(l) for l in prefs.locations]
    return [_city(cand.location)] if cand.location else []


def location_matches(job_locations: list[str], cities: list[str]) -> bool:
    jd = {_city(l) for l in job_locations}
    return any(c in jd or any(c and c in j for j in jd) for c in cities)


def check_eligibility(
    job: JobAnalysis, cand: CandidateView, sk: SkillAnalysis, prefs: Preferences, cfg: ScoringConfig
) -> Eligibility:
    ecfg = cfg.eligibility
    checks: list[EligibilityCheck] = []

    def add(name, status, requirement=None, value=None, detail="", hard=True):
        checks.append(EligibilityCheck(name=name, status=status, hard=hard, requirement=requirement,
                                       candidate_value=value, detail=detail))

    # 1. minimum experience ---------------------------------------------------------
    if job.minimum_experience_years is None:
        add("min_experience", "NOT_APPLICABLE", detail="The job states no minimum experience.")
    elif cand.total_years is None:
        add("min_experience", "UNKNOWN", f">= {job.minimum_experience_years:g} years", None,
            "Candidate total experience could not be determined (missing dates).")
    elif cand.total_years + ecfg.experience_tolerance_years >= job.minimum_experience_years:
        add("min_experience", "PASS", f">= {job.minimum_experience_years:g} years", f"{cand.total_years:g} years",
            f"Candidate has {cand.total_years:g} years ({cand.years_source}).")
    else:
        add("min_experience", "FAIL", f">= {job.minimum_experience_years:g} years", f"{cand.total_years:g} years",
            f"Candidate has {cand.total_years:g} years; {job.minimum_experience_years:g} required.")

    # 2. maximum experience (only when explicitly restrictive) -----------------------------
    if job.maximum_experience_years is None or not job.maximum_is_restrictive:
        add("max_experience", "NOT_APPLICABLE", detail="No restrictive maximum stated.")
    elif cand.total_years is None:
        add("max_experience", "UNKNOWN", f"<= {job.maximum_experience_years:g} years", None, "Candidate experience unknown.")
    elif cand.total_years <= job.maximum_experience_years:
        add("max_experience", "PASS", f"<= {job.maximum_experience_years:g} years", f"{cand.total_years:g} years")
    else:
        add("max_experience", "FAIL", f"<= {job.maximum_experience_years:g} years", f"{cand.total_years:g} years",
            "Candidate exceeds the stated maximum experience.")

    # 3. mandatory technical skills ---------------------------------------------------------
    if not sk.mandatory:
        add("mandatory_skills", "NOT_APPLICABLE", detail="No mandatory skills were identified in the job.")
    elif not cand.skills:
        add("mandatory_skills", "UNKNOWN", ", ".join(r.label for r in sk.mandatory), None,
            "The candidate profile lists no skills.")
    else:
        met_by = ecfg.mandatory_skill_equivalent_credit
        unmet = [r for r in sk.mandatory if r.credit < met_by]                     # exact (1.0) or a documented near-equivalent is met
        equiv = [f"{r.label} (via {r.best.candidate_skill})" for r in sk.mandatory if not r.exact and r.credit >= met_by]
        frac = len(unmet) / len(sk.mandatory)
        req = ", ".join(r.label for r in sk.mandatory)
        note = (" Met through equivalent skills in the profile: " + "; ".join(equiv) + ".") if equiv else ""
        if not unmet:
            add("mandatory_skills", "PASS", req, "all matched", "All mandatory skills are present in the profile." + note)
        elif frac > ecfg.mandatory_skills_fail_if_missing_fraction_above:
            parts = [f"{r.label} (partial via {r.best.candidate_skill})" if r.credit > 0 else r.label for r in unmet]
            add("mandatory_skills", "FAIL", req, f"{len(sk.mandatory) - len(unmet)}/{len(sk.mandatory)} matched",
                "Mandatory skills not found in the verified profile: " + "; ".join(parts) +
                ". Loosely related skills do not satisfy a mandatory requirement." + note)
        else:
            add("mandatory_skills", "PASS", req, f"{len(sk.mandatory) - len(unmet)}/{len(sk.mandatory)} matched",
                f"Missing share {frac:.0%} is within the configured tolerance." + note)

    # 4. mandatory education ---------------------------------------------------------------------
    need = [e for e in job.education_requirements if e.mandatory and e.level_rank]
    if not need:
        add("education", "NOT_APPLICABLE", detail="No mandatory education requirement.")
    else:
        top = max(need, key=lambda e: e.level_rank)
        if cand.education_rank is None:
            add("education", "UNKNOWN", top.level, None, "Candidate education level could not be determined.")
        elif cand.education_rank >= top.level_rank:
            add("education", "PASS", top.level, cand.education_text, "Education level meets the requirement.")
        elif top.equivalent_experience_accepted:
            add("education", "UNKNOWN", top.level, cand.education_text,
                "Below the stated degree level, but the job accepts equivalent experience - confirm manually.")
        else:
            add("education", "FAIL", top.level, cand.education_text, "Education level is below the requirement.")

    # 5. mandatory certifications ----------------------------------------------------------------
    certs = [c for c in job.certification_requirements if c.mandatory]
    if not certs:
        add("certifications", "NOT_APPLICABLE", detail="No mandatory certification.")
    else:
        have = [_tokens(c) for c in cand.certifications]
        missing = []
        for c in certs:
            t = _tokens(c.name)
            if not t or not any(len(t & h) / len(t) >= 0.6 for h in have):
                missing.append(c.name)
        if not missing:
            add("certifications", "PASS", ", ".join(c.name for c in certs), "present")
        elif not cand.certifications and not cand.reviewed:
            add("certifications", "UNKNOWN", ", ".join(missing), None,
                "No certifications in the profile and the profile has not been reviewed.")
        else:
            add("certifications", "FAIL", ", ".join(missing), ", ".join(cand.certifications) or "none listed",
                "Required certification not found in the profile.")

    # 6. location / work mode ------------------------------------------------------------------------
    cities = candidate_cities(cand, prefs)
    if job.work_mode == "remote" or (not job.location and job.work_mode is None):
        if job.work_mode == "remote" and prefs.remote is False:
            add("location", "FAIL", "remote", "prefers not remote", "Candidate is not open to remote roles.", hard=False)
        else:
            add("location", "NOT_APPLICABLE", detail="Remote or no location restriction stated.")
    elif not job.location:
        add("location", "UNKNOWN", job.work_mode, None, "No city given for an on-site/hybrid role.", hard=True)
    elif not cities:
        add("location", "UNKNOWN", ", ".join(job.location), None, "Candidate location/preference is unknown.")
    elif location_matches(job.location, cities):
        add("location", "PASS", ", ".join(job.location), ", ".join(cities))
    elif prefs.open_to_relocation is True:
        add("location", "PASS", ", ".join(job.location), ", ".join(cities), "Different city; candidate is open to relocation.")
    elif prefs.open_to_relocation is False:
        add("location", "FAIL", ", ".join(job.location), ", ".join(cities), "Different city and candidate is not open to relocation.")
    else:
        add("location", "UNKNOWN", ", ".join(job.location), ", ".join(cities), "Different city; relocation preference unknown.")

    # 7. employment type (candidate preference) ------------------------------------------------------------
    if not job.employment_type:
        add("employment_type", "NOT_APPLICABLE", hard=False, detail="Not stated.")
    elif not prefs.employment_types:
        add("employment_type", "UNKNOWN", job.employment_type, None, "Candidate employment-type preference is not set.", hard=False)
    elif job.employment_type in [e.lower() for e in prefs.employment_types]:
        add("employment_type", "PASS", job.employment_type, ", ".join(prefs.employment_types), hard=False)
    else:
        add("employment_type", "FAIL", job.employment_type, ", ".join(prefs.employment_types),
            "Employment type differs from the candidate's preference.", hard=False)

    # 8. work authorisation -----------------------------------------------------------------------------------
    if not job.work_authorization_requirement:
        add("work_authorization", "NOT_APPLICABLE", detail="No work-authorisation requirement stated.")
    elif not prefs.work_authorization:
        add("work_authorization", "UNKNOWN", job.work_authorization_requirement, None,
            "Candidate work authorisation is not set in job preferences.")
    else:
        text = job.work_authorization_requirement.lower()
        named = [c for c in _COUNTRIES if re.search(rf"(?<![a-z]){re.escape(c)}(?![a-z])", text)]
        mine = [c.lower() for c in prefs.work_authorization]
        if any(m in text for m in mine):
            add("work_authorization", "PASS", job.work_authorization_requirement, ", ".join(prefs.work_authorization))
        elif named:
            add("work_authorization", "FAIL", job.work_authorization_requirement, ", ".join(prefs.work_authorization),
                "The job requires authorisation for a country not in the candidate's list.")
        else:
            add("work_authorization", "UNKNOWN", job.work_authorization_requirement, ", ".join(prefs.work_authorization),
                "Could not tell which country the requirement refers to - confirm manually.")

    # 9. salary (candidate expectation vs advertised range) ---------------------------------------------------------
    hard_salary = ecfg.salary_is_hard
    if job.maximum_salary is None:
        add("salary", "NOT_APPLICABLE", hard=hard_salary, detail="No salary stated.")
    elif prefs.min_salary is None:
        add("salary", "UNKNOWN", f"up to {job.maximum_salary:,.0f} {job.currency or ''}".strip(), None,
            "Candidate salary expectation is not set.", hard=hard_salary)
    elif prefs.salary_currency and job.currency and prefs.salary_currency.upper() != job.currency.upper():
        add("salary", "UNKNOWN", f"{job.currency}", f"{prefs.salary_currency}", "Currencies differ.", hard=hard_salary)
    elif job.maximum_salary >= prefs.min_salary:
        add("salary", "PASS", f"up to {job.maximum_salary:,.0f}", f">= {prefs.min_salary:,.0f}", hard=hard_salary)
    else:
        add("salary", "FAIL", f"up to {job.maximum_salary:,.0f}", f">= {prefs.min_salary:,.0f}",
            "The advertised maximum is below the candidate's expected minimum.", hard=hard_salary)

    # 10. other explicit requirements -------------------------------------------------------------------------------------
    if job.other_requirements:
        add("other_requirements", "UNKNOWN", "; ".join(job.other_requirements)[:300], None,
            "Explicit conditions that need the candidate's confirmation.", hard=False)

    return _summarise(checks, cfg)


def _summarise(checks: list[EligibilityCheck], cfg: ScoringConfig) -> Eligibility:
    blocking = set(cfg.eligibility.blocking_unknown_checks)
    failed = [c.name for c in checks if c.status == "FAIL" and c.hard]
    unresolved = [c.name for c in checks if c.status == "UNKNOWN" and c.hard and c.name in blocking]
    warnings = [f"{c.name}: {c.detail or c.status}" for c in checks
                if (c.status == "FAIL" and not c.hard) or (c.status == "UNKNOWN" and c.name not in unresolved)]
    status = "FAIL" if failed else "UNKNOWN" if unresolved else "PASS"
    return Eligibility(status=status, checks=checks, failed_checks=failed, unresolved_checks=unresolved,
                       soft_warnings=warnings,
                       can_proceed=status == "PASS" or (status == "UNKNOWN" and cfg.eligibility.allow_unknown_to_proceed))
