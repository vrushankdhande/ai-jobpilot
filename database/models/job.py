"""Pydantic models for discovered jobs (collection ``jobs``).

Unknown values stay ``None``/empty - nothing is invented. Original source values are preserved on each
``SourceRef`` next to the normalised fields.
"""
import hashlib
import re
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ai.schemas.candidate_profile import new_id, utcnow
from integrations.base_adapter import public_http_url

JobStatus = Literal["active", "closed", "expired"]
WorkMode = Literal["remote", "hybrid", "onsite"]
EmploymentType = Literal["full-time", "part-time", "contract", "internship", "freelance", "temporary", "permanent"]


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def phase3_content_hash(title: str | None, company: str | None, location: str | None, description: str | None) -> str:
    """Same formula as the Phase 3 ``job_content_hash`` so a pasted copy of a discovered JD reuses its job id."""
    return hashlib.sha256("|".join([_norm(title), _norm(company), _norm(location), _norm(description)]).encode()).hexdigest()


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore")


class SourceRef(_Base):
    """One place where this vacancy was seen. A merged vacancy keeps all of them."""

    source: str
    provider: str | None = None
    source_job_id: str | None = None
    url: str | None = None
    canonical_url: str | None = None
    first_seen_at: datetime = Field(default_factory=utcnow)
    last_seen_at: datetime = Field(default_factory=utcnow)
    content_hash: str = ""  # hash of what THIS source showed last time (detects changes per source)
    original: dict = Field(default_factory=dict)  # untouched source values (title, company, location, ...)


class DedupInfo(_Base):
    status: Literal["unique", "merged", "possible_duplicate"] = "unique"
    confidence: float | None = Field(default=None, ge=0, le=1)
    matched_job_id: str | None = None
    reasons: list[str] = Field(default_factory=list)
    needs_review: bool = False


class JobRecord(_Base):
    job_id: str = Field(default_factory=new_id)
    source: str = Field(min_length=1)
    source_job_id: str | None = None
    job_title: str | None = None
    company_name: str | None = None
    job_description: str | None = None
    mandatory_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    experience_min: float | None = Field(default=None, ge=0, le=60)
    experience_max: float | None = Field(default=None, ge=0, le=60)
    location: list[str] = Field(default_factory=list)
    work_mode: WorkMode | None = None
    employment_type: EmploymentType | None = None
    salary_min: float | None = Field(default=None, ge=0)
    salary_max: float | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    job_url: str | None = None
    posted_date: datetime | None = None
    discovered_at: datetime = Field(default_factory=utcnow)
    last_updated_at: datetime = Field(default_factory=utcnow)
    status: JobStatus = "active"

    # --- bookkeeping (not part of the spec's field list, needed for dedup/traceability)
    source_refs: list[SourceRef] = Field(default_factory=list)
    title_key: str = ""
    company_key: str = ""
    location_keys: list[str] = Field(default_factory=list)
    canonical_url: str | None = None
    content_hash: str = ""
    dedup: DedupInfo = Field(default_factory=DedupInfo)
    warnings: list[str] = Field(default_factory=list)
    normalizer_version: str = ""

    @field_validator("job_url", "canonical_url")
    @classmethod
    def _public_url(cls, v):
        if v is None:
            return v
        if public_http_url(v) is None:
            raise ValueError("URL must be a public http(s) address")
        return v

    @field_validator("posted_date", "discovered_at", "last_updated_at")
    @classmethod
    def _aware(cls, v):
        if v is not None and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v

    @model_validator(mode="after")
    def _ranges(self):
        if self.salary_min is not None and self.salary_max is not None and self.salary_min > self.salary_max:
            raise ValueError("salary_min cannot exceed salary_max")
        if self.experience_min is not None and self.experience_max is not None and self.experience_min > self.experience_max:
            raise ValueError("experience_min cannot exceed experience_max")
        return self

    # ---- persistence helpers ----
    def location_string(self) -> str:
        return ", ".join(self.location)

    def compute_content_hash(self) -> str:
        return phase3_content_hash(self.job_title, self.company_name, self.location_string(), self.job_description)

    def to_document(self) -> dict:
        self.content_hash = self.content_hash or self.compute_content_hash()
        return self.model_dump(mode="python")

    @classmethod
    def from_document(cls, doc: dict) -> "JobRecord":
        """Tolerant loader: Phase 3 (pasted JD) documents lack the discovery fields."""
        d = dict(doc)
        d.setdefault("source", "manual_paste")
        d["source"] = d["source"] or "manual_paste"
        d.setdefault("job_description", d.get("jd_text"))
        if d.get("job_description") is None:
            d["job_description"] = d.get("jd_text")
        if isinstance(d.get("location"), str):
            d["location"] = [d["location"]] if d["location"] else []
        for list_field in ("location", "mandatory_skills", "preferred_skills", "source_refs", "location_keys", "warnings"):
            if d.get(list_field) is None:
                d.pop(list_field, None)  # Phase 3 documents store None where Phase 4 expects a list
        if d.get("discovered_at") is None:
            d["discovered_at"] = d.get("created_at") or utcnow()
        d.setdefault("last_updated_at", d["discovered_at"])
        if d.get("job_url") and public_http_url(d["job_url"]) is None:
            d["job_url"] = None
        for key in ("employment_type", "work_mode", "currency"):
            if d.get(key) in ("", None):
                d.pop(key, None)
        try:
            return cls.model_validate(d)
        except Exception:
            d.pop("employment_type", None)
            d.pop("salary", None)
            return cls.model_validate({k: v for k, v in d.items() if k in cls.model_fields and k not in ("employment_type",)})
