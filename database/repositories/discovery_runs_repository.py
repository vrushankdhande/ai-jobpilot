"""Discovery run tracking (collection ``discovery_runs``) - powers /api/jobs/discovery/status."""
from datetime import datetime, timedelta

from pymongo import DESCENDING
from pymongo.database import Database

from ai.schemas.candidate_profile import new_id, utcnow

RUNS = "discovery_runs"
_NO_ID = {"_id": 0}
STALE_AFTER = timedelta(minutes=30)


class DiscoveryRunsRepository:
    def __init__(self, db: Database):
        self._runs = db[RUNS]

    def create(self, query: dict, sources: list[str], skipped: dict[str, str]) -> dict:
        doc = {"run_id": new_id(), "status": "running", "started_at": utcnow(), "finished_at": None, "query": query,
               "requested_sources": sources, "skipped_sources": skipped, "sources": {}, "totals": {}, "error": None}
        self._runs.insert_one(dict(doc))
        return doc

    def update(self, run_id: str, fields: dict) -> None:
        self._runs.update_one({"run_id": run_id}, {"$set": fields})

    def get(self, run_id: str) -> dict | None:
        return self._runs.find_one({"run_id": run_id}, _NO_ID)

    def latest(self) -> dict | None:
        return self._runs.find_one({}, _NO_ID, sort=[("started_at", DESCENDING)])

    def running(self, now: datetime | None = None) -> dict | None:
        """A run that is genuinely in progress (a crashed run older than 30 minutes is not counted)."""
        cutoff = (now or utcnow()) - STALE_AFTER
        return self._runs.find_one({"status": "running", "started_at": {"$gte": cutoff}}, _NO_ID)

    def recent(self, limit: int = 10) -> list[dict]:
        return list(self._runs.find({}, _NO_ID).sort("started_at", DESCENDING).limit(limit))
