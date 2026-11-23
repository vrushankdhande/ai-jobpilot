"""Document generation API (/api/documents)."""
import logging
import urllib.parse
from datetime import datetime
from typing import Literal

import yaml
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from pydantic import BaseModel, Field, ValidationError, field_validator

from ai.schemas.candidate_profile import ValidationIssue
from backend.api.routes.matching import get_matching_service
from backend.services.document_service import (
    DocumentNotFound, DocumentService, ExportRefused, NotApprovable,
)
from backend.services.matching_service import JobNotFound, MatchNotFound, MatchingService, NoActiveResume, ProfileMissing, UnreviewedProfile
from config.scoring_config import load_preferences, load_scoring_config
from config.settings import Settings, get_settings
from database.connection import DatabaseManager, get_database_manager
from database.repositories.documents_repository import DocumentsRepository
from engines.generation.ai_writer import build_document_client, document_ai_status
from engines.matching.skill_extractor import get_catalog

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/documents", tags=["documents"])
ID = Path(pattern=r"^[0-9a-f]{32}$", description="32-character hex id")
HEX32 = r"^[0-9a-f]{32}$"


def get_document_service(manager: DatabaseManager = Depends(get_database_manager), settings: Settings = Depends(get_settings),
                         matching: MatchingService = Depends(get_matching_service)) -> DocumentService:
    db = manager.get_database()
    try:
        cfg, prefs, catalog = load_scoring_config(settings), load_preferences(settings), get_catalog(settings)
    except (ValidationError, yaml.YAMLError, OSError, KeyError) as exc:
        logger.error("Document configuration is invalid: %s", type(exc).__name__)
        raise HTTPException(500, "Configuration is invalid. Check config/*.yaml.") from None
    return DocumentService(DocumentsRepository(db), matching, settings, catalog, prefs, cfg, ai=build_document_client(settings))


# ---- models ----------------------------------------------------------------------------------------------
class GenerateBase(BaseModel):
    job_id: str = Field(pattern=HEX32)
    resume_id: str | None = Field(default=None, max_length=64, description="Defaults to the active resume")
    match_id: str | None = Field(default=None, pattern=HEX32)
    allow_unreviewed: bool = Field(default=False, description="Explicit confirmation to use an unreviewed profile")
    use_ai: bool | None = Field(default=None, description="false forces the deterministic writer")


class ResumeGenerateRequest(GenerateBase):
    max_projects: int = Field(default=3, ge=0, le=6)
    bullets_recent: int = Field(default=5, ge=1, le=8)
    bullets_older: int = Field(default=3, ge=0, le=6)


class CoverLetterGenerateRequest(GenerateBase):
    pass


class QuestionIn(BaseModel):
    id: str | None = Field(default=None, pattern=r"^[a-z0-9_]{1,40}$")
    text: str = Field(min_length=3, max_length=300)

    @field_validator("text")
    @classmethod
    def _strip(cls, v):
        return v.strip()


class ScreeningGenerateRequest(GenerateBase):
    questions: list[QuestionIn] | None = Field(default=None, max_length=15)
    length: Literal["short", "medium", "long"] = "medium"
    candidate_inputs: dict[Literal["notice_period", "salary_expectation", "relocation", "work_authorization"], str] = Field(default_factory=dict)

    @field_validator("candidate_inputs")
    @classmethod
    def _inputs(cls, v):
        return {k: s.strip()[:200] for k, s in v.items() if s and s.strip()}


class UpdateRequest(BaseModel):
    content: dict


class ApproveRequest(BaseModel):
    acknowledge_warnings: bool = False


class ExportRequest(BaseModel):
    formats: list[Literal["docx", "pdf"]] = Field(default_factory=lambda: ["docx", "pdf"], min_length=1, max_length=2)


class ExportOut(BaseModel):
    format: str
    download_name: str
    created_at: datetime
    size_bytes: int
    from_approved: bool
    warnings: list[str] = []


