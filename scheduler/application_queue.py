"""Queue rules: who may be queued, in what order, and how a queue entry follows its application.

Queueing a job NEVER submits anything. A queued job is only *prepared* by the worker (Phase 6 ``prepare``); the candidate approves and submits.
"""
from datetime import datetime, timezone

from database.models.application import idempotency_key
from database.repositories.applications_repository import ApplicationsRepository
from database.repositories.queue_repository import (
    AWAITING_APPROVAL, CANCELLED, COMPLETED, FAILED, MANUAL_HANDOFF, NEEDS_VERIFICATION, PAUSED, PROCESSING, QUEUED, QueueRepository,
)
from engines.application.state_machine import SUBMISSION_MAY_EXIST, AppState

# application state -> queue status (the queue mirrors reality; it never invents progress)
APP_TO_QUEUE = {
    AppState.DISCOVERED: PROCESSING, AppState.READY_FOR_REVIEW: AWAITING_APPROVAL, AppState.AWAITING_USER_INPUT: AWAITING_APPROVAL, AppState.APPROVED: AWAITING_APPROVAL,
    AppState.SUBMITTING: PROCESSING, AppState.SUBMITTED: COMPLETED, AppState.VERIFIED: COMPLETED, AppState.SUBMISSION_FAILED: FAILED,
    AppState.VERIFICATION_PENDING: NEEDS_VERIFICATION, AppState.MANUAL_HANDOFF_REQUIRED: MANUAL_HANDOFF,
}


class NotQueueable(Exception):
    """Why a job was not queued (user-safe)."""


def priority_for(match_score: float, eligibility: str | None) -> float:
    """Higher first. A confirmed-eligible job outranks an equally good job whose eligibility is still unknown."""
    return float(match_score) + (10.0 if eligibility == "PASS" else 0.0)


class ApplicationQueue:
    def __init__(self, repo: QueueRepository, apps: ApplicationsRepository, limits_fn, now_fn=lambda: datetime.now(timezone.utc)):
        """``limits_fn()`` is read on every call, so a dashboard change (min score, retries) applies to the next enqueue."""
        self.repo, self.apps, self.limits_fn, self.now = repo, apps, limits_fn, now_fn

    def enqueue(self, *, job_id: str, resume_id: str, match_score: float, eligibility: str | None, job_title=None, company_name=None, source=None) -> tuple[dict, bool]:
        if eligibility == "FAIL":
            raise NotQueueable("The job failed the eligibility check; it is not queued.")
        lim = self.limits_fn()
        if match_score is None or match_score < lim.min_match_score:
            raise NotQueueable(f"Match score {match_score} is below the configured minimum {lim.min_match_score:g}.")
        key = idempotency_key(resume_id, job_id)
        app = self.apps.find_by_key(key)
        if app and AppState(app["state"]) in SUBMISSION_MAY_EXIST:
            raise NotQueueable(f"An application for this job already exists (state {app['state']}).")
        task, created = self.repo.enqueue(idempotency_key=key, job_id=job_id, resume_id=resume_id, priority=priority_for(match_score, eligibility), match_score=match_score,
                                          eligibility=eligibility, job_title=job_title, company_name=company_name, source=source, max_attempts=lim.max_retries_per_application + 1, now=self.now())
        return task, created

    # --- user controls ------------------------------------------------------------------------------------------
    def pause(self, task_id: str) -> dict | None:
        t = self.repo.get(task_id)
        return self.repo.transition(task_id, [QUEUED], PAUSED, {"paused_from": QUEUED}, event="paused", now=self.now()) if t else None

    def resume(self, task_id: str) -> dict | None:
        return self.repo.transition(task_id, [PAUSED], QUEUED, {"paused_from": None, "next_attempt_at": self.now()}, event="resumed", now=self.now())

    def cancel(self, task_id: str) -> dict | None:
        """Cancel before anything was sent. A task whose application may already be submitted cannot be cancelled."""
        t = self.repo.get(task_id)
        if t is None:
            return None
        app = self.apps.find_by_key(t["idempotency_key"])
        if app and AppState(app["state"]) in SUBMISSION_MAY_EXIST:
            raise NotQueueable("This application may already have been submitted; it cannot be cancelled here.")
        return self.repo.transition(task_id, [QUEUED, PAUSED, PROCESSING, AWAITING_APPROVAL, FAILED, MANUAL_HANDOFF], CANCELLED, {"lease_expires_at": None, "worker_id": None},
                                    event="cancelled", now=self.now())

    # --- follow the application ---------------------------------------------------------------------------------------
    def status_for_application(self, app: dict) -> str:
        return APP_TO_QUEUE[AppState(app["state"])]
