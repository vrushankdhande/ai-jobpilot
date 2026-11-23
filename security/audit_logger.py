"""Audit trail for applications: appends to the immutable ``application_events`` log after stripping anything personal."""
import logging

from database.repositories.applications_repository import ApplicationsRepository
from security.pii_protection import safe_detail

logger = logging.getLogger(__name__)


class AuditLogger:
    def __init__(self, repo: ApplicationsRepository):
        self.repo = repo

    def record(self, application_id: str, type_: str, *, from_state: str | None = None, to_state: str | None = None, actor: str = "system",
               **detail) -> dict:
        ev = self.repo.add_event(application_id, type_, from_state=from_state, to_state=to_state, actor=actor, detail=safe_detail(detail))
        logger.info("Application event: app=%s type=%s %s->%s actor=%s", application_id, type_, from_state, to_state, actor)  # ids/types only
        return ev
