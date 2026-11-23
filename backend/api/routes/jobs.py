"""Job discovery API (/api/jobs)."""
import logging
from datetime import datetime
from functools import lru_cache
from typing import Literal

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Path, Query, Response
from pydantic import BaseModel, Field, field_validator

from ai.claude_client import build_claude_client
from backend.services.matching_service import MatchingService
from database.repositories.matches_repository import MatchesRepository
from backend.services.discovery_service import DiscoveryBusy, DiscoveryService, JobNotFound, NotRefreshable
from config.scoring_config import load_preferences, load_scoring_config
from config.settings import Settings, get_settings
from database.connection import DatabaseManager, get_database_manager
from database.models.job import JobRecord
from database.repositories.discovery_runs_repository import DiscoveryRunsRepository
from database.repositories.jobs_repository import JobsRepository
from database.repositories.resumes_repository import ResumesRepository
from database.repositories.users_repository import UsersRepository
from engines.discovery.discovery_engine import DiscoveryEngine
from engines.matching.skill_extractor import get_catalog
from integrations.base_adapter import AdapterError, SourceBlocked
from integrations.policy import PolicyRegistry, PolicyViolation
from integrations.registry import build_adapters

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/jobs", tags=["jobs"])
ID = Path(pattern=r"^[0-9a-f]{32}$", description="32-character hex id")


@lru_cache
def _http_client() -> httpx.Client:
    return httpx.Client()


def get_discovery_service(manager: DatabaseManager = Depends(get_database_manager),
                          settings: Settings = Depends(get_settings)) -> DiscoveryService:
    db = manager.get_database()
    try:
        registry = PolicyRegistry.load(settings)
        cfg, prefs, catalog = load_scoring_config(settings), load_preferences(settings), get_catalog(settings)
        adapters = build_adapters(settings, registry, client=_http_client())
    except Exception as exc:
        logger.error("Discovery configuration is invalid: %s", type(exc).__name__)
        raise HTTPException(500, "Discovery configuration is invalid. Check config/*.yaml.") from None
    jobs, runs = JobsRepository(db), DiscoveryRunsRepository(db)
    engine = DiscoveryEngine(adapters, jobs, runs, registry, catalog, cfg.seniority.bands)
    return DiscoveryService(jobs, runs, registry, adapters, engine, prefs)


# ---- models ---------------------------------------------------------------------------------------------
class DiscoverRequest(BaseModel):
    sources: list[str] | None = Field(default=None, max_length=10)
    use_preferences: bool = True
    titles: list[str] = Field(default_factory=list, max_length=20)
    skills: list[str] = Field(default_factory=list, max_length=30)
    locations: list[str] = Field(default_factory=list, max_length=20)
    companies: list[str] = Field(default_factory=list, max_length=50)
    work_modes: list[Literal["remote", "hybrid", "onsite"]] = Field(default_factory=list)
    employment_types: list[str] = Field(default_factory=list, max_length=8)
    min_salary: float | None = Field(default=None, ge=0)
    salary_currency: str | None = Field(default=None, max_length=3)
    experience_min: float | None = Field(default=None, ge=0, le=60)
    experience_max: float | None = Field(default=None, ge=0, le=60)
    posted_within_days: int | None = Field(default=None, ge=1, le=3650)
    max_jobs: int | None = Field(default=None, ge=1, le=1000)

    @field_validator("titles", "skills", "locations", "companies", "employment_types")
    @classmethod
    def _short(cls, v):
        return [s.strip()[:100] for s in v if s and s.strip()]


class ManualJobFields(BaseModel):
    job_title: str | None = Field(default=None, max_length=200)
    company_name: str | None = Field(default=None, max_length=200)
    job_description: str | None = Field(default=None, max_length=60000)
    location: str | None = Field(default=None, max_length=300)
    employment_type: str | None = Field(default=None, max_length=50)
    work_mode: Literal["remote", "hybrid", "onsite"] | None = None
    salary: str | None = Field(default=None, max_length=200)
    posted_date: str | None = Field(default=None, max_length=50)
    experience_requirement: str | None = Field(default=None, max_length=100)


class ImportRequest(BaseModel):
    url: str | None = Field(default=None, max_length=2000)
    job: ManualJobFields | None = None
    source: Literal["linkedin", "naukri", "indeed", "instahyre"] | None = Field(default=None, description="Where you copied the job from, when there is no job link")

    @field_validator("url")
    @classmethod
    def _url(cls, v):
        return (v or "").strip() or None


class SourceLink(BaseModel):
    source: str
    provider: str | None = None
    url: str | None = None
    last_seen_at: datetime | None = None


