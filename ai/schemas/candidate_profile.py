"""Pydantic schemas for the structured candidate profile.

Design rules:
* Every field is optional / defaults to empty - missing data stays missing.
* Dates are stored as ``YYYY`` or ``YYYY-MM`` strings (or ``None``).
* ``provenance`` records where a value came from; ``review_flags`` and
  ``validation_warnings`` record what a human should double check.
"""
import re
import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
_DATE_RE = re.compile(r"^\d{4}(-(0[1-9]|1[0-2]))?$")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


def _clean_str(value):
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _check_date(value):
    value = _clean_str(value)
    if value is None:
        return None
    if not _DATE_RE.match(value):
        raise ValueError("date must be YYYY or YYYY-MM")
    return value


def _check_url(value):
    value = _clean_str(value)
    if value is None:
        return None
    if not re.match(r"^https?://[^\s/$.?#][^\s]*$", value, re.IGNORECASE):
        raise ValueError("URL must start with http:// or https://")
    return value


class _Base(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")


class ContactInformation(_Base):
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    portfolio_url: str | None = None

    @field_validator("full_name", "phone", "location", mode="before")
    @classmethod
    def _strip(cls, v):
        return _clean_str(v)

    @field_validator("email", mode="before")
    @classmethod
    def _email(cls, v):
        v = _clean_str(v)
        if v is not None and not _EMAIL_RE.match(v):
            raise ValueError("invalid email address")
        return v

    @field_validator("linkedin_url", "github_url", "portfolio_url", mode="before")
    @classmethod
    def _url(cls, v):
        return _check_url(v)


class ProfessionalSummary(_Base):
    professional_summary: str | None = None
    current_job_title: str | None = None
    total_experience_years: float | None = Field(default=None, ge=0, le=60)
    primary_domains: list[str] = Field(default_factory=list)
    career_objective: str | None = None

    @field_validator("professional_summary", "current_job_title", "career_objective", mode="before")
    @classmethod
    def _strip(cls, v):
        return _clean_str(v)


class TechnicalSkills(_Base):
    programming_languages: list[str] = Field(default_factory=list)
    machine_learning: list[str] = Field(default_factory=list)
    deep_learning: list[str] = Field(default_factory=list)
    generative_ai: list[str] = Field(default_factory=list)
    llm_frameworks: list[str] = Field(default_factory=list)
    libraries: list[str] = Field(default_factory=list)
    databases: list[str] = Field(default_factory=list)
    cloud_platforms: list[str] = Field(default_factory=list)
    developer_tools: list[str] = Field(default_factory=list)
    other_skills: list[str] = Field(default_factory=list)

    @field_validator("*", mode="before")
    @classmethod
    def _clean_list(cls, v):
        if v is None:
            return []
        return [s.strip() for s in v if isinstance(s, str) and s.strip()]

    def all_skills(self) -> list[tuple[str, str]]:
        return [(cat, s) for cat, items in self.model_dump().items() for s in items]


class WorkExperience(_Base):
    company_name: str | None = None
    job_title: str | None = None
    employment_type: str | None = None
    location: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    is_current: bool = False
    description: str | None = None
    responsibilities: list[str] = Field(default_factory=list)
    technologies_used: list[str] = Field(default_factory=list)
    achievements: list[str] = Field(default_factory=list)

    @field_validator("company_name", "job_title", "employment_type", "location", "description", mode="before")
    @classmethod
    def _strip(cls, v):
        return _clean_str(v)

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def _date(cls, v):
        return _check_date(v)


class Project(_Base):
    project_name: str | None = None
    description: str | None = None
    technologies: list[str] = Field(default_factory=list)
    role: str | None = None
    responsibilities: list[str] = Field(default_factory=list)
    achievements: list[str] = Field(default_factory=list)
    project_url: str | None = None

    @field_validator("project_name", "description", "role", mode="before")
    @classmethod
    def _strip(cls, v):
        return _clean_str(v)

    @field_validator("project_url", mode="before")
    @classmethod
    def _url(cls, v):
        return _check_url(v)


class Education(_Base):
    degree: str | None = None
    specialization: str | None = None
    institution: str | None = None
    location: str | None = None
    start_year: int | None = Field(default=None, ge=1950, le=2100)
    graduation_year: int | None = Field(default=None, ge=1950, le=2100)
    grade: str | None = None

    @field_validator("degree", "specialization", "institution", "location", "grade", mode="before")
    @classmethod
    def _strip(cls, v):
        return _clean_str(v)


class Certification(_Base):
    certification_name: str | None = None
    issuing_organization: str | None = None
    issue_date: str | None = None
    expiry_date: str | None = None
    credential_id: str | None = None
    credential_url: str | None = None

    @field_validator("certification_name", "issuing_organization", "credential_id", mode="before")
    @classmethod
    def _strip(cls, v):
        return _clean_str(v)

    @field_validator("issue_date", "expiry_date", mode="before")
    @classmethod
    def _date(cls, v):
        return _check_date(v)

    @field_validator("credential_url", mode="before")
    @classmethod
    def _url(cls, v):
        return _check_url(v)


class Provenance(_Base):
    source_section: str | None = None
    source_text: str | None = None
    # rule_based | ai | manual | calculated
    method: str = "rule_based"
    # extracted | inferred | edited
    kind: str = "extracted"


class ReviewFlag(_Base):
    field: str
    reason: str


class ValidationIssue(_Base):
    field: str
    code: str
    message: str
    severity: Literal["info", "warning", "error"] = "warning"


class ProfileContent(_Base):
    """The editable part of a profile (what the candidate can correct)."""

    contact: ContactInformation = Field(default_factory=ContactInformation)
    summary: ProfessionalSummary = Field(default_factory=ProfessionalSummary)
    technical_skills: TechnicalSkills = Field(default_factory=TechnicalSkills)
    work_experience: list[WorkExperience] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)
    achievements: list[str] = Field(default_factory=list)


class CandidateProfile(ProfileContent):
    profile_id: str = Field(default_factory=new_id)
    resume_id: str = Field(min_length=1)
    profile_version: int = Field(default=1, ge=1)
    parsing_status: Literal["completed", "completed_with_warnings", "failed"] = "completed"
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    extraction_method: Literal["rule_based", "rule_based+ai", "manual"] = "rule_based"
    review_status: Literal["pending_review", "reviewed"] = "pending_review"
    # Intentionally never auto-assigned: no artificial confidence numbers.
    confidence_score: float | None = None

    missing_fields: list[str] = Field(default_factory=list)
    review_flags: list[ReviewFlag] = Field(default_factory=list)
    validation_warnings: list[ValidationIssue] = Field(default_factory=list)
    provenance: dict[str, Provenance] = Field(default_factory=dict)

    def content(self) -> ProfileContent:
        return ProfileContent.model_validate(self.model_dump(include=set(ProfileContent.model_fields)))
