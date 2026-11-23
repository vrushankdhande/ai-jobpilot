"""MongoDB access for interviews. The unique ``dup_key`` makes the same interview impossible to record twice."""
import re
from datetime import datetime, timedelta, timezone

from pymongo import ASCENDING, DESCENDING
from pymongo.database import Database

from database.models.interview import InterviewRecord

_NO_ID = {"_id": 0}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class InterviewsRepository:
    def __init__(self, db: Database):
        self.col = db["interviews"]

    def insert(self, rec: InterviewRecord) -> dict:
        """Raises DuplicateKeyError for an identical interview."""
        self.col.insert_one(rec.model_dump(mode="python"))
        return self.get(rec.interview_id)

    def get(self, interview_id: str) -> dict | None:
        return self.col.find_one({"interview_id": interview_id}, _NO_ID)

    def replace_fields(self, interview_id: str, fields: dict, history_entry: dict | None = None) -> dict | None:
        update: dict = {"$set": {**fields, "updated_at": _now()}}
        if history_entry:
            update["$push"] = {"history": history_entry}
        self.col.update_one({"interview_id": interview_id}, update)
        return self.get(interview_id)

    def delete(self, interview_id: str) -> bool:
        return self.col.delete_one({"interview_id": interview_id}).deleted_count == 1

    @staticmethod
    def _query(*, status=None, outcome=None, q=None, date_from=None, date_to=None, upcoming_from=None, application_id=None, job_id=None) -> dict:
        query: dict = {}
        for k, v in (("status", status), ("outcome", outcome), ("application_id", application_id), ("job_id", job_id)):
            if v:
                query[k] = v
        if q:
            rx = {"$regex": re.escape(q), "$options": "i"}
            query["$or"] = [{"company_name": rx}, {"job_title": rx}]
        dates: dict = {}
        if upcoming_from:
            dates["$gte"] = upcoming_from
        if date_from:
            dates["$gte"] = max(date_from, dates.get("$gte", date_from))
        if date_to:
            dates["$lt"] = date_to
        if dates:
            query["scheduled_at"] = dates
        return query

    def find_all(self, *, limit: int = 100, skip: int = 0, ascending: bool = True, **filters) -> list[dict]:
        return list(self.col.find(self._query(**filters), _NO_ID).sort("scheduled_at", ASCENDING if ascending else DESCENDING).skip(skip).limit(limit))

    def count(self, **filters) -> int:
        return self.col.count_documents(self._query(**filters))

    def upcoming(self, now: datetime, hours: int = 24 * 7, limit: int = 50) -> list[dict]:
        return list(self.col.find({"status": "scheduled", "scheduled_at": {"$gte": now, "$lt": now + timedelta(hours=hours)}}, _NO_ID).sort("scheduled_at", ASCENDING).limit(limit))

    def due_reminders(self, now: datetime) -> list[dict]:
        """Scheduled interviews that start within their own reminder window (and have not started yet)."""
        out = []
        for d in self.col.find({"status": "scheduled", "scheduled_at": {"$gte": now}}, _NO_ID).sort("scheduled_at", ASCENDING).limit(200):
            if d["scheduled_at"].replace(tzinfo=timezone.utc) - timedelta(minutes=d.get("reminder_minutes_before", 60)) <= now:
                out.append(d)
        return out
