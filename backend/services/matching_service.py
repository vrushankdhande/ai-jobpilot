"""Matching workflow: verified profile + job description -> saved, explainable match report."""
import logging
from datetime import datetime

from ai.claude_client import ClaudeClient, ai_status
from ai.schemas.candidate_profile import CandidateProfile
from ai.schemas.job_schema import JD_PARSER_VERSION, JobAnalysis, JobInput
from ai.schemas.match_schema import MatchReport
from config.scoring_config import Preferences, ScoringConfig
from config.settings import Settings
from database.repositories.jobs_repository import JobsRepository, job_content_hash
from database.repositories.matches_repository import MatchesRepository
from database.repositories.resumes_repository import ResumesRepository
from database.repositories.users_repository import UsersRepository
from engines.matching.explainability import ai_summary
from engines.matching.jd_parser import parse_jd
from engines.matching.matching_engine import run_match
from engines.matching.skill_extractor import SkillCatalog

logger = logging.getLogger(__name__)


class NoActiveResume(Exception):
    pass


class ProfileMissing(Exception):
    pass


class UnreviewedProfile(Exception):
    pass


class MatchNotFound(Exception):
    pass


class JobNotFound(Exception):
    pass



def preferred_job_ids_in(db, prefs, catalog) -> list[str]:
    """Shared by Job Matching, Job Discovery and the Dashboard so every page counts "jobs that fit your preferences" the same way."""
    from datetime import datetime, timezone

    from database.models.job import JobRecord
    from engines.discovery.discovery_engine import matches_query, query_from_preferences
    query, now, out = query_from_preferences(prefs), datetime.now(timezone.utc), []
    for doc in db["jobs"].find({}, {"job_description": 0}):
        try:
            rec = JobRecord.from_document(doc)
        except Exception:  # noqa: BLE001 - e.g. a job pasted on the matching page: not a discovery record, the user chose it
            out.append(doc["job_id"])
            continue
        if rec.source != "company_career_page" or matches_query(rec, query, catalog, now)[0]:
            out.append(rec.job_id)
    return out

