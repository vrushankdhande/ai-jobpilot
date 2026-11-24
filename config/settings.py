"""Centralized application configuration (Pydantic Settings).

All configuration is read here from the environment / ``.env``. Other modules
must call :func:`get_settings` instead of using ``os.getenv`` directly.
"""
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_VERSION = "0.1.0"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @property
    def ai_api_key(self) -> str:
        """The key of the selected AI provider (empty = none configured)."""
        return self.gemini_api_key if self.ai_provider == "gemini" else self.anthropic_api_key

    @property
    def ai_model(self) -> str:
        return self.gemini_model if self.ai_provider == "gemini" else self.claude_model

    @property
    def ai_provider_name(self) -> str:
        return "Google Gemini" if self.ai_provider == "gemini" else "Anthropic (Claude)"

    app_name: str = "AI JobPilot"
    app_env: Literal["development", "staging", "production", "test"] = "development"
    debug: bool = True

    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)

    web_port: int = Field(default=8502, ge=1, le=65535)
    backend_base_url: str = "http://127.0.0.1:8000"

    mongodb_uri: str = Field(default="mongodb://localhost:27017/", repr=False)   # may contain credentials
    mongodb_database: str = Field(default="ai_jobpilot", min_length=1)

    log_level: str = "INFO"
    log_file: str = "logs/app.log"

    # Reserved for later phases (kept from the original settings stub).
    anthropic_api_key: str = Field(default="", repr=False)
    secret_key: str = Field(default="", repr=False)

    # Which AI provider the optional AI features use. Everything works without one (deterministic fallback).
    ai_provider: Literal["anthropic", "gemini"] = "anthropic"
    gemini_api_key: str = Field(default="", repr=False)
    gemini_model: str = "gemini-3.1-flash-lite"

    # --- Phase 2: resume intelligence ---
    resume_storage_dir: str = "storage/resumes"
    max_upload_mb: int = Field(default=10, ge=1, le=100)
    min_resume_text_chars: int = Field(default=100, ge=1)
    store_resume_text: bool = True
    # AI-assisted extraction is OFF unless explicitly enabled AND a key is set.
    # When on, resume text is sent to the Anthropic API.
    ai_extraction_enabled: bool = False
    ai_extraction_model: str = "claude-sonnet-5-5"

    # --- Phase 3: AI matching ---
    claude_model: str = "claude-sonnet-5-5"
    claude_max_tokens: int = Field(default=4096, ge=256, le=64000)
    claude_timeout: float = Field(default=60.0, ge=1, le=600)
    claude_max_retries: int = Field(default=3, ge=0, le=8)
    # Claude is used for matching only if ENABLE_AI_MATCHING, an API key AND explicit
    # consent to send job/profile text to the Anthropic API are all present.
    enable_ai_matching: bool = True
    ai_consent_external_processing: bool = False
    # Let the AI re-classify which job skills are mandatory/preferred (this CAN change scores and eligibility). Set false to keep the AI to
    # summary wording only: scores and eligibility then come from the deterministic rules alone.
    ai_refine_jd: bool = True
    scoring_config_file: str = "config/scoring_weights.yaml"
    job_preferences_file: str = "config/job_preferences.yaml"
    skills_file: str = "config/skills.yaml"
    # --- Phase 5: document generation ---
    generated_storage_dir: str = "storage/generated"
    max_document_chars: int = Field(default=200_000, ge=1000)
    pdf_page_size: str = "A4"  # A4 | LETTER
    # Claude may only WORD summaries/letters/answers from verified facts, and only with this explicit consent
    # (profile content - but never name/email/phone/links - is sent to the Anthropic API).
    ai_documents_consent: bool = False
    platform_policies_file: str = "config/platform_policies.yaml"
    career_sources_file: str = "config/career_sources.yaml"
    max_jd_chars: int = Field(default=50000, ge=1000)

    # --- Phase 6: application execution ---
    # Master switch: with False (default) NO browser automation of any application ever runs; everything is manual handoff.
    application_automation_enabled: bool = False
    # Lets the engine drive http://127.0.0.1 test forms (used by the automated tests only). Never applies to real hosts.
    allow_local_test_forms: bool = False
    application_policies_file: str = "config/application_policies.yaml"
    automation_limits_file: str = "config/automation_limits.yaml"
    browser_headless: bool = True
    browser_channel: str = ""  # "" = Playwright's bundled Chromium; "msedge" / "chrome" = the installed browser
    browser_timeout_ms: int = Field(default=15000, ge=1000, le=120000)
    browser_slow_mo_ms: int = Field(default=0, ge=0, le=2000)
    screenshots_dir: str = "storage/screenshots"
    browser_sessions_dir: str = "storage/browser_sessions"
    persist_browser_sessions: bool = False  # saved logins are encrypted with SECRET_KEY; off unless you opt in
    session_max_age_hours: int = Field(default=12, ge=1, le=720)
    max_submit_retries: int = Field(default=2, ge=0, le=5)  # only for transient failures BEFORE the submit click

    # --- Phase 7: scheduler, queue, notifications ---
    scheduler_timezone: str = "Asia/Kolkata"
    scheduler_autostart: bool = False          # the scheduler never starts on its own unless you opt in
    scheduler_tick_seconds: float = Field(default=30.0, ge=0.05, le=600)
    # Operational endpoints (start/stop/discover/queue changes/limits) need this token in the X-Admin-Token header.
    # Empty = those endpoints are refused (fail closed).
    admin_token: str = Field(default="", repr=False)
    notifications_enabled: bool = False
    notification_recipient: str = ""
    notification_credentials_file: str = "storage/notifications/gmail.cred"   # Fernet-encrypted with SECRET_KEY; never plain text
    # Job-alert e-mails (Naukri alerts in YOUR Gmail, read with the read-only Gmail API). Separate, Fernet-encrypted credentials.
    job_alert_credentials_file: str = "storage/notifications/gmail_job_alerts.cred"
    job_alert_lookback_days: int = Field(default=3, ge=1, le=30)
    gmail_sender: str = "me"

    @property
    def notification_credentials_path(self) -> Path:
        path = Path(self.notification_credentials_file)
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def job_alert_credentials_path(self) -> Path:
        path = Path(self.job_alert_credentials_file)
        return path if path.is_absolute() else PROJECT_ROOT / path

    def config_path(self, name: str) -> Path:
        path = Path(getattr(self, name))
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def generated_storage_path(self) -> Path:
        path = Path(self.generated_storage_dir)
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def screenshots_path(self) -> Path:
        path = Path(self.screenshots_dir)
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def sessions_path(self) -> Path:
        path = Path(self.browser_sessions_dir)
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def resume_storage_path(self) -> Path:
        path = Path(self.resume_storage_dir)
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def app_version(self) -> str:
        return APP_VERSION

    @property
    def log_path(self) -> Path:
        path = Path(self.log_file)
        return path if path.is_absolute() else PROJECT_ROOT / path

    @field_validator("mongodb_uri")
    @classmethod
    def _validate_mongodb_uri(cls, value: str) -> str:
        if not value.startswith(("mongodb://", "mongodb+srv://")):
            raise ValueError("MONGODB_URI must start with mongodb:// or mongodb+srv://")
        return value

    @field_validator("backend_base_url")
    @classmethod
    def _validate_backend_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("BACKEND_BASE_URL must start with http:// or https://")
        return value.rstrip("/")

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        level = value.upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR or CRITICAL")
        return level


@lru_cache
def get_settings() -> Settings:
    return Settings()
