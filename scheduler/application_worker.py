"""Background worker: PREPARES queued applications (Phase 6 ``prepare``). It never approves and never submits - it has no code path that can.

Failure handling:
* permanent problems (not eligible, policy denied, profile not reviewed ...) -> ``failed`` immediately, with the reason;
* temporary/recoverable problems (network, browser, unexpected) -> retried with exponential back-off up to ``max_retries_per_application``;
* before ANY retry the application is looked at: if it may already have been submitted (or its outcome is uncertain) the task becomes
  ``needs_verification`` and is never retried - a retry can therefore never create a second submission.
Every attempt is appended to the task history; a task stuck in ``processing`` after a crash is recovered when its lease expires.
"""
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

import httpx
from pydantic import ValidationError
from pymongo.errors import AutoReconnect, NetworkTimeout

from browser.browser_manager import BrowserError
from config.automation_config import AutomationLimits, in_window
from database.repositories.applications_repository import ApplicationsRepository
from database.repositories.queue_repository import (
    AWAITING_APPROVAL, COMPLETED, FAILED, MANUAL_HANDOFF, NEEDS_VERIFICATION, PROCESSING, QUEUED, QueueRepository,
)
from database.repositories.scheduler_repository import SchedulerRepository
from engines.application.state_machine import SUBMISSION_MAY_EXIST, AppState
from integrations.policy import PolicyViolation
from scheduler.application_queue import APP_TO_QUEUE
from scheduler.scheduler import get_zone, local_day

logger = logging.getLogger(__name__)
S = AppState


@dataclass
class Outcome:
    status: str                       # a queue status
    application_id: str | None = None
    error: str | None = None
    retry: bool = False               # recoverable: try again later
    detail: str = ""


def classify(exc: Exception) -> str:
    """'permanent' | 'transient' | 'recoverable'. Domain refusals are permanent; network/browser hiccups transient; anything else recoverable."""
    from backend.services.application_service import ActionNotAllowed, DuplicateApplication, LimitReached, NotEligible
    from backend.services.matching_service import JobNotFound, NoActiveResume, ProfileMissing, UnreviewedProfile
    if isinstance(exc, (NotEligible, ActionNotAllowed, DuplicateApplication, PolicyViolation, JobNotFound, NoActiveResume, ProfileMissing, UnreviewedProfile,
                        ValidationError, ValueError, LimitReached)):
        return "permanent"
    if isinstance(exc, BrowserError):
        return "transient" if exc.transient else "permanent"
    if isinstance(exc, (httpx.HTTPError, TimeoutError, ConnectionError, AutoReconnect, NetworkTimeout, OSError)):
        return "transient"
    return "recoverable"


def backoff_seconds(base: float, attempt: int, cap: float = 3600.0) -> float:
    return min(cap, base * (2 ** max(0, attempt - 1)))


