"""Interview records (collection ``interviews``). Entered by the candidate - nothing is inferred or invented."""
import hashlib
import re
from datetime import datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ai.schemas.candidate_profile import new_id, utcnow
from integrations.base_adapter import public_http_url

Status = Literal["scheduled", "completed", "cancelled", "no_show"]
Outcome = Literal["pending", "advanced", "rejected", "offer", "withdrawn"]
Kind = Literal["phone", "video", "onsite", "technical", "hr", "other"]
HEX32 = r"^[0-9a-f]{32}$"


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def as_utc(value: datetime, tz_name: str = "Asia/Kolkata") -> datetime:
    """Aware datetimes are converted to UTC; a naive one is read as local time in ``tz_name`` (the scheduler time zone)."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo(tz_name))
    return value.astimezone(timezone.utc)


def dup_key(company: str, title: str, round_: str, scheduled_at: datetime) -> str:
    """The same company + role + round at the same instant is the same interview (duplicates are refused)."""
    return hashlib.sha256("|".join([_norm(company), _norm(title), _norm(round_), scheduled_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M")]).encode()).hexdigest()


class InterviewFields(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    company_name: str = Field(min_length=1, max_length=120)
    job_title: str = Field(min_length=1, max_length=160)
    round: str = Field(default="Round 1", min_length=1, max_length=60)
    kind: Kind = "video"
    scheduled_at: datetime
    duration_minutes: int = Field(default=60, ge=5, le=600)
    meeting_link: str | None = Field(default=None, max_length=500)
    location: str | None = Field(default=None, max_length=200)
    interviewers: str | None = Field(default=None, max_length=300)
    preparation_notes: str | None = Field(default=None, max_length=5000)
    feedback: str | None = Field(default=None, max_length=5000)
    status: Status = "scheduled"
    outcome: Outcome = "pending"
    reminder_minutes_before: int = Field(default=60, ge=0, le=10080)
    application_id: str | None = Field(default=None, pattern=HEX32)
    job_id: str | None = Field(default=None, pattern=HEX32)

    @field_validator("meeting_link")
    @classmethod
    def _link(cls, v):
        if v in (None, ""):
            return None
        ok = public_http_url(v)
        if ok is None:
            raise ValueError("The meeting link must be a public http(s) URL")
        return ok

    @field_validator("location", "interviewers", "preparation_notes", "feedback", mode="before")
    @classmethod
    def _blank_to_none(cls, v):
        return None if isinstance(v, str) and not v.strip() else v


class InterviewRecord(InterviewFields):
    interview_id: str = Field(default_factory=new_id)
    dup_key: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    history: list[dict] = Field(default_factory=list)      # what changed and when (field names only)
