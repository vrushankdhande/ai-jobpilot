"""Scheduler, queue, notification and report API. Reads are open (local app); anything that changes automation needs the X-Admin-Token header."""
import logging
import secrets
import threading
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Path, Query
from pydantic import BaseModel, Field

from backend.services.matching_service import JobNotFound, NoActiveResume, ProfileMissing, UnreviewedProfile
from config.automation_config import ADJUSTABLE
from config.settings import Settings, get_settings
from database.connection import DatabaseManager, get_database_manager
from database.repositories.queue_repository import ALL_STATUSES
from scheduler.application_queue import NotQueueable
from scheduler.runtime import Runtime, build_runtime
from scheduler.scheduler import AlreadyRunning, UnknownJob, local_day

logger = logging.getLogger(__name__)
HEX32 = r"^[0-9a-f]{32}$"
TaskId = Annotated[str, Path(pattern=HEX32, description="32-character hex queue task id")]
queue_router = APIRouter(prefix="/api/queue", tags=["queue"])
scheduler_router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])
notifications_router = APIRouter(prefix="/api/notifications", tags=["notifications"])
reports_router = APIRouter(prefix="/api/reports", tags=["reports"])
_holder: dict = {"rt": None}
_lock = threading.Lock()


def get_runtime(manager: DatabaseManager = Depends(get_database_manager), settings: Settings = Depends(get_settings)) -> Runtime:
    with _lock:
        if _holder["rt"] is None:
            _holder["rt"] = build_runtime(manager.get_database(), settings)
        return _holder["rt"]


def reset_runtime() -> None:
    with _lock:
        rt, _holder["rt"] = _holder["rt"], None
    if rt is not None:
        rt.scheduler.shutdown()


def require_admin(x_admin_token: Annotated[str | None, Header()] = None, settings: Settings = Depends(get_settings)) -> None:
    """Operational endpoints are closed unless ADMIN_TOKEN is configured and supplied (constant-time comparison)."""
    if not settings.admin_token:
        raise HTTPException(403, "Operational endpoints are disabled: set ADMIN_TOKEN in .env and send it in the X-Admin-Token header.")
    if not x_admin_token or not secrets.compare_digest(x_admin_token.encode(), settings.admin_token.encode()):
        raise HTTPException(401, "Missing or invalid admin token.")


admin = Depends(require_admin)


def start_if_enabled(db, settings: Settings) -> None:
    """Called at startup: resume the scheduler if you started it before (persisted) or opted in with SCHEDULER_AUTOSTART."""
    try:
        rt = build_runtime(db, settings)
        if settings.scheduler_autostart or rt.sched_repo.get_state().get("enabled"):
            with _lock:
                _holder["rt"] = rt
            rt.scheduler.start()
            logger.info("Scheduler resumed after startup")
    except Exception as exc:  # noqa: BLE001 - the API must come up even if the scheduler cannot
        logger.error("Scheduler could not be started: %s", type(exc).__name__)


# ---------------------------------------------------------------------------------------------------- scheduler
def _status(rt: Runtime) -> dict:
    caps, lim = rt.limits_caps(), rt.limits()
    day = local_day(rt.scheduler.now_fn(), rt.scheduler.tz)
    counts = rt.sched_repo.counts(day)
    qc = rt.queue_repo.counts()
    from scheduler.daily_report import day_bounds
    submitted_today = rt.apps_repo.count_submitted_since(day_bounds(day, rt.scheduler.tz)[0])      # automated submissions that reached SUBMITTING or later (Phase 6 limit)
    return {**rt.scheduler.status(), "worker": {"processing": qc.get("processing", 0), "queued": qc.get("queued", 0), "last_run": rt.sched_repo.latest_run("application_worker")},
            "last_discovery": rt.sched_repo.latest_run("daily_discovery"), "queue_counts": qc, "automation_target": lim.daily_application_target,
            "limits": {"effective": lim.as_dict(), "caps": caps.as_dict(), "usage": {"jobs_discovered": counts.get("jobs_discovered", 0), "jobs_processed": counts.get("jobs_processed", 0), "applications_submitted": submitted_today}},
            "notifications": rt.notifier.status()}


