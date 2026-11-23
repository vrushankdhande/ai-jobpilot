"""Resume management API (/api/resumes)."""
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from ai.schemas.candidate_profile import CandidateProfile, ProfileContent
from backend.services.resume_service import (
    ProfileNotFound,
    ResumeConflict,
    ResumeNotFound,
    ResumeService,
)
from config.settings import Settings, get_settings
from database.connection import DatabaseManager, get_database_manager
from database.repositories.resumes_repository import ResumesRepository
from database.repositories.users_repository import UsersRepository
from engines.resume.ai_extractor import build_ai_extractor
from engines.resume.document_validator import ResumeValidationError

router = APIRouter(prefix="/api/resumes", tags=["resumes"])


def get_resume_service(
    manager: DatabaseManager = Depends(get_database_manager),
    settings: Settings = Depends(get_settings),
) -> ResumeService:
    db = manager.get_database()  # DatabaseConnectionError -> 503 via app handler
    return ResumeService(
        ResumesRepository(db), UsersRepository(db), settings, ai_extractor=build_ai_extractor(settings)
    )


# ---- response / request models ------------------------------------------
class ResumeOut(BaseModel):
    resume_id: str
    original_filename: str
    file_type: str
    size_bytes: int
    uploaded_at: datetime
    parsing_status: str
    parsing_error: str | None = None
    review_status: str | None = None
    profile_version: int | None = None
    is_active: bool = False


class UploadResponse(BaseModel):
    resume: ResumeOut
    duplicate: bool
    message: str


class StatusOut(BaseModel):
    resume_id: str
    parsing_status: str
    parsing_error: str | None = None
    review_status: str | None = None
    profile_version: int | None = None


class ProfileUpdateRequest(ProfileContent):
    review_status: Literal["pending_review", "reviewed"] | None = None


def _out(doc: dict, active_id: str | None) -> ResumeOut:
    return ResumeOut(**{k: doc.get(k) for k in ResumeOut.model_fields if k != "is_active"},
                     is_active=doc["resume_id"] == active_id)


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, ResumeValidationError):
        return HTTPException(exc.status_code, exc.message)
    if isinstance(exc, (ResumeNotFound, ProfileNotFound)):
        return HTTPException(404, "Resume or profile not found.")
    if isinstance(exc, ResumeConflict):
        return HTTPException(409, str(exc))
    raise exc


# ---- endpoints -------------------------------------------------------------
@router.post("/upload", response_model=UploadResponse, status_code=201, summary="Upload a master resume")
async def upload_resume(
    response: Response,
    file: UploadFile = File(...),
    service: ResumeService = Depends(get_resume_service),
):
    max_bytes = service.settings.max_upload_mb * 1024 * 1024
    data = bytearray()
    while chunk := await file.read(1024 * 1024):
        data += chunk
        if len(data) > max_bytes:
            raise HTTPException(413, f"File exceeds the {service.settings.max_upload_mb} MB limit.")
    try:
        doc, duplicate = await run_in_threadpool(service.upload, file.filename, bytes(data))
    except (ResumeValidationError, ResumeNotFound) as exc:
        raise _http(exc) from None
    if duplicate:
        response.status_code = 200
    msg = "This exact file was already uploaded." if duplicate else f"Upload complete: {doc['parsing_status']}."
    return UploadResponse(resume=_out(doc, service.active_resume_id()), duplicate=duplicate, message=msg)


@router.get("", response_model=list[ResumeOut], summary="List uploaded resumes")
def list_resumes(service: ResumeService = Depends(get_resume_service)):
    active = service.active_resume_id()
    return [_out(d, active) for d in service.list_resumes()]


@router.get("/{resume_id}", response_model=ResumeOut, summary="Resume details")
def get_resume(resume_id: str, service: ResumeService = Depends(get_resume_service)):
    try:
        return _out(service.get_resume(resume_id), service.active_resume_id())
    except ResumeNotFound as exc:
        raise _http(exc) from None


@router.post("/{resume_id}/parse", response_model=ResumeOut, summary="Start or retry parsing")
def parse_resume(
    resume_id: str,
    force: bool = Query(False, description="Re-parse even if a profile exists (old one is archived)."),
    service: ResumeService = Depends(get_resume_service),
):
    try:
        return _out(service.parse(resume_id, force=force), service.active_resume_id())
    except (ResumeNotFound, ResumeConflict) as exc:
        raise _http(exc) from None


@router.get("/{resume_id}/profile", response_model=CandidateProfile, summary="Extracted candidate profile")
def get_profile(resume_id: str, service: ResumeService = Depends(get_resume_service)):
    try:
        return service.get_profile(resume_id)
    except (ResumeNotFound, ProfileNotFound) as exc:
        raise _http(exc) from None


@router.put("/{resume_id}/profile", response_model=CandidateProfile, summary="Save profile corrections")
def update_profile(
    resume_id: str, body: ProfileUpdateRequest, service: ResumeService = Depends(get_resume_service)
):
    try:
        return service.update_profile(resume_id, ProfileContent.model_validate(body.model_dump(exclude={"review_status"})), body.review_status)
    except (ResumeNotFound, ProfileNotFound) as exc:
        raise _http(exc) from None


@router.get("/{resume_id}/profile/versions", response_model=list[CandidateProfile], summary="Profile version history")
def list_versions(resume_id: str, service: ResumeService = Depends(get_resume_service)):
    try:
        return service.list_versions(resume_id)
    except ResumeNotFound as exc:
        raise _http(exc) from None


@router.get("/{resume_id}/status", response_model=StatusOut, summary="Parsing status")
def get_status(resume_id: str, service: ResumeService = Depends(get_resume_service)):
    try:
        return StatusOut(**{k: service.get_resume(resume_id).get(k) for k in StatusOut.model_fields})
    except ResumeNotFound as exc:
        raise _http(exc) from None


@router.post("/{resume_id}/activate", response_model=ResumeOut, summary="Make this the active master resume")
def activate_resume(resume_id: str, service: ResumeService = Depends(get_resume_service)):
    try:
        return _out(service.activate(resume_id), resume_id)
    except ResumeNotFound as exc:
        raise _http(exc) from None


@router.delete("/{resume_id}", status_code=204, summary="Delete a resume and all derived data")
def delete_resume(resume_id: str, service: ResumeService = Depends(get_resume_service)):
    try:
        service.delete(resume_id)
    except ResumeNotFound as exc:
        raise _http(exc) from None
    return Response(status_code=204)
