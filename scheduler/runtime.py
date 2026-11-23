"""Wires the scheduler, worker, discovery, report and notifier together from a database + settings (no FastAPI needed).

The default queue processor calls Phase 6 ``ApplicationService.prepare`` - it can prepare, never approve or submit.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from pymongo.database import Database

from backend.services.application_service import ApplicationService, DuplicateApplication
from backend.services.discovery_service import DiscoveryService
from backend.services.document_service import DocumentService
from backend.services.matching_service import MatchingService
from browser.screenshot_manager import ScreenshotManager
from browser.session_manager import SessionManager
from config.automation_config import AutomationLimits, effective, in_window, load_limits
from config.scoring_config import load_preferences, load_scoring_config
from config.settings import Settings
from database.repositories.applications_repository import ApplicationsRepository
from database.repositories.discovery_runs_repository import DiscoveryRunsRepository
from database.repositories.documents_repository import DocumentsRepository
from database.repositories.jobs_repository import JobsRepository
from database.repositories.matches_repository import MatchesRepository
from database.repositories.notifications_repository import NotificationsRepository
from database.repositories.queue_repository import QueueRepository
from database.repositories.resumes_repository import ResumesRepository
from database.repositories.scheduler_repository import SchedulerRepository
from database.repositories.users_repository import UsersRepository
from engines.application.application_engine import ApplicationEngine
from engines.application.retry_manager import RetryManager
from engines.application.state_machine import AppState
from engines.discovery.discovery_engine import DiscoveryEngine
from engines.matching.skill_extractor import get_catalog
from integrations.application_policy import ApplicationPolicy
from integrations.policy import PolicyRegistry
from integrations.registry import build_adapters
from notifications.email_notifications import EmailNotifier
from scheduler.application_queue import APP_TO_QUEUE, ApplicationQueue
from scheduler.application_worker import ApplicationWorker, Outcome
from scheduler.daily_discovery import DailyDiscovery
from scheduler.daily_report import DailyReport
from scheduler.scheduler import JobSpec, Scheduler, get_zone
from ai.claude_client import build_claude_client

logger = logging.getLogger(__name__)


@dataclass
class Runtime:
    settings: Settings
    scheduler: Scheduler
    worker: ApplicationWorker
    discovery: DailyDiscovery
    report: DailyReport
    notifier: EmailNotifier
    queue_repo: QueueRepository
    apps_repo: ApplicationsRepository
    queue: ApplicationQueue
    sched_repo: SchedulerRepository
    limits_caps: callable
    limits: callable


def build_matching(db: Database, settings: Settings) -> MatchingService:
    cfg, prefs, catalog = load_scoring_config(settings), load_preferences(settings), get_catalog(settings)
    return MatchingService(JobsRepository(db), MatchesRepository(db), ResumesRepository(db), UsersRepository(db), settings, cfg, prefs, catalog, ai_client=build_claude_client(settings))


def build_application_service(db: Database, settings: Settings, matching: MatchingService | None = None) -> ApplicationService:
    matching = matching or build_matching(db, settings)
    docs = DocumentService(DocumentsRepository(db), matching, settings, matching.catalog, matching.prefs, matching.cfg)
    apps, limits = ApplicationsRepository(db), load_limits(settings)
    shots = ScreenshotManager(settings.screenshots_path)
    policy = ApplicationPolicy.load(settings, PolicyRegistry.load(settings))
    engine = ApplicationEngine(settings, SessionManager(settings), shots, limits)
    return ApplicationService(apps, matching, docs, settings, policy, engine, RetryManager(apps, settings.max_submit_retries), limits, shots)


def build_discovery(db: Database, settings: Settings, client: httpx.Client | None = None) -> DiscoveryService:
    registry = PolicyRegistry.load(settings)
    cfg, prefs, catalog = load_scoring_config(settings), load_preferences(settings), get_catalog(settings)
    adapters = build_adapters(settings, registry, client=client or httpx.Client())
    jobs, runs = JobsRepository(db), DiscoveryRunsRepository(db)
    return DiscoveryService(jobs, runs, registry, adapters, DiscoveryEngine(adapters, jobs, runs, registry, catalog, cfg.seniority.bands), prefs)


def prepare_processor(db: Database, settings: Settings, apps: ApplicationsRepository, matching: MatchingService):
    """task -> Outcome. Prepares (idempotently) and reports where the application stands. NEVER approves or submits."""
    def process(task: dict) -> Outcome:
        svc = build_application_service(db, settings, matching)
        try:
            app = svc.prepare(task["job_id"], resume_id=task["resume_id"])
        except DuplicateApplication as exc:
            app = apps.get(exc.existing_id) if exc.existing_id else None
            if app is None:
                raise
        status = APP_TO_QUEUE[AppState(app["state"])]
        if status == "processing":
            raise RuntimeError("application is still being processed")          # recoverable: retried later
        return Outcome(status, application_id=app["application_id"], detail=app["state"])
    return process


def build_runtime(db: Database, settings: Settings, *, now_fn=lambda: datetime.now(timezone.utc), processor=None, transport=None, discovery_service=None,
                  http_client: httpx.Client | None = None) -> Runtime:
    sched_repo, queue_repo, apps = SchedulerRepository(db), QueueRepository(db), ApplicationsRepository(db)
    caps = load_limits(settings)
    limits = lambda: effective(caps, sched_repo.get_state().get("limit_overrides"))  # noqa: E731 - dashboard overrides can only lower the YAML caps
    matching = build_matching(db, settings)
    notifier = EmailNotifier(settings, NotificationsRepository(db), transport, now_fn=now_fn)
    queue = ApplicationQueue(queue_repo, apps, limits, now_fn=now_fn)
    worker = ApplicationWorker(queue_repo, apps, sched_repo, processor or prepare_processor(db, settings, apps, matching), limits, caps.timezone, notifier=notifier, now_fn=now_fn)
    disc = DailyDiscovery(discovery_service or build_discovery(db, settings, http_client), matching, queue, sched_repo, limits, caps.timezone, notifier=notifier, now_fn=now_fn)
    report = DailyReport(db, sched_repo, queue_repo, limits, caps.timezone, notifier=notifier, now_fn=now_fn)
    tz = get_zone(caps.timezone)
    jobs = [
        JobSpec("daily_discovery", disc.run, at=caps.discovery_time, lock_ttl=3600, description="Discover jobs on permitted sources, match them and queue eligible ones",
                gate=lambda now: None if in_window(limits().discovery_window, now.astimezone(tz).time()) else "outside the discovery window"),
        JobSpec("application_worker", lambda trigger: worker.run_once(), every_seconds=60, lock_ttl=1800, description="Prepare queued applications (never submits)"),
        JobSpec("daily_report", lambda trigger: {"report_id": report.generate(email=True, trigger=trigger)["report_id"]}, at=caps.report_time, lock_ttl=600,
                description="Build and e-mail the daily report"),
        JobSpec("notification_retry", lambda trigger: notifier.retry_failed(), every_seconds=900, lock_ttl=300, description="Re-send temporarily failed e-mails"),
    ]
    sched = Scheduler(sched_repo, tz, jobs, now_fn=now_fn, catchup_grace_minutes=caps.catchup_grace_minutes, tick_seconds=settings.scheduler_tick_seconds)
    return Runtime(settings, sched, worker, disc, report, notifier, queue_repo, apps, queue, sched_repo, lambda: caps, limits)