class ApplicationWorker:
    def __init__(self, queue: QueueRepository, apps: ApplicationsRepository, sched: SchedulerRepository, processor: Callable[[dict], Outcome],
                 limits_fn: Callable[[], AutomationLimits], tz_name: str, *, notifier=None, now_fn=lambda: datetime.now(timezone.utc)):
        self.queue, self.apps, self.sched, self.processor, self.limits_fn, self.notifier, self.now = queue, apps, sched, processor, limits_fn, notifier, now_fn
        self.tz = get_zone(tz_name)

    # ------------------------------------------------------------------------------------------------- gates
    def gate(self, now: datetime) -> str | None:
        """Why the worker must not process right now (None = it may)."""
        st = self.sched.get_state()
        if st.get("paused"):
            return "automation is paused"
        if not in_window(self.limits_fn().application_window, now.astimezone(self.tz).time()):
            return "outside the allowed application time window"
        return None

    # ------------------------------------------------------------------------------------------------- one pass
    def run_once(self, worker_id: str | None = None, max_tasks: int | None = None) -> dict:
        """Process due tasks (up to the daily/concurrency limits). Safe to call repeatedly and from several workers."""
        now = self.now()
        wid = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        summary = {"worker": wid, "processed": [], "skipped_reason": None, "recovered": 0, "synced": 0}
        summary["recovered"] = len(self.queue.recover_stale(now))
        summary["synced"] = self.sync_all(now)
        reason = self.gate(now)
        if reason:
            summary["skipped_reason"] = reason
            return summary
        lim = self.limits_fn()
        day = local_day(now, self.tz)
        for _ in range(max_tasks or lim.max_concurrent_workers):
            task = self.queue.claim_next(wid, lim.queue_lease_seconds, now, lim.max_concurrent_workers)
            if task is None:
                break
            if not self.sched.consume(day, "jobs_processed", 1, lim.max_jobs_processed_per_day):     # atomic: the daily limit can never be overshot
                self.queue.transition(task["task_id"], [PROCESSING], QUEUED, {"attempts": task["attempts"] - 1, "worker_id": None, "lease_expires_at": None},
                                      event="deferred", detail="daily processing limit reached", now=now)
                summary["skipped_reason"] = "daily processing limit reached"
                break
            summary["processed"].append(self._process(task, lim, now))
        return summary

    def _process(self, task: dict, lim: AutomationLimits, now: datetime) -> dict:
        try:
            out = self.processor(task)
        except Exception as exc:  # noqa: BLE001
            out = self._failure(task, exc, lim)
        out = self._guard_uncertain(task, out)
        fields = {"worker_id": None, "lease_expires_at": None, "last_error": out.error, "application_id": out.application_id or task.get("application_id")}
        if out.status == QUEUED:                                                       # scheduled retry
            fields["next_attempt_at"] = now + timedelta(seconds=backoff_seconds(lim.retry_backoff_seconds, task["attempts"]))
        new = self.queue.transition(task["task_id"], [PROCESSING], out.status, fields, event="attempt", detail=(out.detail or out.error or out.status), now=now)
        if new is None:                                                                # cancelled/paused while we worked: that decision wins
            return {"task_id": task["task_id"], "status": "superseded"}
        self._notify(new, out)
        return {"task_id": task["task_id"], "status": out.status, "application_id": out.application_id, "error": out.error}

    def _failure(self, task: dict, exc: Exception, lim: AutomationLimits) -> Outcome:
        kind = classify(exc)
        name = type(exc).__name__
        retries_left = task["attempts"] <= min(lim.max_retries_per_application, task["max_attempts"])
        if kind != "permanent" and retries_left:
            return Outcome(QUEUED, error=name, retry=True, detail=f"{kind} failure, will retry")
        return Outcome(FAILED, error=name, detail=f"{kind} failure" + ("" if kind == "permanent" else ", retry limit reached") + f": {str(exc)[:120] if kind == 'permanent' else name}")

    def _guard_uncertain(self, task: dict, out: Outcome) -> Outcome:
        """Never leave a task retryable if its application may already have been sent / has an uncertain outcome."""
        if out.status != QUEUED:
            return out
        app = self.apps.find_by_key(task["idempotency_key"])
        if app and S(app["state"]) in SUBMISSION_MAY_EXIST | {S.VERIFICATION_PENDING}:
            return Outcome(NEEDS_VERIFICATION if S(app["state"]) != S.SUBMITTED and S(app["state"]) != S.VERIFIED else COMPLETED, application_id=app["application_id"],
                           error=out.error, detail="the application may already have been submitted - not retried; verify manually")
        return out

    # ------------------------------------------------------------------------------------------------- follow applications
    def sync_all(self, now: datetime | None = None) -> int:
        """Align queue entries with their application's real state (e.g. the candidate approved/submitted, or the outcome was uncertain)."""
        now, n = now or self.now(), 0
        for t in self.queue.find_all(statuses=[AWAITING_APPROVAL, MANUAL_HANDOFF, NEEDS_VERIFICATION], limit=500):
            if not t.get("application_id"):
                continue
            app = self.apps.get(t["application_id"])
            if app is None:
                continue
            target = APP_TO_QUEUE[S(app["state"])]
            if target in (PROCESSING, t["status"]):
                continue
            new = self.queue.transition(t["task_id"], [t["status"]], target, {"last_error": (app.get("submission") or {}).get("detail") if target == FAILED else None},
                                        event="synced", detail=f"application {app['state']}", now=now)
            if new:
                n += 1
                self._notify(new, Outcome(target, application_id=t["application_id"]), app)
        return n

    def _notify(self, task: dict, out: Outcome, app: dict | None = None) -> None:
        if self.notifier is None:
            return
        name = f"{task.get('job_title') or 'a job'} at {task.get('company_name') or 'a company'}"
        try:
            if out.status == AWAITING_APPROVAL:
                self.notifier.notify("approval_required", f"Approval needed: {name}", f"An application for {name} is prepared and waiting for your review. Nothing is sent until you approve it.",
                                     dedupe_key=f"approval:{task['task_id']}")
            elif out.status == FAILED:
                self.notifier.notify("application_failed", f"Application not completed: {name}", f"The application for {name} could not be completed ({task.get('last_error') or 'see the dashboard'}). Nothing was submitted.",
                                     dedupe_key=f"failed:{task['task_id']}:{task['attempts']}")
            elif out.status == NEEDS_VERIFICATION:
                self.notifier.notify("verification_needed", f"Please verify: {name}", f"We cannot tell whether the application for {name} went through. Please check, then record the result. It will not be retried automatically.",
                                     dedupe_key=f"verify:{task['task_id']}")
            elif out.status == COMPLETED and app is not None:
                sub = app.get("submission") or {}
                evidence = sub.get("outcome") == "confirmed" or app["state"] == "VERIFIED"
                if evidence:
                    ref = f" Reference: {sub['reference']}." if sub.get("reference") else ""
                    self.notifier.notify("application_success", f"Application submitted: {name}", f"The application for {name} was submitted and a confirmation was seen.{ref}", dedupe_key=f"success:{task['task_id']}")
        except Exception as exc:  # noqa: BLE001 - a notification problem never affects the queue
            logger.warning("Notification failed: %s", type(exc).__name__)