class ValidationOut(BaseModel):
    status: str
    issues: list[ValidationIssue] = []
    validated_at: datetime | None = None
    claims_checked: int = 0


class DocumentSummary(BaseModel):
    document_id: str
    group_id: str
    document_type: str
    version: int
    parent_version_id: str | None = None
    profile_id: str
    profile_version: int
    resume_id: str
    job_id: str
    match_id: str | None = None
    job_title: str | None = None
    company_name: str | None = None
    generated_at: datetime
    template_version: str
    generator: str
    ai_model: str | None = None
    edited: bool = False
    validation_status: str
    review_status: str
    approved_at: datetime | None = None
    exports: list[ExportOut] = []


class DocumentOut(DocumentSummary):
    content: dict
    validation: ValidationOut
    notes: list[str] = []
    profile_stale: bool = False


def _summary(d: dict) -> DocumentSummary:
    return DocumentSummary(
        **{k: d.get(k) for k in ("document_id", "group_id", "document_type", "version", "parent_version_id", "profile_id", "profile_version",
                                  "resume_id", "job_id", "match_id", "job_title", "company_name", "generated_at", "template_version", "generator",
                                  "ai_model", "edited", "review_status", "approved_at")},
        validation_status=(d.get("validation") or {}).get("status", "not_validated"),
        exports=[ExportOut(**{k: e[k] for k in ExportOut.model_fields if k in e}) for e in d.get("exports", [])])  # file ids never leave the server


def _out(svc: DocumentService, d: dict) -> DocumentOut:
    v = d["validation"]
    return DocumentOut(**_summary(d).model_dump(), content=d["content"], notes=d.get("notes", []), profile_stale=svc.profile_stale(d),
                       validation=ValidationOut(status=v["status"], issues=v.get("issues", []), validated_at=v.get("validated_at"),
                                                claims_checked=v.get("claims_checked", 0)))


def _guard(fn):
    """Maps domain errors to clean HTTP errors (no internals)."""
    try:
        return fn()
    except (NoActiveResume, UnreviewedProfile) as exc:
        raise HTTPException(409, str(exc)) from None
    except (ProfileMissing, JobNotFound, MatchNotFound, DocumentNotFound):
        raise HTTPException(404, "Document, job, match or profile not found.") from None
    except NotApprovable as exc:
        raise HTTPException(409, {"message": str(exc), "issues": exc.issues}) from None
    except ExportRefused as exc:
        raise HTTPException(409, str(exc)) from None
    except ValidationError:
        raise HTTPException(422, "The document content does not have the expected structure.") from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


# ---- generate ---------------------------------------------------------------------------------------------
@router.post("/resume/generate", response_model=DocumentOut, status_code=201, summary="Generate a job-specific resume")
def generate_resume(body: ResumeGenerateRequest, svc: DocumentService = Depends(get_document_service)):
    d = _guard(lambda: svc.generate_resume(body.job_id, resume_id=body.resume_id, match_id=body.match_id, allow_unreviewed=body.allow_unreviewed,
                                           use_ai=body.use_ai, options=body.model_dump(include={"max_projects", "bullets_recent", "bullets_older"})))
    return _out(svc, d)


@router.post("/cover-letter/generate", response_model=DocumentOut, status_code=201, summary="Generate a cover letter")
def generate_cover_letter(body: CoverLetterGenerateRequest, svc: DocumentService = Depends(get_document_service)):
    d = _guard(lambda: svc.generate_cover_letter(body.job_id, resume_id=body.resume_id, match_id=body.match_id,
                                                 allow_unreviewed=body.allow_unreviewed, use_ai=body.use_ai))
    return _out(svc, d)