class JobOut(BaseModel):
    job_id: str
    source: str
    source_job_id: str | None = None
    job_title: str | None = None
    company_name: str | None = None
    description_preview: str | None = None
    mandatory_skills: list[str] = []
    preferred_skills: list[str] = []
    experience_min: float | None = None
    experience_max: float | None = None
    location: list[str] = []
    work_mode: str | None = None
    employment_type: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    currency: str | None = None
    job_url: str | None = None
    posted_date: datetime | None = None
    discovered_at: datetime
    last_updated_at: datetime
    status: str
    sources: list[SourceLink] = []
    dedup_status: str = "unique"
    dedup_confidence: float | None = None
    needs_review: bool = False
    warnings: list[str] = []


class JobDetail(JobOut):
    job_description: str | None = None
    dedup_reasons: list[str] = []
    source_originals: list[dict] = []


def _out(doc: dict, detail: bool = False) -> JobOut | JobDetail:
    r = JobRecord.from_document(doc)
    base = dict(
        job_id=r.job_id, source=r.source, source_job_id=r.source_job_id, job_title=r.job_title, company_name=r.company_name,
        description_preview=(r.job_description or "")[:400] or None, mandatory_skills=r.mandatory_skills,
        preferred_skills=r.preferred_skills, experience_min=r.experience_min, experience_max=r.experience_max,
        location=r.location, work_mode=r.work_mode, employment_type=r.employment_type, salary_min=r.salary_min,
        salary_max=r.salary_max, currency=r.currency, job_url=r.job_url, posted_date=r.posted_date,
        discovered_at=r.discovered_at, last_updated_at=r.last_updated_at, status=r.status,
        sources=[SourceLink(source=s.source, provider=s.provider, url=s.url, last_seen_at=s.last_seen_at) for s in r.source_refs]
        or [SourceLink(source=r.source, url=r.job_url)],
        dedup_status=r.dedup.status, dedup_confidence=r.dedup.confidence, needs_review=r.dedup.needs_review, warnings=r.warnings,
    )
    if not detail:
        return JobOut(**base)
    return JobDetail(**base, job_description=r.job_description, dedup_reasons=r.dedup.reasons,
                     source_originals=[{"source": s.source, "provider": s.provider, "original": s.original} for s in r.source_refs])


# ---- endpoints (static paths before /{job_id}) -------------------------------------------------------------
@router.get("/sources", summary="Source availability and policy status")
def list_sources(service: DiscoveryService = Depends(get_discovery_service)):
    return service.sources()


@router.get("/discovery/status", summary="Status of the latest (or a given) discovery run")
def discovery_status(run_id: str | None = Query(None, pattern=r"^[0-9a-f]{32}$"),
                     service: DiscoveryService = Depends(get_discovery_service)):
    return service.status(run_id)


@router.post("/discover", status_code=202, summary="Start discovery on the permitted sources")
def discover(body: DiscoverRequest, background: BackgroundTasks, response: Response,
             service: DiscoveryService = Depends(get_discovery_service)):
    overrides = body.model_dump(exclude={"sources", "use_preferences"})
    try:
        run, skipped, query, runnable = service.start(overrides, body.sources, body.use_preferences)
    except DiscoveryBusy as exc:
        raise HTTPException(409, str(exc)) from None
    if run is None:
        response.status_code = 200
        return {"run_id": None, "status": "nothing_to_run", "runnable_sources": [], "skipped_sources": skipped,
                "message": "None of the requested sources permits automated discovery."}
    background.add_task(service.execute, run["run_id"], query, runnable)
    return {"run_id": run["run_id"], "status": "running", "runnable_sources": runnable, "skipped_sources": skipped}