@scheduler_router.get("/status", summary="Scheduler, worker, limits and notification status")
def scheduler_status(rt: Runtime = Depends(get_runtime)):
    return _status(rt)


@scheduler_router.post("/start", dependencies=[admin], summary="Start the scheduler (persisted: it resumes after a restart)")
def start(rt: Runtime = Depends(get_runtime)):
    rt.scheduler.start()
    return _status(rt)


@scheduler_router.post("/stop", dependencies=[admin], summary="Stop the scheduler")
def stop(rt: Runtime = Depends(get_runtime)):
    rt.scheduler.stop()
    return _status(rt)


@scheduler_router.post("/pause", dependencies=[admin], summary="Pause all automation (discovery and worker) without stopping")
def pause(rt: Runtime = Depends(get_runtime)):
    rt.scheduler.pause()
    return _status(rt)


@scheduler_router.post("/resume", dependencies=[admin], summary="Resume paused automation")
def resume(rt: Runtime = Depends(get_runtime)):
    rt.scheduler.resume()
    return _status(rt)


def _run_bg(rt: Runtime, name: str) -> None:
    try:
        rt.scheduler.run_now(name, "manual")
    except AlreadyRunning:
        logger.info("Manual %s skipped: already running", name)


@scheduler_router.post("/discover", status_code=202, dependencies=[admin], summary="Trigger discovery now (permitted sources only)")
def discover(background: BackgroundTasks, rt: Runtime = Depends(get_runtime)):
    if rt.sched_repo.lock_holder("job:daily_discovery", rt.scheduler.now_fn()):          # the scheduler's clock (= real time in production)
        raise HTTPException(409, "A discovery run is already in progress.")
    background.add_task(_run_bg, rt, "daily_discovery")
    return {"status": "started", "job": "daily_discovery"}


@scheduler_router.post("/worker/run", status_code=202, dependencies=[admin], summary="Run the application worker once now (prepares only)")
def worker_run(background: BackgroundTasks, rt: Runtime = Depends(get_runtime)):
    if rt.sched_repo.lock_holder("job:application_worker", rt.scheduler.now_fn()):
        raise HTTPException(409, "The worker is already running.")
    background.add_task(_run_bg, rt, "application_worker")
    return {"status": "started", "job": "application_worker"}


@scheduler_router.get("/runs", summary="Execution history")
def runs(job: str | None = Query(default=None, max_length=40), limit: int = Query(default=30, ge=1, le=200), rt: Runtime = Depends(get_runtime)):
    return rt.sched_repo.list_runs(job, limit)


class LimitsBody(BaseModel):
    limits: dict[str, float] = Field(max_length=10, description="Only lowers (min_match_score: raises) the caps in config/automation_limits.yaml")


@scheduler_router.get("/limits", summary="Effective limits, YAML caps and today's usage")
def limits(rt: Runtime = Depends(get_runtime)):
    return _status(rt)["limits"] | {"adjustable": sorted(ADJUSTABLE), "overrides": rt.sched_repo.get_state().get("limit_overrides", {})}


@scheduler_router.put("/limits", dependencies=[admin], summary="Adjust limits within the configured caps")
def set_limits(body: LimitsBody, rt: Runtime = Depends(get_runtime)):
    bad = [k for k in body.limits if k not in ADJUSTABLE]
    if bad:
        raise HTTPException(422, f"These limits cannot be changed here: {', '.join(bad)}")
    rt.sched_repo.set_state({"limit_overrides": {k: v for k, v in body.limits.items()}})
    return limits(rt)


# ---------------------------------------------------------------------------------------------------- queue
class EnqueueBody(BaseModel):
    job_id: str = Field(pattern=HEX32)


@queue_router.get("", summary="The application queue (highest priority first)")
def list_queue(status: str | None = Query(default=None), limit: int = Query(default=200, ge=1, le=500), skip: int = Query(default=0, ge=0), rt: Runtime = Depends(get_runtime)):
    if status and status not in ALL_STATUSES:
        raise HTTPException(422, f"status must be one of: {', '.join(ALL_STATUSES)}")
    return {"counts": rt.queue_repo.counts(), "tasks": [{k: v for k, v in t.items() if k not in ("idempotency_key", "history")} for t in rt.queue_repo.find_all(status=status, limit=limit, skip=skip)]}