@router.post("/screening/generate", response_model=DocumentOut, status_code=201, summary="Generate screening answers")
def generate_screening(body: ScreeningGenerateRequest, svc: DocumentService = Depends(get_document_service)):
    d = _guard(lambda: svc.generate_screening(
        body.job_id, resume_id=body.resume_id, match_id=body.match_id, allow_unreviewed=body.allow_unreviewed, use_ai=body.use_ai,
        questions=[q.model_dump() for q in body.questions] if body.questions else None, length=body.length, candidate_inputs=body.candidate_inputs))
    return _out(svc, d)


# ---- read -------------------------------------------------------------------------------------------------
@router.get("/ai-status", summary="Whether Claude may word documents")
def ai_status_route(settings: Settings = Depends(get_settings)):
    return document_ai_status(settings)


@router.get("", response_model=list[DocumentSummary], summary="List generated documents")
def list_documents(
    document_type: Literal["resume", "cover_letter", "screening_answers"] | None = None, job_id: str | None = Query(None, pattern=HEX32),
    resume_id: str | None = Query(None, max_length=64), review_status: Literal["draft", "approved", "superseded"] | None = None,
    latest_only: bool = True, limit: int = Query(50, ge=1, le=200), skip: int = Query(0, ge=0),
    svc: DocumentService = Depends(get_document_service),
):
    return [_summary(d) for d in svc.list_documents(document_type=document_type, job_id=job_id, resume_id=resume_id, review_status=review_status,
                                          latest_only=latest_only, limit=limit, skip=skip)]


@router.get("/{document_id}", response_model=DocumentOut, summary="Document details")
def get_document(document_id: str = ID, svc: DocumentService = Depends(get_document_service)):
    return _out(svc, _guard(lambda: svc.get(document_id)))


@router.get("/{document_id}/versions", response_model=list[DocumentSummary], summary="All versions of this document")
def versions(document_id: str = ID, svc: DocumentService = Depends(get_document_service)):
    return [_summary(d) for d in _guard(lambda: svc.versions(document_id))]


@router.get("/{document_id}/compare/{other_id}", summary="Compare two versions of a document")
def compare(document_id: str = ID, other_id: str = Path(pattern=HEX32), svc: DocumentService = Depends(get_document_service)):
    return _guard(lambda: svc.compare(document_id, other_id))


# ---- change -----------------------------------------------------------------------------------------------
@router.put("/{document_id}", response_model=DocumentOut, summary="Save edited content as a new version")
def update_document(body: UpdateRequest, document_id: str = ID, svc: DocumentService = Depends(get_document_service),
                    settings: Settings = Depends(get_settings)):
    if len(str(body.content)) > settings.max_document_chars:
        raise HTTPException(413, "The document is too large.")
    return _out(svc, _guard(lambda: svc.update_content(document_id, body.content)))


@router.post("/{document_id}/validate", response_model=DocumentOut, summary="Validate factual accuracy")
def validate_document(document_id: str = ID, svc: DocumentService = Depends(get_document_service)):
    return _out(svc, _guard(lambda: svc.validate(document_id)))


@router.post("/{document_id}/approve", response_model=DocumentOut, summary="Approve this version (explicit candidate action)")
def approve_document(body: ApproveRequest, document_id: str = ID, svc: DocumentService = Depends(get_document_service)):
    return _out(svc, _guard(lambda: svc.approve(document_id, body.acknowledge_warnings)))


@router.post("/{document_id}/export", response_model=DocumentOut, summary="Export DOCX and/or PDF")
def export_document(body: ExportRequest, document_id: str = ID, svc: DocumentService = Depends(get_document_service)):
    return _out(svc, _guard(lambda: svc.export(document_id, body.formats)))


@router.get("/{document_id}/download/{fmt}", summary="Download the latest export")
def download(fmt: Literal["docx", "pdf"], document_id: str = ID, svc: DocumentService = Depends(get_document_service)):
    data, name, ctype = _guard(lambda: svc.download(document_id, fmt))
    return Response(content=data, media_type=ctype, headers={
        "Content-Disposition": f"attachment; filename=\"{name}\"; filename*=UTF-8''{urllib.parse.quote(name)}",
        "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})
