"""The persistent application queue (collection ``application_queue``).

* One entry per (resume, job): the unique ``idempotency_key`` makes duplicate queue entries - and therefore duplicate applications - impossible.
* ``claim_next`` and ``transition`` are atomic compare-and-set operations; every change appends to the task's ``history`` (never rewritten).
* A task stuck in ``processing`` (crash) is recovered by ``recover_stale`` once its lease expired.
"""
from datetime import datetime, timedelta, timezone

from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from ai.schemas.candidate_profile import new_id

_NO_ID = {"_id": 0}
QUEUED, PROCESSING, AWAITING_APPROVAL, COMPLETED, FAILED, MANUAL_HANDOFF, NEEDS_VERIFICATION, PAUSED, CANCELLED = (
    "queued", "processing", "awaiting_approval", "completed", "failed", "manual_handoff", "needs_verification", "paused", "cancelled")
ALL_STATUSES = (QUEUED, PROCESSING, AWAITING_APPROVAL, COMPLETED, FAILED, MANUAL_HANDOFF, NEEDS_VERIFICATION, PAUSED, CANCELLED)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class QueueRepository:
    def __init__(self, db: Database):
        self.col = db["application_queue"]

    def enqueue(self, *, idempotency_key: str, job_id: str, resume_id: str, priority: float, match_score: float | None, eligibility: str | None,
                job_title: str | None, company_name: str | None, source: str | None, max_attempts: int, now: datetime | None = None) -> tuple[dict, bool]:
        """-> (task, created). An existing entry for the same job+candidate is returned untouched (created=False)."""
        now = now or _now()
        doc = {"task_id": new_id(), "idempotency_key": idempotency_key, "job_id": job_id, "resume_id": resume_id, "status": QUEUED, "priority": priority,
               "match_score": match_score, "eligibility": eligibility, "job_title": job_title, "company_name": company_name, "source": source,
               "application_id": None, "attempts": 0, "max_attempts": max_attempts, "next_attempt_at": now, "lease_expires_at": None, "worker_id": None,
               "last_error": None, "paused_from": None, "created_at": now, "updated_at": now,
               "history": [{"at": now, "event": "queued", "status": QUEUED, "detail": ""}]}
        try:
            self.col.insert_one(dict(doc))
            return doc, True
        except DuplicateKeyError:
            return self.find_by_key(idempotency_key), False

    def get(self, task_id: str) -> dict | None:
        return self.col.find_one({"task_id": task_id}, _NO_ID)

    def find_by_key(self, key: str) -> dict | None:
        return self.col.find_one({"idempotency_key": key}, _NO_ID)

    def find_by_application(self, application_id: str) -> dict | None:
        return self.col.find_one({"application_id": application_id}, _NO_ID)

    def find_all(self, *, status: str | None = None, statuses: list[str] | None = None, limit: int = 200, skip: int = 0) -> list[dict]:
        q: dict = {}
        if status:
            q["status"] = status
        if statuses:
            q["status"] = {"$in": statuses}
        return list(self.col.find(q, _NO_ID).sort([("priority", DESCENDING), ("created_at", ASCENDING)]).skip(skip).limit(limit))

    def counts(self) -> dict[str, int]:
        out = {s: 0 for s in ALL_STATUSES}
        for d in self.col.aggregate([{"$group": {"_id": "$status", "n": {"$sum": 1}}}]):
            out[d["_id"]] = d["n"]
        return out

    def processing_count(self, now: datetime | None = None) -> int:
        return self.col.count_documents({"status": PROCESSING, "lease_expires_at": {"$gt": now or _now()}})

    def claim_next(self, worker_id: str, lease_s: float, now: datetime | None = None, max_concurrent: int = 1) -> dict | None:
        """Atomically take the best queued task that is due (highest priority first). None if nothing is due or the concurrency cap is reached."""
        now = now or _now()
        if self.processing_count(now) >= max_concurrent:
            return None
        return self.col.find_one_and_update(
            {"status": QUEUED, "next_attempt_at": {"$lte": now}},
            {"$set": {"status": PROCESSING, "worker_id": worker_id, "lease_expires_at": now + timedelta(seconds=lease_s), "updated_at": now},
             "$inc": {"attempts": 1}, "$push": {"history": {"at": now, "event": "claimed", "status": PROCESSING, "detail": worker_id}}},
            sort=[("priority", DESCENDING), ("created_at", ASCENDING)], projection=_NO_ID, return_document=ReturnDocument.AFTER)

    def transition(self, task_id: str, from_statuses: list[str], to: str, fields: dict | None = None, *, event: str = "", detail: str = "",
                   now: datetime | None = None) -> dict | None:
        """Compare-and-set. None means the task was not in one of ``from_statuses`` (someone else changed it first)."""
        now = now or _now()
        return self.col.find_one_and_update(
            {"task_id": task_id, "status": {"$in": from_statuses}},
            {"$set": {**(fields or {}), "status": to, "updated_at": now}, "$push": {"history": {"at": now, "event": event or to, "status": to, "detail": detail[:300]}}},
            projection=_NO_ID, return_document=ReturnDocument.AFTER)

    def recover_stale(self, now: datetime | None = None) -> list[dict]:
        """Tasks whose worker died (lease expired) go back to the queue - preparing an application is idempotent, so this is safe."""
        now = now or _now()
        out = []
        for t in list(self.col.find({"status": PROCESSING, "lease_expires_at": {"$lte": now}}, _NO_ID)):
            new = self.transition(t["task_id"], [PROCESSING], QUEUED, {"worker_id": None, "lease_expires_at": None, "next_attempt_at": now,
                                                                       "last_error": "worker stopped unexpectedly"}, event="recovered", detail="lease expired", now=now)
            if new:
                out.append(new)
        return out
