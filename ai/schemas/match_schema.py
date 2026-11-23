"""Pydantic schemas for eligibility results and match reports."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai.schemas.candidate_profile import new_id, utcnow

CheckStatus = Literal["PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE"]
Classification = Literal["Excellent Match", "Strong Match", "Good Match", "Moderate Match", "Low Match"]
RecommendationCode = Literal[
    "RECOMMENDED", "NOT_ELIGIBLE", "ELIGIBILITY_UNRESOLVED", "BELOW_THRESHOLD", "INSUFFICIENT_DATA"
]


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore")


class EligibilityCheck(_Base):
    name: str
    status: CheckStatus
    hard: bool = True  # False = preference only (can warn, never rejects)
    requirement: str | None = None
    candidate_value: str | None = None
    detail: str = ""


class Eligibility(_Base):
    status: Literal["PASS", "FAIL", "UNKNOWN"]
    checks: list[EligibilityCheck] = Field(default_factory=list)
    failed_checks: list[str] = Field(default_factory=list)
    unresolved_checks: list[str] = Field(default_factory=list)  # UNKNOWN checks that block
    soft_warnings: list[str] = Field(default_factory=list)
    can_proceed: bool = False  # may an application proceed automatically?


class Evidence(_Base):
    jd: str | None = None
    candidate: str | None = None


class CategoryScore(_Base):
    name: str
    weight: float = Field(ge=0)
    earned_points: float = Field(ge=0)
    status: Literal["EVALUATED", "UNKNOWN"]
    ratio: float | None = Field(default=None, ge=0, le=1)
    notes: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def _bounds(self):
        if self.earned_points > self.weight + 1e-6:
            raise ValueError("earned_points cannot exceed the category weight")
        if self.status == "UNKNOWN" and self.earned_points != 0:
            raise ValueError("UNKNOWN categories earn no points")
        return self


class PartialMatch(_Base):
    candidate_skill: str
    required_skill: str
    credit: float = Field(ge=0, le=1)
    reason: str


class ProjectRelevance(_Base):
    project: str
    relevance: float = Field(ge=0, le=1)
    matched_skills: list[str] = Field(default_factory=list)


class RoleFitInfo(_Base):
    """How the job's function compares with the candidate's, and its effect on the score (engines/matching/role_fit.py)."""
    relation: Literal["same", "adjacent", "different", "unknown"] = "unknown"
    factor: float = 1.0
    job_family: str | None = None
    candidate_families: list[str] = Field(default_factory=list)
    score_before_role_fit: float | None = None
    note: str = ""


class MatchReport(_Base):
    match_id: str = Field(default_factory=new_id)
    job_id: str
    profile_id: str
    profile_version: int
    resume_id: str
    job_title: str | None = None
    company_name: str | None = None

    match_score: float = Field(ge=0, le=100)
    classification: Classification
    eligibility: Eligibility
    score_breakdown: dict[str, float | None]  # weighted points; None = UNKNOWN
    category_details: list[CategoryScore]
    data_coverage: float = Field(ge=0, le=100)  # % of the weighting that could be evaluated
    missing_data_policy: Literal["normalize", "zero"] = "normalize"
    role_fit: RoleFitInfo | None = None

    matched_skills: list[str] = Field(default_factory=list)
    missing_mandatory_skills: list[str] = Field(default_factory=list)
    missing_preferred_skills: list[str] = Field(default_factory=list)
    partial_matches: list[PartialMatch] = Field(default_factory=list)
    irrelevant_candidate_skills: list[str] = Field(default_factory=list)

    experience_analysis: list[str] = Field(default_factory=list)
    project_relevance: list[ProjectRelevance] = Field(default_factory=list)
    responsibility_analysis: list[str] = Field(default_factory=list)
    education_analysis: list[str] = Field(default_factory=list)
    location_analysis: list[str] = Field(default_factory=list)

    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    rejection_reasons: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    explanation: str = ""  # deterministic, always present
    ai_summary: str | None = None  # optional Claude wording; never used in scoring
    recommendation: str = ""
    recommendation_code: RecommendationCode

    scoring_version: str
    config_hash: str = ""
    jd_parser: Literal["deterministic", "claude+deterministic"] = "deterministic"
    ai_used: bool = False
    ai_model: str | None = None
    input_hash: str = ""
    created_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _score_consistent(self):
        total = sum(c.earned_points for c in self.category_details)
        evaluated = sum(c.weight for c in self.category_details if c.status == "EVALUATED")
        if self.missing_data_policy == "normalize":
            expected = (total / evaluated * 100) if evaluated > 0 else 0.0
        else:
            expected = total
        if self.role_fit is not None:                       # the role-fit factor is part of the documented score (never hidden)
            expected = expected * self.role_fit.factor
        if abs(expected - self.match_score) > 0.11:
            raise ValueError(f"match_score {self.match_score} does not equal its category breakdown ({expected:.2f})")
        weights = sum(c.weight for c in self.category_details)
        if weights and abs(evaluated / weights * 100 - self.data_coverage) > 0.11:
            raise ValueError("data_coverage does not match evaluated category weights")
        return self


class AIExplanation(_Base):
    """Optional natural-language wording requested from Claude (validated, never scored)."""

    summary: str = Field(max_length=2000)
