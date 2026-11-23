"""Interview tracker API (/api/interviews). Your own records: create, update, list, upcoming and reminders."""
import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from pydantic import BaseModel, ConfigDict, ValidationError
from pymongo.errors import DuplicateKeyError

from config.settings import Settings, get_settings
from database.connection import DatabaseManager, get_database_manager
from database.models.interview import InterviewFields, InterviewRecord, Kind, Outcome, Status, as_utc, dup_key
from database.repositories.applications_repository import ApplicationsRepository
from database.repositories.interviews_repository import InterviewsRepository

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/interviews", tags=["interviews"])
HEX32 = r"^[0-9a-f]{32}$"
InterviewId = Annotated[str, Path(pattern=HEX32, description="32-character hex interview id")]


def get_interviews(manager: DatabaseManager = Depends(get_database_manager)) -> InterviewsRepository:
    return InterviewsRepository(manager.get_database())


class InterviewUpdate(BaseModel):
    """Every field optional; unknown fields are refused."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    company_name: str | None = None
    job_title: str | None = None
    round: str | None = None
    kind: Kind | None = None
    scheduled_at: datetime | None = None
    duration_minutes: int | None = None
    meeting_link: str | None = None
    location: str | None = None
    interviewers: str | None = None
    preparation_notes: str | None = None
    feedback: str | None = None
    status: Status | None = None
    outcome: Outcome | None = None
    reminder_minutes_before: int | None = None


def _public(doc: dict) -> dict:
    doc = dict(doc)
    doc.pop("dup_key", None)
    return doc


def _tz(settings: Settings) -> str:
    return settings.scheduler_timezone or "Asia/Kolkata"


@router.post("", status_code=201, summary="Add an interview")
def create(body: InterviewFields, repo: InterviewsRepository = Depends(get_interviews), manager: DatabaseManager = Depends(get_database_manager),
           settings: Settings = Depends(get_settings)):
    data = body.model_dump()
    data["scheduled_at"] = as_utc(body.scheduled_at, _tz(settings))
    if body.application_id and ApplicationsRepository(manager.get_database()).get(body.application_id) is None:
        raise HTTPException(404, "The linked application was not found.")
    rec = InterviewRecord(**data, dup_key=dup_key(body.company_name, body.job_title, body.round, data["scheduled_at"]))
    try:
        return _public(repo.insert(rec))
    except DuplicateKeyError:
        raise HTTPException(409, "This interview is already recorded (same company, role, round and time).") from None


@router.get("", summary="List interviews (filters, pagination)")
def list_interviews(status: Status | None = None, outcome: Outcome | None = None, q: str | None = Query(default=None, max_length=100),
                    date_from: datetime | None = None, date_to: datetime | None = None, application_id: str | None = Query(default=None, pattern=HEX32),
                    limit: int = Query(default=50, ge=1, le=200), skip: int = Query(default=0, ge=0), newest_first: bool = False,
                    repo: InterviewsRepository = Depends(get_interviews), settings: Settings = Depends(get_settings)):
    f = dict(status=status, outcome=outcome, q=q, application_id=application_id,
             date_from=as_utc(date_from, _tz(settings)) if date_from else None, date_to=as_utc(date_to, _tz(settings)) if date_to else None)
    return {"total": repo.count(**f), "items": [_public(d) for d in repo.find_all(limit=limit, skip=skip, ascending=not newest_first, **f)]}


@router.get("/upcoming", summary="Scheduled interviews in the next N hours")
def upcoming(hours: int = Query(default=168, ge=1, le=24 * 90), repo: InterviewsRepository = Depends(get_interviews)):
    return [_public(d) for d in repo.upcoming(datetime.now(timezone.utc), hours)]


@router.get("/reminders", summary="Interviews inside their reminder window right now")
def reminders(repo: InterviewsRepository = Depends(get_interviews)):
    return [_public(d) for d in repo.due_reminders(datetime.now(timezone.utc))]


@router.get("/{interview_id}", summary="One interview")
def get_interview(interview_id: InterviewId, repo: InterviewsRepository = Depends(get_interviews)):
    d = repo.get(interview_id)
    if d is None:
        raise HTTPException(404, "Interview not found.")
    return _public(d)


@router.put("/{interview_id}", summary="Update an interview (schedule, status, outcome, notes, feedback)")
def update_interview(interview_id: InterviewId, body: InterviewUpdate, repo: InterviewsRepository = Depends(get_interviews), settings: Settings = Depends(get_settings)):
    cur = repo.get(interview_id)
    if cur is None:
        raise HTTPException(404, "Interview not found.")
    changes = body.model_dump(exclude_unset=True)
    if changes.get("scheduled_at"):
        changes["scheduled_at"] = as_utc(changes["scheduled_at"], _tz(settings))
    merged = {**{k: cur[k] for k in InterviewFields.model_fields if k in cur}, **{k: v for k, v in changes.items() if v is not None or k in ("meeting_link", "location", "interviewers", "preparation_notes", "feedback")}}
    try:
        InterviewFields.model_validate(merged)                                   # same validation as creation
    except ValidationError as exc:
        raise HTTPException(422, "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())) from None
    merged_clean = InterviewFields.model_validate(merged).model_dump()
    merged_clean["scheduled_at"] = as_utc(merged_clean["scheduled_at"], _tz(settings))
    fields = {k: merged_clean[k] for k in changes if k in merged_clean}
    fields["dup_key"] = dup_key(merged_clean["company_name"], merged_clean["job_title"], merged_clean["round"], merged_clean["scheduled_at"])
    entry = {"at": datetime.now(timezone.utc), "changed": sorted(k for k in changes if cur.get(k) != merged_clean.get(k))}
    try:
        return _public(repo.replace_fields(interview_id, fields, entry if entry["changed"] else None))
    except DuplicateKeyError:
        raise HTTPException(409, "Another interview with the same company, role, round and time already exists.") from None


@router.delete("/{interview_id}", status_code=204, summary="Delete an interview record")
def delete_interview(interview_id: InterviewId, repo: InterviewsRepository = Depends(get_interviews)):
    if not repo.delete(interview_id):
        raise HTTPException(404, "Interview not found.")
    return Response(status_code=204)