@queue_router.get("/{task_id}", summary="One queue entry with its attempt history")
def get_task(task_id: TaskId, rt: Runtime = Depends(get_runtime)):
    t = rt.queue_repo.get(task_id)
    if t is None:
        raise HTTPException(404, "Queue entry not found.")
    t.pop("idempotency_key", None)
    return t


@queue_router.post("", status_code=201, dependencies=[admin], summary="Queue a job for preparation (it is scored first; nothing is submitted)")
def enqueue(body: EnqueueBody, rt: Runtime = Depends(get_runtime)):
    m = rt.discovery.matching
    try:
        profile = m.profile_for_matching(None, False)
        report, _ = m.score(job_id=body.job_id, resume_id=profile.resume_id, allow_unreviewed=False, use_ai=False)
        task, created = rt.queue.enqueue(job_id=body.job_id, resume_id=profile.resume_id, match_score=report.match_score, eligibility=report.eligibility.status,
                                         job_title=report.job_title, company_name=report.company_name)
    except (NoActiveResume, ProfileMissing, UnreviewedProfile) as exc:
        raise HTTPException(409, str(exc)) from None
    except JobNotFound:
        raise HTTPException(404, "Job not found.") from None
    except NotQueueable as exc:
        raise HTTPException(409, str(exc)) from None
    task.pop("idempotency_key", None)
    return {"created": created, "task": task}


def _control(rt: Runtime, fn, task_id: str, verb: str):
    try:
        out = fn(task_id)
    except NotQueueable as exc:
        raise HTTPException(409, str(exc)) from None
    if out is None:
        if rt.queue_repo.get(task_id) is None:
            raise HTTPException(404, "Queue entry not found.")
        raise HTTPException(409, f"This entry cannot be {verb} in its current state.")
    out.pop("idempotency_key", None)
    return out


@queue_router.post("/{task_id}/pause", dependencies=[admin], summary="Pause a queued task")
def pause_task(task_id: TaskId, rt: Runtime = Depends(get_runtime)):
    return _control(rt, rt.queue.pause, task_id, "paused")


@queue_router.post("/{task_id}/resume", dependencies=[admin], summary="Resume a paused task")
def resume_task(task_id: TaskId, rt: Runtime = Depends(get_runtime)):
    return _control(rt, rt.queue.resume, task_id, "resumed")


@queue_router.post("/{task_id}/cancel", dependencies=[admin], summary="Cancel a task (never one that may already be submitted)")
def cancel_task(task_id: TaskId, rt: Runtime = Depends(get_runtime)):
    return _control(rt, rt.queue.cancel, task_id, "cancelled")


# ---------------------------------------------------------------------------------------------------- notifications
@notifications_router.get("", summary="Notification history and delivery status")
def notification_history(status: str | None = Query(default=None, max_length=20), type: str | None = Query(default=None, max_length=40),
                         limit: int = Query(default=100, ge=1, le=500), rt: Runtime = Depends(get_runtime)):
    return {"config": rt.notifier.status(), "notifications": rt.notifier.history(status=status, type_=type, limit=limit)}


@notifications_router.post("/test", dependencies=[admin], summary="Send a test e-mail (if Gmail is configured)")
def notification_test(rt: Runtime = Depends(get_runtime)):
    n = rt.notifier.notify("test", "AI JobPilot test notification", "This is a test message from AI JobPilot. No action is needed.")
    return {"status": n["status"], "error": n.get("error"), "notification_id": n["notification_id"]}


# ---------------------------------------------------------------------------------------------------- reports
class ReportBody(BaseModel):
    date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    email: bool = False


@reports_router.get("/daily", summary="Stored daily reports (newest first)")
def daily_reports(date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"), limit: int = Query(default=14, ge=1, le=100), rt: Runtime = Depends(get_runtime)):
    return rt.sched_repo.list_reports(date, limit)


@reports_router.post("/daily/generate", status_code=201, dependencies=[admin], summary="Generate (and optionally e-mail) a daily report now")
def generate_report(body: ReportBody, rt: Runtime = Depends(get_runtime)):
    return rt.report.generate(body.date, email=body.email, trigger="manual")