@router.post("/import", status_code=201, summary="Import a job by URL or from details you provide")
def import_job(body: ImportRequest, response: Response, service: DiscoveryService = Depends(get_discovery_service),
               settings: Settings = Depends(get_settings)):
    if not body.url and not body.job:
        raise HTTPException(422, "Provide a job URL, job details, or both.")
    try:
        out = service.import_job(body.url, body.job.model_dump(exclude_none=True) if body.job else None, body.source)
    except PolicyViolation as exc:
        raise HTTPException(403, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except SourceBlocked:
        raise HTTPException(502, "The source refused the request; nothing was imported.") from None
    except AdapterError as exc:
        raise HTTPException(502, str(exc)) from None
    if out["action"] in ("unchanged", "update", "merge"):
        response.status_code = 200
    match = None                                   # score it right away (rules only) so the job shows a score and eligibility without another step
    job = out["job"]
    if job and (job.get("job_description") or "").strip():
        try:
            db = service.jobs._jobs.database
            matching = MatchingService(service.jobs, MatchesRepository(db), ResumesRepository(db), UsersRepository(db), settings, load_scoring_config(settings),
                                       load_preferences(settings), get_catalog(settings), ai_client=build_claude_client(settings))
            report, _ = matching.score(job_id=job["job_id"], allow_unreviewed=False, use_ai=False)
            match = {"match_id": report.match_id, "match_score": report.match_score, "eligibility": report.eligibility.status, "classification": report.classification}
        except Exception as exc:  # noqa: BLE001 - no reviewed profile, or a job too thin to score: the import itself still succeeded
            logger.info("Imported job not scored: %s", type(exc).__name__)
    return {"action": out["action"], "duplicate_confidence": out["confidence"], "reasons": out["reasons"],
            "source": out["source"], "job": _out(job, detail=True) if job else None, "match": match}


def _job_filters(source, title, company, location, work_mode, employment_type, status, skill, posted_after, posted_before, discovered_after, discovered_before,
                 needs_review, min_experience, max_experience, min_match_score, service) -> dict:
    f = dict(source=source, title=title, company=company, location=location, work_mode=work_mode, employment_type=employment_type, status=status, skill=skill,
             posted_after=posted_after, posted_before=posted_before, discovered_after=discovered_after, discovered_before=discovered_before, needs_review=needs_review,
             min_experience=min_experience, max_experience=max_experience)
    if min_match_score is not None:   # jobs whose LATEST saved match score reaches the threshold
        col = service.jobs._jobs.database["job_matches"]
        f["job_ids"] = [d["_id"] for d in col.aggregate([{"$sort": {"created_at": -1}}, {"$group": {"_id": "$job_id", "s": {"$first": "$match_score"}}},
                                                          {"$match": {"s": {"$gte": min_match_score}}}])]
    return f


@router.get("/linkedin/suggestions", summary="Search keywords and location taken from your reviewed profile (nothing is sent to LinkedIn)")
def linkedin_suggestions(service: DiscoveryService = Depends(get_discovery_service)):
    db = service.jobs._jobs.database
    out = {"profile_available": False, "current_title": None, "titles": [], "skills": [], "location": None, "experience_years": None}
    rid = UsersRepository(db).get_active_resume_id()
    if not rid:
        return out
    prof = ResumesRepository(db).get_profile(rid)
    if prof is None:
        return out
    titles: list[str] = []
    cur = prof.summary.current_job_title
    for t in [cur] + [e.job_title for e in sorted(prof.work_experience, key=lambda e: (e.is_current, e.start_date or ""), reverse=True)]:
        t = (t or "").strip()
        t = t.title().replace("Ai", "AI").replace("Ml", "ML") if t.isupper() else t      # an ALL-CAPS resume headline reads badly as a search keyword
        if t and "intern" not in t.lower() and t.lower() not in [x.lower() for x in titles]:
            titles.append(t)
    skills = [s for _, s in prof.technical_skills.all_skills()][:8]
    out.update(profile_available=True, current_title=titles[0] if titles else None, titles=titles[:5], skills=skills,
               location=prof.contact.location or next((e.location for e in prof.work_experience if e.location), None),
               experience_years=prof.summary.total_experience_years)
    return out


@router.get("/linkedin/table", summary="Jobs you imported from LinkedIn with their latest match and application status")
def linkedin_table(limit: int = Query(100, ge=1, le=200), service: DiscoveryService = Depends(get_discovery_service)):
    db = service.jobs._jobs.database
    docs = service.list_jobs(source="linkedin", sort_by="discovered_at", descending=True, limit=limit, skip=0)
    ids = [d["job_id"] for d in docs]
    match = {d["_id"]: d for d in db["job_matches"].aggregate([
        {"$match": {"job_id": {"$in": ids}}}, {"$sort": {"created_at": -1}},
        {"$group": {"_id": "$job_id", "score": {"$first": "$match_score"}, "elig": {"$first": "$eligibility.status"}, "match_id": {"$first": "$match_id"}}}])}
    app = {}
    for a in db["applications"].find({"job_id": {"$in": ids}}, {"job_id": 1, "state": 1, "created_at": 1}).sort("created_at", 1):
        app[a["job_id"]] = a.get("state")
    rows = []
    for d in docs:
        o = _out(d)
        m = match.get(o.job_id, {})
        url = o.job_url if (o.job_url or "").lower().startswith(("http://", "https://")) else None
        rows.append({"job_id": o.job_id, "job_title": o.job_title, "company_name": o.company_name, "location": ", ".join(o.location) or None,
                     "experience_min": o.experience_min, "experience_max": o.experience_max, "posted_date": o.posted_date, "discovered_at": o.discovered_at,
                     "match_score": m.get("score"), "eligibility": m.get("elig"), "match_id": m.get("match_id"), "job_url": url,
                     "application_status": app.get(o.job_id) or "NOT_STARTED"})
    return {"total": len(rows), "items": rows}


@router.get("/discovery/runs", summary="Discovery run history (newest first)")
def discovery_runs(limit: int = Query(10, ge=1, le=100), service: DiscoveryService = Depends(get_discovery_service)):
    return service.runs.recent(limit)


@router.get("/page", summary="Filtered jobs with the total count: {total, items}")
def page_jobs(source: str | None = Query(None, max_length=40), title: str | None = Query(None, max_length=100), company: str | None = Query(None, max_length=100),
              location: str | None = Query(None, max_length=100), work_mode: Literal["remote", "hybrid", "onsite"] | None = None, employment_type: str | None = Query(None, max_length=30),
              status: Literal["active", "closed", "expired"] | None = None, skill: str | None = Query(None, max_length=60), posted_after: datetime | None = None,
              posted_before: datetime | None = None, discovered_after: datetime | None = None, discovered_before: datetime | None = None, needs_review: bool | None = None,
              min_experience: float | None = Query(None, ge=0, le=60), max_experience: float | None = Query(None, ge=0, le=60), min_match_score: float | None = Query(None, ge=0, le=100),
              sort_by: Literal["posted_date", "discovered_at", "last_updated_at", "job_title", "company_name", "experience_min", "salary_max"] = "discovered_at",
              descending: bool = True, limit: int = Query(25, ge=1, le=200), skip: int = Query(0, ge=0),
              preferred_only: bool = Query(False, description="Only jobs that fit your saved preferences (titles, locations, age), plus jobs you imported yourself"),
              service: DiscoveryService = Depends(get_discovery_service)):
    f = _job_filters(source, title, company, location, work_mode, employment_type, status, skill, posted_after, posted_before, discovered_after, discovered_before,
                     needs_review, min_experience, max_experience, min_match_score, service)
    if preferred_only:
        from backend.services.matching_service import preferred_job_ids_in
        fits = preferred_job_ids_in(service.jobs._jobs.database, service.prefs, service.engine.catalog)
        f["job_ids"] = [i for i in f["job_ids"] if i in set(fits)] if "job_ids" in f else fits
    return {"total": service.jobs.count_filtered(**f), "items": [_out(d) for d in service.list_jobs(**f, sort_by=sort_by, descending=descending, limit=limit, skip=skip)]}

@router.get("", response_model=list[JobOut], summary="List discovered jobs")
def list_jobs(
    source: str | None = Query(None, max_length=40), title: str | None = Query(None, max_length=100),
    company: str | None = Query(None, max_length=100), location: str | None = Query(None, max_length=100),
    work_mode: Literal["remote", "hybrid", "onsite"] | None = None, employment_type: str | None = Query(None, max_length=30),
    status: Literal["active", "closed", "expired"] | None = None, skill: str | None = Query(None, max_length=60),
    posted_after: datetime | None = None, posted_before: datetime | None = None, discovered_after: datetime | None = None,
    needs_review: bool | None = None, min_experience: float | None = Query(None, ge=0, le=60),
    max_experience: float | None = Query(None, ge=0, le=60),
    sort_by: Literal["posted_date", "discovered_at", "last_updated_at", "job_title", "company_name", "experience_min", "salary_max"] = "discovered_at",
    descending: bool = True, limit: int = Query(50, ge=1, le=200), skip: int = Query(0, ge=0),
    service: DiscoveryService = Depends(get_discovery_service),
):
    docs = service.list_jobs(source=source, title=title, company=company, location=location, work_mode=work_mode,
                             employment_type=employment_type, status=status, skill=skill, posted_after=posted_after,
                             posted_before=posted_before, discovered_after=discovered_after, needs_review=needs_review,
                             min_experience=min_experience, max_experience=max_experience, sort_by=sort_by,
                             descending=descending, limit=limit, skip=skip)
    return [_out(d) for d in docs]


@router.get("/{job_id}", response_model=JobDetail, summary="Job details")
def get_job(job_id: str = ID, service: DiscoveryService = Depends(get_discovery_service)):
    try:
        return _out(service.get_job(job_id), detail=True)
    except JobNotFound:
        raise HTTPException(404, "Job not found.") from None


@router.post("/{job_id}/refresh", summary="Refresh a job's details from its (permitted) source")
def refresh_job(job_id: str = ID, service: DiscoveryService = Depends(get_discovery_service)):
    try:
        out = service.refresh(job_id)
    except JobNotFound:
        raise HTTPException(404, "Job not found.") from None
    except NotRefreshable as exc:
        raise HTTPException(409, str(exc)) from None
    except PolicyViolation as exc:
        raise HTTPException(403, str(exc)) from None
    except SourceBlocked:
        raise HTTPException(502, "The source refused the request; nothing was changed.") from None
    except AdapterError as exc:
        raise HTTPException(502, str(exc)) from None
    return {"action": out["action"], "job": _out(out["job"], detail=True)}
