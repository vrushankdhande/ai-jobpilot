"""MongoDB access for applications + the append-only event log.

* ``application_events`` has insert-only methods: there is no update or delete for events anywhere in the code base
  (a unique (application_id, seq) index also makes a concurrent writer fail instead of overwriting).
* ``transition`` is an atomic compare-and-set on the current state, so two concurrent submit requests cannot both start.
"""
import re
from datetime import datetime, timezone
from typing import Iterable

from pymongo import ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from ai.schemas.candidate_profile import new_id
from database.models.application import ApplicationRecord

_PROJECTION = {"_id": 0}


class ApplicationsRepository:
    def __init__(self, db: Database):
        self.col = db["applications"]
        self.events = db["application_events"]

    # --- applications ---------------------------------------------------------------------------------------
    def insert(self, rec: ApplicationRecord) -> dict:
        """Raises DuplicateKeyError if an application for this (resume, job) already exists (idempotency)."""
        self.col.insert_one(rec.model_dump(mode="python"))
        return self.get(rec.application_id)

    def get(self, application_id: str) -> dict | None:
        return self.col.find_one({"application_id": application_id}, _PROJECTION)

    def find_by_key(self, key: str) -> dict | None:
        return self.col.find_one({"idempotency_key": key}, _PROJECTION)

    def find_for_job(self, job_id: str, exclude_id: str | None = None) -> list[dict]:
        q: dict = {"job_id": job_id}
        if exclude_id:
            q["application_id"] = {"$ne": exclude_id}
        return list(self.col.find(q, _PROJECTION))

    def find_by_url(self, url: str, exclude_id: str | None = None) -> list[dict]:
        q: dict = {"apply_url": url}
        if exclude_id:
            q["application_id"] = {"$ne": exclude_id}
        return list(self.col.find(q, _PROJECTION))

    def update(self, application_id: str, fields: dict) -> bool:
        fields = {**fields, "updated_at": datetime.now(timezone.utc)}
        return self.col.update_one({"application_id": application_id}, {"$set": fields}).matched_count == 1

    def transition(self, application_id: str, from_states: Iterable[str], to_state: str, fields: dict | None = None) -> dict | None:
        """Atomically move to ``to_state`` only if the current state is one of ``from_states``. None = the guard failed."""
        set_fields = {**(fields or {}), "state": to_state, "updated_at": datetime.now(timezone.utc)}
        return self.col.find_one_and_update({"application_id": application_id, "state": {"$in": list(from_states)}}, {"$set": set_fields},
                                            projection=_PROJECTION, return_document=ReturnDocument.AFTER)

    def increment(self, application_id: str, field: str) -> None:
        self.col.update_one({"application_id": application_id}, {"$inc": {field: 1}})

    @staticmethod
    def _filter(*, state=None, states=None, job_id=None, resume_id=None, source=None, q=None, date_from=None, date_to=None, dismissed=None, employer_outcome=None) -> dict:
        query: dict = {}
        if state:
            query["state"] = state
        if states:
            query["state"] = {"$in": states}
        for k, v in (("job_id", job_id), ("resume_id", resume_id), ("source", source)):
            if v:
                query[k] = v
        if q:
            rx = {"$regex": re.escape(q), "$options": "i"}
            query["$or"] = [{"company_name": rx}, {"job_title": rx}, {"user_notes": rx}, {"submission.reference": rx}]
        if date_from or date_to:
            # the date the list SHOWS: when it was submitted (or recorded as applied), else when the application was created - never the last
            # change, so adding a note or a reference later does not move an application to another day
            rng = {**({"$gte": date_from} if date_from else {}), **({"$lt": date_to} if date_to else {})}
            query["$and"] = [{"$or": [{"submitted_at": rng}, {"submitted_at": None, "created_at": rng}]}]
        if dismissed is not None:
            query["dismissed_at"] = {"$ne": None} if dismissed else None
        if employer_outcome:          # "rejected" = the employer said no; "none" = no answer recorded yet
            query["employer_response.outcome"] = None if employer_outcome == "none" else employer_outcome
        return query

    def find_all(self, *, limit: int = 100, skip: int = 0, **filters) -> list[dict]:
        # application_id breaks ties: without it, records changed at the same moment can repeat or go missing between pages
        return list(self.col.find(self._filter(**filters), _PROJECTION).sort([("updated_at", -1), ("application_id", 1)]).skip(skip).limit(limit))

    def count(self, **filters) -> int:
        return self.col.count_documents(self._filter(**filters))
    def count_submitted_since(self, since: datetime, source: str | None = None) -> int:
        q: dict = {"submission.at": {"$gte": since}, "mode": "automated", "state": {"$in": ["SUBMITTING", "SUBMITTED", "VERIFICATION_PENDING", "VERIFIED"]}}
        if source:
            q["source"] = source
        return self.col.count_documents(q)

    def redact_for_resume(self, resume_id: str) -> int:
        """Deleting a resume removes the personal values copied into applications (history and states are kept)."""
        return self.col.update_many({"resume_id": resume_id}, {"$set": {"mapping": [], "uploads": [], "notes": []}}).matched_count

    # --- events (append only) -------------------------------------------------------------------------------
    def add_event(self, application_id: str, type_: str, *, from_state: str | None = None, to_state: str | None = None, actor: str = "system",
                  detail: dict | None = None) -> dict:
        for _ in range(8):
            last = self.events.find_one({"application_id": application_id}, {"seq": 1, "_id": 0}, sort=[("seq", -1)])
            ev = {"event_id": new_id(), "application_id": application_id, "seq": (last["seq"] + 1) if last else 1,
                  "at": datetime.now(timezone.utc), "type": type_, "from_state": from_state, "to_state": to_state, "actor": actor,
                  "detail": detail or {}}
            try:
                self.events.insert_one(dict(ev))
                return ev
            except DuplicateKeyError:  # another writer took this seq: try the next one
                continue
        raise RuntimeError("could not append application event")

    def list_events(self, application_id: str) -> list[dict]:
        return list(self.events.find({"application_id": application_id}, _PROJECTION).sort("seq", 1))
