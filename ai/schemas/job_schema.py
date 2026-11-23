"""Pydantic schemas for job-description input and parsed analysis."""
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Importance = Literal["mandatory", "preferred", "mentioned", "example"]
SKILL_CATEGORIES = [
    "programming_languages", "frameworks", "libraries", "databases",
    "cloud_platforms", "tools", "domain_knowledge",
]
SENIORITY_LEVELS = ["entry", "junior", "mid", "senior", "lead", "principal"]
JD_PARSER_VERSION = "1.2"


class _Base(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")


def _blank_to_none(v):
    if v is None:
        return None
    v = str(v).strip()
    return v or None


class JobInput(_Base):
    """What the user supplies for one job."""

    job_description: str = Field(min_length=10, max_length=200000)
    job_title: str | None = Field(default=None, max_length=200)
    company_name: str | None = Field(default=None, max_length=200)
    location: str | None = Field(default=None, max_length=300)
    experience_requirement: str | None = Field(default=None, max_length=200)
    employment_type: str | None = Field(default=None, max_length=100)
    salary: str | None = Field(default=None, max_length=200)
    job_url: str | None = Field(default=None, max_length=2000)

    @field_validator("job_title", "company_name", "location", "experience_requirement",
                     "employment_type", "salary", mode="before")
    @classmethod
    def _blank(cls, v):
        return _blank_to_none(v)

    @field_validator("job_url", mode="before")
    @classmethod
    def _url(cls, v):
        v = _blank_to_none(v)
        if v is not None and not re.match(r"^https?://[^\s]+$", v, re.IGNORECASE):
            raise ValueError("job_url must start with http:// or https://")
        return v

    @field_validator("job_description")
    @classmethod
    def _has_text(cls, v):
        if len(re.findall(r"[A-Za-z]{2,}", v)) < 2:
            raise ValueError("job_description does not look like text")
        return v


class SkillRequirement(_Base):
    name: str  # canonical skill name
    category: str = "tools"
    importance: Importance = "mentioned"
    original_terms: list[str] = Field(default_factory=list)
    evidence: str | None = None  # JD snippet this classification is based on
    group: int | None = None  # skills sharing a group are alternatives ("PyTorch or TensorFlow")


class EducationRequirement(_Base):
    level: str | None = None  # diploma | bachelor | master | phd
    level_rank: int | None = None
    field: str | None = None
    mandatory: bool = True
    equivalent_experience_accepted: bool = False
    text: str = ""


class CertificationRequirement(_Base):
    name: str
    mandatory: bool = True
    text: str = ""


class SkillExperience(_Base):
    skill: str
    years: float


class JobAnalysis(_Base):
    # --- job information
    job_title: str | None = None
    company_name: str | None = None
    location: list[str] = Field(default_factory=list)
    employment_type: str | None = None
    work_mode: Literal["remote", "hybrid", "onsite"] | None = None
    minimum_experience_years: float | None = Field(default=None, ge=0, le=60)
    maximum_experience_years: float | None = Field(default=None, ge=0, le=60)
    maximum_is_restrictive: bool = False
    minimum_salary: float | None = None
    maximum_salary: float | None = None
    currency: str | None = None
    education_requirements: list[EducationRequirement] = Field(default_factory=list)
    certification_requirements: list[CertificationRequirement] = Field(default_factory=list)

    # --- technical requirements (single source of truth = skills)
    skills: list[SkillRequirement] = Field(default_factory=list)
    # derived views, filled by finalize()
    mandatory_skills: list[str] = Field(default_factory=list)
    mandatory_groups: list[list[str]] = Field(default_factory=list)  # any-of groups, one per requirement
    preferred_skills: list[str] = Field(default_factory=list)
    mentioned_skills: list[str] = Field(default_factory=list)
    example_skills: list[str] = Field(default_factory=list)
    programming_languages: list[str] = Field(default_factory=list)
    frameworks: list[str] = Field(default_factory=list)
    libraries: list[str] = Field(default_factory=list)
    databases: list[str] = Field(default_factory=list)
    cloud_platforms: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    domain_knowledge: list[str] = Field(default_factory=list)

    # --- responsibilities / experience
    responsibilities: list[str] = Field(default_factory=list)
    required_experience: list[str] = Field(default_factory=list)
    preferred_experience: list[str] = Field(default_factory=list)
    skill_experience: list[SkillExperience] = Field(default_factory=list)
    seniority_level: str | None = None
    seniority_source: Literal["title", "experience_requirement"] | None = None
    work_authorization_requirement: str | None = None
    other_requirements: list[str] = Field(default_factory=list)

    # --- traceability
    parser: Literal["deterministic", "claude+deterministic"] = "deterministic"
    parser_version: str = JD_PARSER_VERSION
    warnings: list[str] = Field(default_factory=list)
    jd_text_sha256: str | None = None

    def finalize(self) -> "JobAnalysis":
        """Rebuild the derived lists from ``skills`` (call after editing skills)."""
        by = {"mandatory": [], "preferred": [], "mentioned": [], "example": []}
        groups: dict[int, list[str]] = {}
        singles: list[list[str]] = []
        for s in self.skills:
            if s.name not in by[s.importance]:
                by[s.importance].append(s.name)
            if s.importance == "mandatory":
                if s.group is None:
                    singles.append([s.name])
                else:
                    groups.setdefault(s.group, []).append(s.name)
        self.mandatory_skills = by["mandatory"]
        self.preferred_skills = by["preferred"]
        self.mentioned_skills = by["mentioned"]
        self.example_skills = by["example"]
        self.mandatory_groups = singles + list(groups.values())
        for cat in SKILL_CATEGORIES:
            names = []
            for s in self.skills:
                if s.category == cat and s.importance in ("mandatory", "preferred", "mentioned") and s.name not in names:
                    names.append(s.name)
            setattr(self, cat, names)
        return self

    @property
    def all_scored_skills(self) -> list[str]:
        return self.mandatory_skills + [s for s in self.preferred_skills if s not in self.mandatory_skills]


# ---- what Claude is asked to return for JD extraction (validated, then grounded) ----------
class AISkill(_Base):
    name: str
    importance: Importance
    evidence: str | None = None


class AIJobExtraction(_Base):
    skills: list[AISkill] = Field(default_factory=list)
    minimum_experience_years: float | None = Field(default=None, ge=0, le=60)
    maximum_experience_years: float | None = Field(default=None, ge=0, le=60)
    responsibilities: list[str] = Field(default_factory=list)
    seniority_level: str | None = None
