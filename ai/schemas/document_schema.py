"""Schemas for generated documents (resume, cover letter, screening answers) and their version metadata.

Every text element can carry ``source`` evidence: a path into the verified candidate profile (for example
``work_experience[0].achievements[1]``). Content that the candidate edits keeps its shape, so it can be re-validated.
"""
import hashlib
import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ai.schemas.candidate_profile import ValidationIssue, new_id, utcnow

TEMPLATE_VERSION = "1.0"
DocType = Literal["resume", "cover_letter", "screening_answers"]
DOC_TYPES = ("resume", "cover_letter", "screening_answers")
MAX_TEXT = 4000
PLACEHOLDER = "[CANDIDATE INPUT REQUIRED"


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


def _clean(v):
    return v.strip() if isinstance(v, str) else v


# ---------------------------------------------------------------------------------------- resume
class Bullet(_Base):
    text: str = Field(max_length=MAX_TEXT)
    source: str | None = None  # profile path the text was taken from


class SkillGroup(_Base):
    label: str = Field(max_length=100)
    items: list[str] = Field(default_factory=list, max_length=60)
    source: str | None = None

    @field_validator("items")
    @classmethod
    def _items(cls, v):
        return [s.strip() for s in v if isinstance(s, str) and s.strip()]


class ResumeHeader(_Base):
    full_name: str | None = None
    headline: str | None = None
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    portfolio_url: str | None = None


class ExperienceItem(_Base):
    company_name: str | None = None
    job_title: str | None = None
    location: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    is_current: bool = False
    bullets: list[Bullet] = Field(default_factory=list, max_length=30)
    source: str | None = None  # work_experience[i]


class ProjectItem(_Base):
    project_name: str | None = None
    technologies: list[str] = Field(default_factory=list, max_length=40)
    project_url: str | None = None
    bullets: list[Bullet] = Field(default_factory=list, max_length=20)
    source: str | None = None


class EducationItem(_Base):
    degree: str | None = None
    specialization: str | None = None
    institution: str | None = None
    start_year: int | None = None
    graduation_year: int | None = None
    grade: str | None = None
    source: str | None = None


class CertificationItem(_Base):
    certification_name: str | None = None
    issuing_organization: str | None = None
    issue_date: str | None = None
    source: str | None = None


class ResumeContent(_Base):
    header: ResumeHeader = Field(default_factory=ResumeHeader)
    summary: str = Field(default="", max_length=MAX_TEXT)
    summary_sources: list[str] = Field(default_factory=list)
    skills: list[SkillGroup] = Field(default_factory=list, max_length=20)
    experience: list[ExperienceItem] = Field(default_factory=list, max_length=30)
    projects: list[ProjectItem] = Field(default_factory=list, max_length=20)
    education: list[EducationItem] = Field(default_factory=list, max_length=15)
    certifications: list[CertificationItem] = Field(default_factory=list, max_length=30)
    achievements: list[Bullet] = Field(default_factory=list, max_length=20)
    notes: list[str] = Field(default_factory=list)  # how it was customised (not printed)


# ---------------------------------------------------------------------------------- cover letter
class LetterParagraph(_Base):
    text: str = Field(max_length=MAX_TEXT)
    sources: list[str] = Field(default_factory=list)


class CoverLetterContent(_Base):
    salutation: str = "Dear Hiring Manager,"
    paragraphs: list[LetterParagraph] = Field(default_factory=list, max_length=12)
    closing: str = "Sincerely,"
    signature: str | None = None
    company_name: str | None = None
    job_title: str | None = None
    missing: list[str] = Field(default_factory=list)  # information the candidate still has to supply


# ---------------------------------------------------------------------------- screening answers
AnswerStatus = Literal["answered", "draft", "needs_input"]
AnswerLength = Literal["short", "medium", "long"]


class ScreeningAnswer(_Base):
    question_id: str = Field(max_length=60)
    question: str = Field(max_length=500)
    answer: str = Field(default="", max_length=MAX_TEXT)
    status: AnswerStatus = "answered"
    length: AnswerLength = "medium"
    sources: list[str] = Field(default_factory=list)
    note: str | None = None  # why it needs input / review


class ScreeningContent(_Base):
    answers: list[ScreeningAnswer] = Field(default_factory=list, max_length=40)
    candidate_inputs: dict[str, str] = Field(default_factory=dict)  # values the candidate typed in (kept for validation)

    @field_validator("candidate_inputs")
    @classmethod
    def _inputs(cls, v):
        return {str(k)[:40]: str(val)[:300] for k, val in (v or {}).items() if str(val).strip()}


CONTENT_MODELS = {"resume": ResumeContent, "cover_letter": CoverLetterContent, "screening_answers": ScreeningContent}


def parse_content(doc_type: str, data: dict):
    return CONTENT_MODELS[doc_type].model_validate(data)


def content_hash(content: dict) -> str:
    return hashlib.sha256(json.dumps(content, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


# -------------------------------------------------------------------------- document metadata
ValidationStatus = Literal["not_validated", "passed", "passed_with_warnings", "failed"]
ReviewStatus = Literal["draft", "approved", "superseded"]


class DocumentValidation(_Base):
    status: ValidationStatus = "not_validated"
    issues: list[ValidationIssue] = Field(default_factory=list)
    validated_at: datetime | None = None
    content_hash: str | None = None  # hash of the content that was validated
    claims_checked: int = 0

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "warning"]


class ExportRef(_Base):
    format: Literal["docx", "pdf"]
    file_id: str  # server-side stored filename (never returned to clients as a path)
    download_name: str
    created_at: datetime = Field(default_factory=utcnow)
    sha256: str
    size_bytes: int
    from_approved: bool = False
    warnings: list[str] = Field(default_factory=list)


class GeneratedDocument(_Base):
    document_id: str = Field(default_factory=new_id)
    group_id: str  # all versions of "this type for this job and candidate" share it
    document_type: DocType
    version: int = Field(default=1, ge=1)
    parent_version_id: str | None = None

    profile_id: str
    profile_version: int
    resume_id: str
    job_id: str
    match_id: str | None = None
    job_title: str | None = None
    company_name: str | None = None

    generated_at: datetime = Field(default_factory=utcnow)
    template_version: str = TEMPLATE_VERSION
    generator: Literal["deterministic", "claude+deterministic", "manual_edit"] = "deterministic"
    ai_model: str | None = None
    notes: list[str] = Field(default_factory=list)

    content: dict
    edited: bool = False
    validation: DocumentValidation = Field(default_factory=DocumentValidation)
    review_status: ReviewStatus = "draft"
    approved_at: datetime | None = None
    exports: list[ExportRef] = Field(default_factory=list)
