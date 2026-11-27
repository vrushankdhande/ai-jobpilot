"""Job matching API (/api/matching)."""
import logging
from datetime import datetime
from typing import Literal

import yaml
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field, ValidationError, model_validator

from ai.claude_client import ai_status, build_claude_client
from ai.schemas.job_schema import JobAnalysis, JobInput
from ai.schemas.match_schema import MatchReport
from backend.services.matching_service import (
    JobNotFound,
    MatchingService,
    MatchNotFound,
    NoActiveResume,
    ProfileMissing,
    UnreviewedProfile,
)
from config.scoring_config import load_preferences, load_scoring_config
from config.settings import Settings, get_settings
from database.connection import DatabaseManager, get_database_manager
from database.repositories.jobs_repository import JobsRepository
from database.repositories.matches_repository import MatchesRepository, min_experience_required
from database.repositories.resumes_repository import ResumesRepository
from database.repositories.users_repository import UsersRepository
from engines.matching.skill_extractor import get_catalog

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/matching", tags=["matching"])

ID = Path(pattern=r"^[0-9a-f]{32}$", description="32-character hex id")


def get_matching_service(
    manager: DatabaseManager = Depends(get_database_manager), settings: Settings = Depends(get_settings)
) -> MatchingService:
    db = manager.get_database()  # DatabaseConnectionError -> 503 via the app handler
    try:
        cfg, prefs, catalog = load_scoring_config(settings), load_preferences(settings), get_catalog(settings)
    except (ValidationError, yaml.YAMLError, OSError, KeyError) as exc:
        logger.error("Matching configuration is invalid: %s", type(exc).__name__)
        raise HTTPException(500, "Matching configuration is invalid. Check config/scoring_weights.yaml.") from None
    return MatchingService(
        JobsRepository(db), MatchesRepository(db), ResumesRepository(db), UsersRepository(db),
        settings, cfg, prefs, catalog, ai_client=build_claude_client(settings),
    )


# ---- models ------------------------------------------------------------------------------------------
class AnalyzeRequest(JobInput):
    use_ai: bool | None = Field(default=None, description="false forces the deterministic parser")


class AnalyzeResponse(BaseModel):
    job_id: str
    cached: bool
    analysis: JobAnalysis
    ai: dict


class ScoreRequest(BaseModel):
    job: JobInput | None = None
    job_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    resume_id: str | None = Field(default=None, max_length=64, description="Defaults to the active resume")
    allow_unreviewed: bool = Field(default=False, description="Explicit confirmation to match an unreviewed profile")
    use_ai: bool | None = None
    force: bool = Field(default=False, description="Create a new record even if identical inputs were already scored")

    @model_validator(mode="after")
    def _one_job(self):
        if (self.job is None) == (self.job_id is None):
            raise ValueError("provide exactly one of 'job' or 'job_id'")
        return self


class ScoreResponse(BaseModel):
    report: MatchReport
    created: bool = Field(description="false = an identical earlier result was returned")


class BulkScoreRequest(BaseModel):
    jobs: list[JobInput] = Field(min_length=1, max_length=25)
    resume_id: str | None = Field(default=None, max_length=64)
    allow_unreviewed: bool = False
    use_ai: bool | None = None


class BulkItem(BaseModel):
    index: int
    job_title: str | None = None
    status: Literal["ok", "error"]
    created: bool | None = None
    error: str | None = None
    report: MatchReport | None = None


class BulkScoreResponse(BaseModel):
    results: list[BulkItem]
    succeeded: int
    failed: int


class MatchSummary(BaseModel):
    match_id: str
    job_id: str
    resume_id: str
    profile_version: int
    job_title: str | None
    company_name: str | None
    match_score: float
    classification: str
    eligibility_status: str
    recommendation_code: str
    data_coverage: float
    scoring_version: str
    ai_used: bool
    created_at: datetime
    min_experience_required: float | None = None       # years the job asks for (None = none stated)
    role_fit: str | None = None                        # same | adjacent | different | unknown
    score_before_role_fit: float | None = None
    source: str | None = None                          # where the job came from: LinkedIn, Naukri, Google, a career page, ...
    application_state: str | None = None               # None = no application yet; SUBMITTED / VERIFIED = applied
    job_url: str | None = None                         # the posting's link (http/https only), when the job has one


