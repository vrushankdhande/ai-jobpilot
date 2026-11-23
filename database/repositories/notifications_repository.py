"""Notification history (collection ``notifications``): delivery status per message; a unique dedupe key stops repeat sends."""
from datetime import datetime, timezone

from pymongo import DESCENDING
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from ai.schemas.candidate_profile import new_id

_NO_ID = {"_id": 0}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class NotificationsRepository:
    def __init__(self, db: Database):
        self.col = db["notifications"]

    def create(self, *, type_: str, subject: str, recipient_masked: str, dedupe_key: str | None, status: str = "queued", payload: dict | None = None) -> tuple[dict | None, bool]:
        """-> (doc, created). Returns (existing, False) when this dedupe key was already used."""
        doc = {"notification_id": new_id(), "type": type_, "subject": subject[:200], "recipient": recipient_masked, "status": status, "attempts": 0,
               "error": None, "provider_message_id": None, "created_at": _now(), "sent_at": None, "updated_at": _now(), "payload": payload or {}}
        if dedupe_key:
            doc["dedupe_key"] = dedupe_key
        try:
            self.col.insert_one(dict(doc))
            return doc, True
        except DuplicateKeyError:
            return self.col.find_one({"dedupe_key": dedupe_key}, _NO_ID), False

    def update(self, notification_id: str, fields: dict) -> None:
        self.col.update_one({"notification_id": notification_id}, {"$set": {**fields, "updated_at": _now()}})

    def bump_attempts(self, notification_id: str) -> int:
        d = self.col.find_one_and_update({"notification_id": notification_id}, {"$inc": {"attempts": 1}}, projection=_NO_ID, return_document=True)
        return d["attempts"] if d else 0

    def get(self, notification_id: str) -> dict | None:
        return self.col.find_one({"notification_id": notification_id}, _NO_ID)

    def find_all(self, *, status: str | None = None, type_: str | None = None, limit: int = 100) -> list[dict]:
        q = {k: v for k, v in (("status", status), ("type", type_)) if v}
        return list(self.col.find(q, {"_id": 0, "payload": 0}).sort("created_at", DESCENDING).limit(limit))

    def failed_retryable(self, max_attempts: int, limit: int = 20) -> list[dict]:
        return list(self.col.find({"status": "failed", "attempts": {"$lt": max_attempts}}, _NO_ID).sort("created_at", 1).limit(limit))
