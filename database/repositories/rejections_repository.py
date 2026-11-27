"""Rejection e-mails found in the candidate's Gmail (collection ``rejection_emails``).

One row per e-mail (``message_key`` = a hash of Gmail's message id, unique), so an e-mail is processed once - also after an Undo. A row keeps
only what the Applied Jobs page shows: company, role, sender domain, date, the short rejection phrase and the decision. Never the e-mail text.
"""
import hashlib
from datetime import datetime, timezone

from pymongo import ASCENDING, DESCENDING
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from ai.schemas.candidate_profile import new_id

COLLECTION = "rejection_emails"
OPEN = ("needs_confirmation", "unmatched")
_P = {"_id": 0, "message_key": 0}


def message_key(message_id: str) -> str:
    return hashlib.sha256(f"gmail|{message_id}".encode()).hexdigest()[:40]


class RejectionsRepository:
    def __init__(self, db: Database):
        self.col = db[COLLECTION]
        self.checks = db["rejection_checks"]          # one document: the summary of the last check (counts only)

    def seen(self, message_id: str) -> bool:
        return self.col.count_documents({"message_key": message_key(message_id)}, limit=1) > 0

    def insert(self, message_id: str, row: dict) -> dict | None:
        """None when this e-mail was already recorded (a concurrent check)."""
        doc = {"signal_id": new_id(), "message_key": message_key(message_id), "created_at": datetime.now(timezone.utc), **row}
        try:
            self.col.insert_one(dict(doc))
        except DuplicateKeyError:
            return None
        doc.pop("message_key")
        return doc

    def get(self, signal_id: str) -> dict | None:
        return self.col.find_one({"signal_id": signal_id}, _P)

    def update(self, signal_id: str, fields: dict, *, only_if: tuple[str, ...] | None = None) -> dict | None:
        q: dict = {"signal_id": signal_id}
        if only_if:
            q["status"] = {"$in": list(only_if)}
        return self.col.find_one_and_update(q, {"$set": fields}, projection=_P, return_document=True)

    def list(self, statuses: tuple[str, ...] | None = None, limit: int = 100) -> list[dict]:
        q = {"status": {"$in": list(statuses)}} if statuses else {}
        return list(self.col.find(q, _P).sort([("received_at", DESCENDING), ("signal_id", ASCENDING)]).limit(limit))

    def count(self, statuses: tuple[str, ...]) -> int:
        return self.col.count_documents({"status": {"$in": list(statuses)}})

    def save_check(self, summary: dict) -> None:
        self.checks.replace_one({"key": "last"}, {"key": "last", **summary}, upsert=True)

    def last_check(self) -> dict | None:
        return self.checks.find_one({"key": "last"}, {"_id": 0, "key": 0})