def _summary(r: MatchReport) -> MatchSummary:
    return MatchSummary(
        match_id=r.match_id, job_id=r.job_id, resume_id=r.resume_id, profile_version=r.profile_version,
        job_title=r.job_title, company_name=r.company_name, match_score=r.match_score, classification=r.classification,
        eligibility_status=r.eligibility.status, recommendation_code=r.recommendation_code, data_coverage=r.data_coverage,
        scoring_version=r.scoring_version, ai_used=r.ai_used, created_at=r.created_at,
        min_experience_required=min_experience_required(r), role_fit=r.role_fit.relation if r.role_fit else None,
        score_before_role_fit=r.role_fit.score_before_role_fit if r.role_fit else None,
    )


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, (NoActiveResume, UnreviewedProfile)):
        return HTTPException(409, str(exc))
    if isinstance(exc, ProfileMissing):
        return HTTPException(404, str(exc))
    if isinstance(exc, (MatchNotFound, JobNotFound)):
        return HTTPException(404, "Match or job not found.")
    raise exc


# ---- endpoints (static paths first) --------------------------------------------------------------------
@router.get("/thresholds", summary="Configured weights, thresholds and AI status")
def thresholds(service: MatchingService = Depends(get_matching_service)):
    return service.thresholds()


@router.get("/counts", summary="Job numbers: stored, fitting your preferences, eligible, good matches")
def counts(service: MatchingService = Depends(get_matching_service)):
    return service.job_counts()


@router.get("/results/count", summary="How many saved results match the same filters as /results")
def count_results(
    resume_id: str | None = Query(None, max_length=64), min_score: float | None = Query(None, ge=0, le=100), max_score: float | None = Query(None, ge=0, le=100),
    eligibility: Literal["PASS", "FAIL", "UNKNOWN"] | None = None, classification: str | None = Query(None, max_length=40),
    recommendation_code: str | None = Query(None, max_length=40), job_title: str | None = Query(None, max_length=100), company: str | None = Query(None, max_length=100),
    date_from: datetime | None = None, date_to: datetime | None = None, latest_only: bool = False,
    max_experience_required: float | None = Query(None, ge=0, le=60), preferred_only: bool = False,
    applied: Literal["yes", "no"] | None = Query(None, description="yes = you applied; no = not applied yet"),
    service: MatchingService = Depends(get_matching_service),
):
    rows = service.list_results(
        job_ids=service.job_ids_for(preferred_only=preferred_only, applied=applied), resume_id=resume_id, min_score=min_score, max_score=max_score, eligibility=eligibility,
        classification=classification, recommendation_code=recommendation_code, job_title=job_title, company=company, date_from=date_from, date_to=date_to,
        limit=100000, skip=0, latest_only=latest_only, max_experience_required=max_experience_required)
    return {"total": len(rows)}


@router.get("/results", response_model=list[MatchSummary], summary="List saved match results")
def list_results(
    resume_id: str | None = Query(None, max_length=64),
    min_score: float | None = Query(None, ge=0, le=100),
    max_score: float | None = Query(None, ge=0, le=100),
    eligibility: Literal["PASS", "FAIL", "UNKNOWN"] | None = None,
    classification: str | None = Query(None, max_length=40),
    recommendation_code: str | None = Query(None, max_length=40),
    job_title: str | None = Query(None, max_length=100),
    company: str | None = Query(None, max_length=100),
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    latest_only: bool = Query(False, description="One row per job: its newest match"),
    max_experience_required: float | None = Query(None, ge=0, le=60, description="Jobs asking for at most this many years (none stated = included)"),
    preferred_only: bool = Query(False, description="Only jobs that fit your saved preferences (titles, locations), plus jobs you imported yourself"),
    applied: Literal["yes", "no"] | None = Query(None, description="yes = you applied; no = not applied yet"),
    sort: Literal["date", "score"] = "date",
    limit: int = Query(50, ge=1, le=200),
    skip: int = Query(0, ge=0),
    service: MatchingService = Depends(get_matching_service),
):
    rows = service.list_results(
        job_ids=service.job_ids_for(preferred_only=preferred_only, applied=applied),
        resume_id=resume_id, min_score=min_score, max_score=max_score, eligibility=eligibility,
        classification=classification, recommendation_code=recommendation_code, job_title=job_title, company=company, date_from=date_from,
        date_to=date_to, limit=limit, skip=skip, latest_only=latest_only, max_experience_required=max_experience_required, sort=sort)
    urls = service.job_urls([r.job_id for r in rows])
    srcs = service.job_sources([r.job_id for r in rows])
    states = service.application_states([r.job_id for r in rows])
    out = []
    for r in rows:
        s = _summary(r)
        url = urls.get(r.job_id)
        s.job_url = url if url and url.lower().startswith(("http://", "https://")) else None
        s.source = srcs.get(r.job_id)
        s.application_state = states.get(r.job_id)
        out.append(s)
    return out


