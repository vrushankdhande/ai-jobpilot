"""MongoDB access for Phase 7: scheduler state/history, locks, daily counters and reports.

* Locks and counters are atomic (a unique index + conditional upsert), so two processes/threads can neither run the same job at once nor
  overshoot a daily limit.
* Run history and reports are append-only (new documents; nothing is edited or deleted).
"""
from datetime import datetime, timedelta, timezone

from pymongo import DESCENDING, ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from ai.schemas.candidate_profile import new_id

_NO_ID = {"_id": 0}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SchedulerRepository:
    def __init__(self, db: Database):
        self.state_col = db["scheduler_state"]
        self.runs = db["scheduler_runs"]
        self.locks = db["scheduler_locks"]
        self.counters = db["automation_counters"]
        self.reports = db["daily_reports"]

    # --- state (one document) -------------------------------------------------------------------------------
    def get_state(self) -> dict:
        return self.state_col.find_one({"key": "state"}, _NO_ID) or {"key": "state", "enabled": False, "paused": False, "last_runs": {}, "limit_overrides": {}}

    def set_state(self, fields: dict) -> dict:
        self.state_col.update_one({"key": "state"}, {"$set": {**fields, "updated_at": _now()}}, upsert=True)
        return self.get_state()

    def mark_job_ran(self, job: str, when: datetime) -> None:
        self.state_col.update_one({"key": "state"}, {"$set": {f"last_runs.{job}": when, "updated_at": _now()}}, upsert=True)

    # --- locks ------------------------------------------------------------------------------------------------
    def acquire_lock(self, name: str, owner: str, ttl_s: float, now: datetime | None = None) -> bool:
        now = now or _now()
        try:
            res = self.locks.update_one({"name": name, "$or": [{"expires_at": {"$lte": now}}, {"owner": owner}]},
                                        {"$set": {"owner": owner, "expires_at": now + timedelta(seconds=ttl_s), "acquired_at": now}}, upsert=True)
        except DuplicateKeyError:                                    # a live lock held by someone else
            return False
        return bool(res.matched_count or res.upserted_id)

    def release_lock(self, name: str, owner: str) -> None:
        self.locks.delete_one({"name": name, "owner": owner})

    def lock_holder(self, name: str, now: datetime | None = None) -> dict | None:
        return self.locks.find_one({"name": name, "expires_at": {"$gt": now or _now()}}, _NO_ID)

    # --- run history -----------------------------------------------------------------------------------------
    def create_run(self, job: str, trigger: str, now: datetime | None = None) -> dict:
        doc = {"run_id": new_id(), "job": job, "trigger": trigger, "status": "running", "started_at": now or _now(), "finished_at": None, "result": {}, "error": None}
        self.runs.insert_one(dict(doc))
        return doc

    def finish_run(self, run_id: str, status: str, result: dict | None = None, error: str | None = None, now: datetime | None = None) -> None:
        self.runs.update_one({"run_id": run_id}, {"$set": {"status": status, "finished_at": now or _now(), "result": result or {}, "error": error}})

    def list_runs(self, job: str | None = None, limit: int = 50) -> list[dict]:
        return list(self.runs.find({"job": job} if job else {}, _NO_ID).sort("started_at", DESCENDING).limit(limit))

    def latest_run(self, job: str) -> dict | None:
        return self.runs.find_one({"job": job}, _NO_ID, sort=[("started_at", DESCENDING)])

    def close_orphaned_runs(self, started_before: datetime) -> int:
        """Runs still 'running' long after their lock could have expired belong to a crashed process: mark them interrupted (history is kept)."""
        return self.runs.update_many({"status": "running", "started_at": {"$lte": started_before}},
                                     {"$set": {"status": "interrupted", "finished_at": _now(), "error": "interrupted by a restart"}}).modified_count

    # --- daily counters -----------------------------------------------------------------------------------------
    def consume(self, day: str, metric: str, n: int, limit: int) -> bool:
        """Atomically add ``n`` to today's counter only if it stays within ``limit``. False = the limit would be exceeded (nothing changes)."""
        if n <= 0:
            return True
        if limit < n:
            return False
        try:
            return self.counters.find_one_and_update({"day": day, "metric": metric, "count": {"$lte": limit - n}}, {"$inc": {"count": n}, "$set": {"updated_at": _now()}},
                                                     upsert=True, return_document=ReturnDocument.AFTER) is not None
        except DuplicateKeyError:                                    # the row exists but is already too close to the limit
            return False

    def add(self, day: str, metric: str, n: int = 1) -> None:
        """Count something that already happened (no limit check)."""
        self.counters.update_one({"day": day, "metric": metric}, {"$inc": {"count": n}, "$set": {"updated_at": _now()}}, upsert=True)

    def counts(self, day: str) -> dict[str, int]:
        return {d["metric"]: d["count"] for d in self.counters.find({"day": day}, _NO_ID)}

    # --- reports (append-only) ------------------------------------------------------------------------------------
    def save_report(self, report: dict) -> dict:
        self.reports.insert_one(dict(report))
        return report

    def list_reports(self, date: str | None = None, limit: int = 30) -> list[dict]:
        return list(self.reports.find({"date": date} if date else {}, _NO_ID).sort([("date", DESCENDING), ("generated_at", DESCENDING), ("_id", DESCENDING)]).limit(limit))
