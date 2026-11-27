"""Who the current request belongs to, and where that person's data lives.

Every signed-in person has their OWN data: a separate MongoDB database and separate storage folders (resumes, generated documents,
screenshots, Gmail authorisations, job preferences). Nothing is shared between people, so a page cannot show another person's data
by mistake - the database it reads simply does not contain it.

* The OWNER (the first account; it took over the data that existed before logins) keeps the original database and folders unchanged.
* Every other user gets ``<MONGODB_DATABASE>_u_<id>`` and ``storage/users/<id>/...``.

The auth middleware sets ``current_tenant`` for each API request; ``get_settings()`` and ``DatabaseManager.get_database()`` read it.
Code that runs without a request (the scheduler thread, scripts, tests) has no tenant and works on the owner's data, as before.
"""
import re
from contextvars import ContextVar
from dataclasses import dataclass

_ID = re.compile(r"^[0-9a-f]{32}$")


@dataclass(frozen=True)
class Tenant:
    user_id: str
    name: str
    role: str                     # "owner" | "user"

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"


current_tenant: ContextVar[Tenant | None] = ContextVar("jobpilot_tenant", default=None)


def user_dir(user_id: str) -> str:
    if not _ID.match(user_id):
        raise ValueError("invalid user id")
    return f"storage/users/{user_id}"


def database_for(base_database: str, tenant: Tenant | None) -> str:
    if tenant is None or tenant.is_owner:
        return base_database
    if not _ID.match(tenant.user_id):
        raise ValueError("invalid user id")
    return f"{base_database}_u_{tenant.user_id[:16]}"


def setting_overrides(tenant: Tenant | None) -> dict:
    """The settings that differ for this person (empty for the owner)."""
    if tenant is None or tenant.is_owner:
        return {}
    d = user_dir(tenant.user_id)
    return {"resume_storage_dir": f"{d}/resumes", "generated_storage_dir": f"{d}/generated", "screenshots_dir": f"{d}/screenshots",
            "browser_sessions_dir": f"{d}/browser_sessions", "notification_credentials_file": f"{d}/notifications/gmail.cred",
            "job_alert_credentials_file": f"{d}/notifications/gmail_job_alerts.cred", "job_preferences_file": f"{d}/job_preferences.yaml",
            "notification_recipient": "", "notifications_enabled": False}