class MatchingService:
    def __init__(
        self, jobs: JobsRepository, matches: MatchesRepository, resumes: ResumesRepository, users: UsersRepository,
        settings: Settings, cfg: ScoringConfig, prefs: Preferences, catalog: SkillCatalog, ai_client: ClaudeClient | None = None,
    ):
        self.jobs, self.matches, self.resumes, self.users = jobs, matches, resumes, users
        self.settings, self.cfg, self.prefs, self.catalog, self.ai = settings, cfg, prefs, catalog, ai_client

    # ---- profile ---------------------------------------------------------------------------------
    def profile_for_matching(self, resume_id: str | None, allow_unreviewed: bool) -> CandidateProfile:
        rid = resume_id or self.users.get_active_resume_id()
        if not rid:
            raise NoActiveResume("No active resume. Upload a resume and make it active first.")
        profile = self.resumes.get_profile(rid)
        if profile is None:
            raise ProfileMissing("No candidate profile exists for this resume.")
        if profile.review_status != "reviewed" and not allow_unreviewed:
            raise UnreviewedProfile("The candidate profile has not been reviewed. Review it in Resume Manager, "
                                    "or confirm you want to match against the unreviewed profile.")
        return profile

    def _ai(self, use_ai: bool | None) -> ClaudeClient | None:
        return None if use_ai is False else self.ai

    # ---- job + analysis (cached) ----------------------------------------------------------------------
    def _ensure_job(self, job: JobInput) -> dict:
        existing = self.jobs.find_by_hash(job_content_hash(job))
        return existing or self.jobs.create_job(job)

    def _analysis(self, job_doc: dict, use_ai: bool | None) -> tuple[JobAnalysis, bool]:
        """Return (analysis, from_cache). The cache key changes with the parser version, skill
        dictionary and AI mode, so a stale parse is never reused."""
        ai = self._ai(use_ai) if self.settings.ai_refine_jd else None     # AI_REFINE_JD=false: the rules alone classify the job's skills
        key = f"{JD_PARSER_VERSION}:{self.catalog.digest}:{ai.model if ai else 'deterministic'}"
        cached = self.jobs.get_cached_analysis(job_doc["job_id"], key)
        if cached is not None:
            return cached, True
        location = job_doc.get("location")
        if isinstance(location, list):  # jobs discovered in Phase 4 store a list of locations
            location = ", ".join(location) or None
        job = JobInput(
            job_description=job_doc.get("job_description") or job_doc.get("jd_text"), job_title=job_doc.get("job_title"),
            company_name=job_doc.get("company_name"), location=location,
            experience_requirement=job_doc.get("experience_requirement"), employment_type=job_doc.get("employment_type"),
            salary=job_doc.get("salary"), job_url=job_doc.get("job_url"),
        )
        analysis = parse_jd(job, self.catalog, ai_client=ai, bands=self.cfg.seniority.bands)
        self.jobs.save_analysis(job_doc["job_id"], analysis, key)
        return analysis, False

    def analysis_for_job(self, job_id: str, use_ai: bool | None = None) -> tuple[dict, JobAnalysis]:
        """The stored job (Phase 3 pasted or Phase 4 discovered) and its parsed analysis (cached)."""
        doc = self.jobs.get_job(job_id)
        if doc is None:
            raise JobNotFound(job_id)
        analysis, _ = self._analysis(doc, use_ai)
        return doc, analysis

    def analyze_jd(self, job: JobInput, use_ai: bool | None = None) -> tuple[str, JobAnalysis, bool]:
        doc = self._ensure_job(job)
        analysis, cached = self._analysis(doc, use_ai)
        return doc["job_id"], analysis, cached

    # ---- scoring ------------------------------------------------------------------------------------------
    def _score(self, profile: CandidateProfile, job_doc: dict, use_ai: bool | None, force: bool) -> tuple[MatchReport, bool]:
        analysis, _ = self._analysis(job_doc, use_ai)
        ai = self._ai(use_ai)                                              # still used below for the summary wording
        report = run_match(profile, analysis, job_doc["job_id"], self.cfg, self.prefs, self.catalog,
                           ai_model=ai.model if (ai and analysis.parser != "deterministic") else None)
        if not force:
            existing = self.matches.find_by_input_hash(report.input_hash)
            if existing is not None:
                return existing, False  # identical inputs + scoring version: no duplicate record
        if ai is not None:
            summary = ai_summary(report, ai)  # wording only; the numbers above are final
            if summary:
                report.ai_summary, report.ai_used, report.ai_model = summary, True, ai.model
        self.matches.save(report)
        logger.info("Match saved: id=%s score=%s eligibility=%s ai=%s", report.match_id, report.match_score,
                    report.eligibility.status, report.ai_used)
        return report, True

    def score(
        self, *, job: JobInput | None = None, job_id: str | None = None, resume_id: str | None = None,
        allow_unreviewed: bool = False, use_ai: bool | None = None, force: bool = False,
    ) -> tuple[MatchReport, bool]:
        profile = self.profile_for_matching(resume_id, allow_unreviewed)
        if job_id:
            doc = self.jobs.get_job(job_id)
            if doc is None:
                raise JobNotFound(job_id)
        elif job is not None:
            doc = self._ensure_job(job)
        else:
            raise ValueError("job or job_id is required")
        return self._score(profile, doc, use_ai, force)

    def bulk_score(
        self, jobs: list[JobInput], *, resume_id: str | None = None, allow_unreviewed: bool = False, use_ai: bool | None = None,
    ) -> list[dict]:
        profile = self.profile_for_matching(resume_id, allow_unreviewed)  # fail once, up front
        out = []
        for i, job in enumerate(jobs):
            try:
                report, created = self._score(profile, self._ensure_job(job), use_ai, force=False)
                out.append({"index": i, "job_title": job.job_title, "status": "ok", "created": created, "report": report})
            except Exception as exc:  # one bad job must not abort the batch
                logger.warning("Bulk item %d failed: %s", i, type(exc).__name__)
                out.append({"index": i, "job_title": job.job_title, "status": "error",
                            "error": "This job could not be processed.", "report": None})
        return out

    def rerun(self, match_id: str, *, allow_unreviewed: bool = False, use_ai: bool | None = None) -> MatchReport:
        """Re-score the same job against the CURRENT profile and configuration (adds a history entry)."""
        old = self.matches.get(match_id)
        if old is None:
            raise MatchNotFound(match_id)
        doc = self.jobs.get_job(old.job_id)
        if doc is None:
            raise JobNotFound(old.job_id)
        profile = self.profile_for_matching(old.resume_id, allow_unreviewed)
        report, _ = self._score(profile, doc, use_ai, force=True)
        return report

    # ---- read ------------------------------------------------------------------------------------------------
    def get(self, match_id: str) -> MatchReport:
        report = self.matches.get(match_id)
        if report is None:
            raise MatchNotFound(match_id)
        return report

    def history(self, match_id: str) -> list[MatchReport]:
        base = self.get(match_id)
        return self.matches.history(base.resume_id, base.job_id)

    def list_results(self, **filters) -> list[MatchReport]:
        return self.matches.list_matches(**filters)

    def preferred_job_ids(self) -> list[str]:
        """Jobs that fit the saved preferences (titles, locations, work mode ...). Jobs you imported yourself always count: you chose them."""
        return preferred_job_ids_in(self.jobs._jobs.database, self.prefs, self.catalog)

    def job_counts(self) -> dict:
        """The numbers behind the lists: jobs stored, how many fit the preferences, and how those score (newest match of each job)."""
        from config.automation_config import load_limits
        good = load_limits(self.settings).min_match_score
        ids = self.preferred_job_ids()
        rows = self.matches.list_matches(job_ids=ids, latest_only=True, limit=100000)
        by = {"PASS": 0, "FAIL": 0, "UNKNOWN": 0}
        for r in rows:
            by[r.eligibility.status] = by.get(r.eligibility.status, 0) + 1
        states = self.application_states(ids)
        applied = {j for j, s in states.items() if s in self.APPLIED_STATES}
        good_rows = [r for r in rows if r.eligibility.status == "PASS" and r.match_score >= good]
        return {"jobs_stored": self.jobs._jobs.count_documents({}), "fit_preferences": len(ids), "scored": len(rows), "not_scored": max(0, len(ids) - len(rows)),
                "eligible": by["PASS"], "not_eligible": by["FAIL"], "eligibility_unknown": by["UNKNOWN"], "good_score": good,
                "good_matches": len(good_rows), "applied": len(applied), "to_apply": sum(1 for r in good_rows if r.job_id not in applied),
                "eligible_not_applied": sum(1 for r in rows if r.eligibility.status == "PASS" and r.job_id not in applied)}

    APPLIED_STATES = ("SUBMITTING", "SUBMITTED", "VERIFICATION_PENDING", "VERIFIED")

    def job_ids_for(self, *, preferred_only: bool, applied: str | None) -> list[str] | None:
        """The job selection behind the results list: preferences and/or applied ("yes") / not applied yet ("no"). None = no restriction."""
        ids = self.preferred_job_ids() if preferred_only else None
        if applied not in ("yes", "no"):
            return ids
        done = set(self.jobs._jobs.database["applications"].distinct("job_id", {"state": {"$in": list(self.APPLIED_STATES)}}))
        if applied == "yes":
            return [i for i in ids if i in done] if ids is not None else list(done)
        pool = ids if ids is not None else self.jobs._jobs.distinct("job_id")
        return [i for i in pool if i not in done]

    def application_states(self, job_ids: list[str]) -> dict[str, str]:
        """job_id -> state of its newest application (read-only; for the 'applied' switch and the to-apply list)."""
        out: dict[str, str] = {}
        col = self.jobs._jobs.database["applications"]
        for a in col.find({"job_id": {"$in": list(job_ids)}}, {"_id": 0, "job_id": 1, "state": 1, "created_at": 1}).sort("created_at", 1):
            out[a["job_id"]] = a["state"]
        return out

    def job_urls(self, job_ids: list[str]) -> dict[str, str]:
        return self.jobs.urls_for(job_ids)

    def job_sources(self, job_ids: list[str]) -> dict[str, str]:
        return self.jobs.sources_for(job_ids)

    def thresholds(self) -> dict:
        c = self.cfg
        return {
            "scoring_version": c.scoring_version, "config_hash": c.config_hash, "weights": c.weights,
            "thresholds": c.thresholds.model_dump(), "recommendation_threshold": c.recommendation_threshold,
            "min_data_coverage": c.min_data_coverage, "missing_data_policy": c.missing_data_policy,
            "ai": ai_status(self.settings),
        }
