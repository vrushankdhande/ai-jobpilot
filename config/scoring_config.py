"""Typed loaders for the YAML configuration (scoring weights, thresholds, preferences)."""
import hashlib
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from config.yaml_cache import load_yaml_with_hash
from config.settings import Settings

CATEGORIES = ["technical_skills", "experience", "projects", "responsibilities", "education", "location", "seniority"]


class _Cfg(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Thresholds(_Cfg):
    excellent: float = 90
    strong: float = 80
    good: float = 75
    moderate: float = 60

    @model_validator(mode="after")
    def _ordered(self):
        if not (100 >= self.excellent > self.strong > self.good > self.moderate >= 0):
            raise ValueError("thresholds must satisfy 100 >= excellent > strong > good > moderate >= 0")
        return self


class SkillsCfg(_Cfg):
    mandatory_share: float = Field(0.8, ge=0, le=1)
    preferred_share: float = Field(0.2, ge=0, le=1)


class ExperienceCfg(_Cfg):
    years_fit_share: float = Field(0.5, ge=0, le=1)
    relevance_share: float = Field(0.5, ge=0, le=1)


class ProjectsCfg(_Cfg):
    top_n: int = Field(3, ge=1)
    outcome_bonus: float = Field(0.1, ge=0, le=1)
    skills_for_full_credit: int = Field(5, ge=1)


class ResponsibilitiesCfg(_Cfg):
    full_credit_overlap: float = Field(0.5, gt=0, le=1)
    no_credit_overlap: float = Field(0.15, ge=0, lt=1)


class EducationCfg(_Cfg):
    one_level_below_credit: float = 0.4
    two_levels_below_credit: float = 0.0
    preferred_unmet_credit: float = 0.75
    none_required_credit: float = 1.0


class LocationCfg(_Cfg):
    city_mismatch_open_to_relocation: float = 0.6
    city_mismatch_not_open: float = 0.0


class SeniorityCfg(_Cfg):
    bands: dict[str, float] = {"entry": 0, "junior": 1, "mid": 3, "senior": 5, "lead": 8, "principal": 12}
    credit_by_difference: dict[str, float] = {"0": 1.0, "1": 0.9, "2": 0.7, "-1": 0.5, "-2": 0.2}


class EligibilityCfg(_Cfg):
    blocking_unknown_checks: list[str] = ["min_experience", "mandatory_skills", "education", "certifications", "work_authorization"]
    experience_tolerance_years: float = 0.0
    mandatory_skills_fail_if_missing_fraction_above: float = Field(0.0, ge=0, le=1)
    # A mandatory skill counts as met when the profile has a documented near-equivalent (config/skills.yaml credit >= this),
    # e.g. CNN/RNN/Keras for 'Deep Learning'. Loose relatives (TensorFlow for PyTorch = 0.5) never satisfy it.
    mandatory_skill_equivalent_credit: float = Field(0.7, ge=0, le=1)
    allow_unknown_to_proceed: bool = False
    salary_is_hard: bool = False


class RoleFitCfg(_Cfg):
    """Function (engineering / sales / finance ...) match between the job title and the candidate's job titles. See engines/matching/role_fit.py."""
    enabled: bool = False
    same_factor: float = Field(1.0, ge=0, le=1)
    adjacent_factor: float = Field(0.8, ge=0, le=1)
    different_factor: float = Field(0.4, ge=0, le=1)
    families: dict[str, list[str]] = {}               # first matching family wins (order matters)
    adjacent_groups: list[list[str]] = []             # families inside one group count as adjacent


class ScoringConfig(_Cfg):
    scoring_version: str = "1.0"
    weights: dict[str, float]
    thresholds: Thresholds = Thresholds()
    recommendation_threshold: float = Field(75, ge=0, le=100)
    min_data_coverage: float = Field(60, ge=0, le=100)
    missing_data_policy: str = "normalize"
    skills: SkillsCfg = SkillsCfg()
    experience: ExperienceCfg = ExperienceCfg()
    projects: ProjectsCfg = ProjectsCfg()
    responsibilities: ResponsibilitiesCfg = ResponsibilitiesCfg()
    education: EducationCfg = EducationCfg()
    location: LocationCfg = LocationCfg()
    seniority: SeniorityCfg = SeniorityCfg()
    eligibility: EligibilityCfg = EligibilityCfg()
    role_fit: RoleFitCfg = RoleFitCfg()

    @property
    def config_hash(self) -> str:
        """Hash of the configuration CONTENT: any weight/threshold/credit/policy change changes it."""
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()[:16]

    @field_validator("weights")
    @classmethod
    def _weights(cls, w):
        if set(w) != set(CATEGORIES):
            raise ValueError(f"weights must define exactly: {', '.join(CATEGORIES)}")
        if any(v < 0 for v in w.values()):
            raise ValueError("weights must be non-negative")
        if abs(sum(w.values()) - 100) > 1e-6:
            raise ValueError(f"weights must total 100 (got {sum(w.values())})")
        return w

    @field_validator("missing_data_policy")
    @classmethod
    def _policy(cls, v):
        if v not in ("normalize", "zero"):
            raise ValueError("missing_data_policy must be 'normalize' or 'zero'")
        return v

    @model_validator(mode="after")
    def _shares(self):
        if abs(self.skills.mandatory_share + self.skills.preferred_share - 1) > 1e-6:
            raise ValueError("skills.mandatory_share + preferred_share must equal 1")
        if abs(self.experience.years_fit_share + self.experience.relevance_share - 1) > 1e-6:
            raise ValueError("experience shares must total 1")
        return self


class Preferences(_Cfg):
    titles: list[str] = []
    skills: list[str] = []
    companies: list[str] = []
    experience_years_min: float | None = None
    experience_years_max: float | None = None
    posted_within_days: int | None = None
    locations: list[str] = []
    remote_within_locations: bool = False   # true = a remote job that names a place must be in one of `locations` (e.g. not 'Remote - United States')
    remote: bool | None = None
    hybrid: bool | None = None
    onsite: bool | None = None
    open_to_relocation: bool | None = None
    notice_period: str | None = None
    employment_types: list[str] = []
    min_salary: float | None = None
    salary_currency: str | None = None
    work_authorization: list[str] = []
    keywords_include: list[str] = []
    keywords_exclude: list[str] = []


def _read(path: Path) -> tuple[dict, str]:
    data, digest = load_yaml_with_hash(path)      # cached by (path, mtime, size): re-parsed only when the file changes
    return (data or {}), digest


def load_scoring_config(settings: Settings | None = None, path: Path | None = None) -> ScoringConfig:
    path = path or (settings or Settings()).config_path("scoring_config_file")
    data, _ = _read(path)
    return ScoringConfig.model_validate(data)


def load_preferences(settings: Settings | None = None, path: Path | None = None) -> Preferences:
    path = path or (settings or Settings()).config_path("job_preferences_file")
    if not path.exists():
        return Preferences()
    data, _ = _read(path)
    return Preferences.model_validate(data)


def preferences_hash(prefs: Preferences) -> str:
    return hashlib.sha256(prefs.model_dump_json().encode()).hexdigest()[:16]
