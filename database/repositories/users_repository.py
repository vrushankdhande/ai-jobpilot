"""Data access for the (single, local) user and their active master resume."""
from datetime import datetime, timezone

from pymongo.database import Database

USERS = "users"
DEFAULT_USER_ID = "default"


class UsersRepository:
    def __init__(self, db: Database):
        self._users = db[USERS]

    def get_or_create_default_user(self) -> dict:
        now = datetime.now(timezone.utc)
        self._users.update_one(
            {"user_id": DEFAULT_USER_ID},
            {"$setOnInsert": {"user_id": DEFAULT_USER_ID, "active_resume_id": None, "created_at": now}},
            upsert=True,
        )
        return self._users.find_one({"user_id": DEFAULT_USER_ID}, {"_id": 0})

    def get_active_resume_id(self) -> str | None:
        return self.get_or_create_default_user().get("active_resume_id")

    def set_active_resume(self, resume_id: str | None) -> None:
        self.get_or_create_default_user()
        self._users.update_one({"user_id": DEFAULT_USER_ID}, {"$set": {"active_resume_id": resume_id}})

    def clear_active_if(self, resume_id: str) -> None:
        self._users.update_one(
            {"user_id": DEFAULT_USER_ID, "active_resume_id": resume_id},
            {"$set": {"active_resume_id": None}},
        )
