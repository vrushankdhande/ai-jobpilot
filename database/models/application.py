"""Pydantic models for applications (collection ``applications``) and their immutable event log (``application_events``).

An application is ONE (resume, job) pair - its ``idempotency_key`` is unique, so a job can never be applied to twice.
Field values come only from the verified profile or approved documents; unknown values stay ``None`` + ``needs_input``.
Events never contain personal data (ids, states, counts, reasons only).
"""
import hashlib
import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ai.schemas.candidate_profile import new_id, utcnow

Confidence = Literal["high", "medium", "low", "none"]   # rule-based match QUALITY (exact synonym / token overlap / guess) - not a probability
FieldStatus = Literal["ready", "needs_input", "manual_required", "optional_skipped", "filled"]
CheckStatus = Literal["pass", "fail", "warn"]


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore")


class FieldMapping(_Base):
    field_id: str                       # stable within a form snapshot (index based), never a CSS selector
    label: str = ""
    kind: str = "text"                  # text | textarea | select | radio | checkbox | file | email | tel | url | number | date
    required: bool = False
    canonical: str | None = None        # e.g. email, phone, linkedin_url, notice_period, screening_question
    value: Any = None                   # str | bool | None (never invented)
    source: str | None = None           # profile path / screening answer id / document id / "candidate_input"
    confidence: Confidence = "none"
    status: FieldStatus = "needs_input"
    sensitive: bool = False
    derived: bool = False               # e.g. first name split from the full name -> flagged for review
    options: list[str] = Field(default_factory=list)
    note: str | None = None


class UploadRecord(_Base):
    field_id: str | None = None
    role: Literal["resume", "cover_letter"]
    document_id: str
    document_version: int
    file_name: str
    sha256: str
    size_bytes: int = 0
    format: str = "pdf"
    verified: bool = False              # the browser confirmed the file is attached
    detail: str | None = None


class CheckResult(_Base):
    name: str
    status: CheckStatus
    critical: bool = True
    message: str = ""


class ApplicationValidation(_Base):
    can_submit: bool = False
    checks: list[CheckResult] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)       # labels the candidate still has to provide
    validated_at: datetime = Field(default_factory=utcnow)
    plan_hash: str | None = None

    @property
    def failed_critical(self) -> list[CheckResult]:
        return [c for c in self.checks if c.status == "fail" and c.critical]


class Approval(_Base):
    approved_at: datetime = Field(default_factory=utcnow)
    plan_hash: str                       # exactly what the candidate saw and approved
    acknowledged_warnings: bool = False
    approved_by: Literal["candidate"] = "candidate"


class SubmissionResult(_Base):
    outcome: Literal["confirmed", "unconfirmed", "failed", "not_attempted", "user_reported"] = "not_attempted"
    evidence: list[str] = Field(default_factory=list)       # e.g. "confirmation message", "reference number", "form no longer shown"
    reference: str | None = None
    detail: str | None = None
    at: datetime = Field(default_factory=utcnow)
    verified_by: Literal["page", "user", "email"] | None = None


class ApplicationRecord(_Base):
    application_id: str = Field(default_factory=new_id)
    idempotency_key: str
    job_id: str
    resume_id: str
    profile_id: str
    profile_version: int
    match_id: str | None = None
    job_title: str | None = None
    company_name: str | None = None
    source: str                          # job source (linkedin, career_pages, manual, local_test ...)
    provider: str | None = None          # ats provider
    apply_url: str | None = None
    mode: Literal["automated", "manual_handoff"] = "manual_handoff"
    policy: dict = Field(default_factory=dict)              # the ApplicationDecision that applied
    state: str = "DISCOVERED"
    document_ids: dict[str, str | None] = Field(default_factory=dict)   # resume / cover_letter / screening
    document_versions: dict[str, int | None] = Field(default_factory=dict)
    uploads: list[UploadRecord] = Field(default_factory=list)
    mapping: list[FieldMapping] = Field(default_factory=list)
    form_fingerprint: str | None = None
    form_summary: dict = Field(default_factory=dict)        # counts only: fields, required, uploads, ...
    validation: ApplicationValidation | None = None
    approval: Approval | None = None
    submission: SubmissionResult = Field(default_factory=SubmissionResult)
    session_meta: dict = Field(default_factory=dict)        # {"persistent": bool, "login_required": bool} - never credentials
    screenshots: list[str] = Field(default_factory=list)    # opaque screenshot ids (files live in storage/screenshots)
    manual: dict = Field(default_factory=dict)              # {"opened_at", "confirmed_at", "notes"}
    inputs: dict[str, Any] = Field(default_factory=dict)    # the candidate's own answers for THIS application, keyed by normalised field label
    plan_hash: str | None = None                            # hash of the values/files/form as last prepared (what approval binds to)
    allow_unreviewed: bool = False
    eligibility: str | None = None                          # PASS | FAIL | UNKNOWN at preparation time
    match_score: float | None = None
    ack_ineligible: bool = False                            # the candidate acknowledged a failed eligibility check
    ack_possible_duplicate: bool = False
    user_notes: str | None = None                           # the candidate's own notes (Phase 8); never used by automation
    dismissed_at: datetime | None = None                    # the candidate rejected this application before it was submitted
    dismissed_reason: str | None = None
    notes: list[str] = Field(default_factory=list)
    attempts: int = 0
    failure_stage: str | None = None                        # before_submit | after_submit | None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    submitted_at: datetime | None = None
    verified_at: datetime | None = None


def idempotency_key(resume_id: str, job_id: str) -> str:
    return hashlib.sha256(f"application|{resume_id}|{job_id}".encode()).hexdigest()[:40]


def plan_hash(mapping: list[FieldMapping | dict], uploads: list[UploadRecord | dict], job_id: str, fingerprint: str | None,
              apply_url: str | None = None) -> str:
    """Hash of everything the candidate approves: the values to enter, the files to attach, the form and the job."""
    def m(x):
        d = x.model_dump(mode="json") if isinstance(x, BaseModel) else x
        out = {k: d.get(k) for k in ("field_id", "canonical", "value", "status")}
        out["status"] = "ready" if out["status"] in ("ready", "filled") else out["status"]   # filled == ready for approval purposes
        return out

    def u(x):
        d = x.model_dump(mode="json") if isinstance(x, BaseModel) else x
        return {k: d.get(k) for k in ("role", "document_id", "document_version", "sha256")}

    payload = {"job": job_id, "url": apply_url, "form": fingerprint, "fields": sorted((m(x) for x in mapping), key=lambda d: d["field_id"]),
               "files": sorted((u(x) for x in uploads), key=lambda d: d["role"])}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