@router.post("/analyze-jd", response_model=AnalyzeResponse, summary="Parse and analyze a job description")
def analyze_jd(body: AnalyzeRequest, service: MatchingService = Depends(get_matching_service),
               settings: Settings = Depends(get_settings)):
    if len(body.job_description) > settings.max_jd_chars:
        raise HTTPException(413, f"Job description exceeds {settings.max_jd_chars} characters.")
    job_id, analysis, cached = service.analyze_jd(JobInput.model_validate(body.model_dump(exclude={"use_ai"})), body.use_ai)
    return AnalyzeResponse(job_id=job_id, cached=cached, analysis=analysis, ai=ai_status(settings))


@router.post("/score", response_model=ScoreResponse, summary="Match the candidate against one job")
def score(body: ScoreRequest, service: MatchingService = Depends(get_matching_service),
          settings: Settings = Depends(get_settings)):
    if body.job and len(body.job.job_description) > settings.max_jd_chars:
        raise HTTPException(413, f"Job description exceeds {settings.max_jd_chars} characters.")
    try:
        report, created = service.score(job=body.job, job_id=body.job_id, resume_id=body.resume_id,
                                        allow_unreviewed=body.allow_unreviewed, use_ai=body.use_ai, force=body.force)
    except (NoActiveResume, ProfileMissing, UnreviewedProfile, JobNotFound) as exc:
        raise _http(exc) from None
    return ScoreResponse(report=report, created=created)


class ListedSkillsRequest(BaseModel):
    job_ids: list[str] = Field(max_length=200)


@router.post("/listed-skills", summary="Skills a job site lists for these jobs (alert e-mails) vs your skills - information, not a score")
def listed_skills(body: ListedSkillsRequest, service: MatchingService = Depends(get_matching_service)):
    try:
        return service.listed_skill_check([j for j in body.job_ids if isinstance(j, str) and len(j) == 32])
    except (NoActiveResume, ProfileMissing, UnreviewedProfile) as exc:
        raise _http(exc) from None


@router.post("/bulk-score", response_model=BulkScoreResponse, summary="Match the candidate against several jobs")
def bulk_score(body: BulkScoreRequest, service: MatchingService = Depends(get_matching_service)):
    try:
        items = service.bulk_score(body.jobs, resume_id=body.resume_id, allow_unreviewed=body.allow_unreviewed,
                                   use_ai=body.use_ai)
    except (NoActiveResume, ProfileMissing, UnreviewedProfile) as exc:
        raise _http(exc) from None
    ok = sum(1 for i in items if i["status"] == "ok")
    return BulkScoreResponse(results=[BulkItem(**i) for i in items], succeeded=ok, failed=len(items) - ok)


@router.get("/{match_id}", response_model=MatchReport, summary="Retrieve a match report")
def get_match(match_id: str = ID, service: MatchingService = Depends(get_matching_service)):
    try:
        return service.get(match_id)
    except MatchNotFound as exc:
        raise _http(exc) from None


@router.get("/{match_id}/history", response_model=list[MatchSummary], summary="All matches of this resume against this job")
def match_history(match_id: str = ID, service: MatchingService = Depends(get_matching_service)):
    try:
        return [_summary(r) for r in service.history(match_id)]
    except MatchNotFound as exc:
        raise _http(exc) from None


@router.post("/{match_id}/rerun", response_model=MatchReport, summary="Re-run with the current profile and configuration")
def rerun(match_id: str = ID, allow_unreviewed: bool = False, use_ai: bool | None = None,
          service: MatchingService = Depends(get_matching_service)):
    try:
        return service.rerun(match_id, allow_unreviewed=allow_unreviewed, use_ai=use_ai)
    except (MatchNotFound, JobNotFound, NoActiveResume, ProfileMissing, UnreviewedProfile) as exc:
        raise _http(exc) from None
