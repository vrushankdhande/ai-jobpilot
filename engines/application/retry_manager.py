"""Duplicate prevention, idempotency and a deliberately narrow retry policy.

* One application per (resume, job): the idempotency key is unique in MongoDB.
* A second application is refused when one already exists for the same job (or the same application URL / the same posting merged by
  discovery) unless that earlier attempt certainly failed before anything was sent.
* Only TRANSIENT failures that happen BEFORE the submit click (page load timeout, network error) are retried, a bounded number of
  times with back-off. Anything at or after the click - or any uncertain result - is never retried: a human verifies it.
"""
import time
from dataclasses import dataclass, field

from database.models.application import idempotency_key
from database.repositories.applications_repository import ApplicationsRepository
from engines.application.state_machine import ACTIVE, SUBMISSION_MAY_EXIST, AppState

__all__ = ["DuplicateCheck", "RetryManager", "idempotency_key"]


@dataclass
class DuplicateCheck:
    blocked: bool = False
    reason: str = ""
    existing_id: str | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class RetryDecision:
    allowed: bool
    reason: str


class RetryManager:
    def __init__(self, repo: ApplicationsRepository, max_retries: int = 2, base_delay: float = 2.0, sleep=time.sleep):
        self.repo, self.max_retries, self.base_delay, self._sleep = repo, max_retries, base_delay, sleep

    # -------------------------------------------------------------------------------------------- duplicates
    def check_duplicate(self, *, job_id: str, apply_url: str | None, resume_id: str, exclude_id: str | None = None,
                        same_posting_job_ids: list[str] | None = None) -> DuplicateCheck:
        for other in self.repo.find_for_job(job_id, exclude_id):
            if other["state"] in {s.value for s in ACTIVE}:
                return DuplicateCheck(True, f"An application for this job already exists (state {other['state']}).", other["application_id"])
        if apply_url:
            for other in self.repo.find_by_url(apply_url, exclude_id):
                if other["state"] in {s.value for s in SUBMISSION_MAY_EXIST}:
                    return DuplicateCheck(True, "This application URL was already used for an application that may have been sent.", other["application_id"])
        warnings: list[str] = []
        for jid in same_posting_job_ids or []:
            for other in self.repo.find_for_job(jid, exclude_id):
                if other["state"] in {s.value for s in SUBMISSION_MAY_EXIST}:
                    return DuplicateCheck(True, "The same posting (merged duplicate) already has an application that may have been sent.", other["application_id"])
                if other["state"] in {s.value for s in ACTIVE}:
                    warnings.append("A possible duplicate of this job already has an application in progress.")
        return DuplicateCheck(False, "", None, warnings)

    # -------------------------------------------------------------------------------------------- retry policy
    def decide(self, *, transient: bool, submit_clicked: bool, attempts: int) -> RetryDecision:
        if submit_clicked:
            return RetryDecision(False, "The submit button was already clicked - never retried automatically; verify manually.")
        if not transient:
            return RetryDecision(False, "Not a transient failure.")
        if attempts >= self.max_retries:
            return RetryDecision(False, "Retry limit reached.")
        return RetryDecision(True, "Transient failure before submitting.")

    def run(self, fn, *, is_clicked=lambda: False, on_retry=None):
        """Call ``fn`` (which raises BrowserError(transient=...)) with bounded retries. Never retries once ``is_clicked()`` is true."""
        attempts = 0
        while True:
            try:
                return fn()
            except Exception as exc:  # noqa: BLE001
                d = self.decide(transient=bool(getattr(exc, "transient", False)), submit_clicked=is_clicked(), attempts=attempts)
                if not d.allowed:
                    raise
                attempts += 1
                if on_retry:
                    on_retry(attempts, d.reason)
                self._sleep(self.base_delay * (2 ** (attempts - 1)))

    @staticmethod
    def may_prepare_again(state: str | AppState, failure_stage: str | None) -> bool:
        """A failed application can be prepared again only when the failure was certain (nothing was sent)."""
        return AppState(state) == AppState.SUBMISSION_FAILED and failure_stage in ("before_submit", "rejected")
