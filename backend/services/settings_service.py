"""Read and change configuration from the dashboard - validated before anything is written, secrets never shown.

* ``config/*.yaml`` values are changed in place (comments preserved) after the NEW content passes the same loader/validators the application uses.
  A scoring change bumps ``scoring_version``, keeps a backup of the previous file and reports before/after (CLAUDE.md rule); old matches are not reused.
* ``.env`` values are limited to an allow-list of NON-secret keys with validators. API keys, SECRET_KEY, ADMIN_TOKEN, database URI, OAuth credentials and the
  master automation switch are never read or written here (status shows only whether they are configured). Changes need an API restart.
* Platform permissions (config/platform_policies.yaml, application_policies.yaml) are shown read-only: enabling a platform is a deliberate edit of that file.
"""
import logging
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import ValidationError

from config.automation_config import ADJUSTABLE, AutomationLimits, effective, load_limits, parse_hhmm
from config.scoring_config import Preferences, load_preferences, load_scoring_config
from config.settings import PROJECT_ROOT, Settings
from config.yaml_edit import set_value
from database.repositories.scheduler_repository import SchedulerRepository
from integrations.application_policy import ApplicationPolicy
from integrations.career_pages.adapter import load_company_sources
from integrations.policy import PolicyRegistry
from notifications.email_notifications import EmailNotifier, mask_email

logger = logging.getLogger(__name__)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SettingsError(ValueError):
    """A submitted value is invalid. The message is user-safe and lists every problem."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


# ----------------------------------------------------------------------------------------------- env allow-list
def _bool(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    raise ValueError("must be true or false")


def _int(lo, hi):
    def f(v):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or int(v) != v or not lo <= v <= hi:
            raise ValueError(f"must be a whole number between {lo} and {hi}")
        return str(int(v))
    return f


def _tz(v):
    try:
        ZoneInfo(str(v))
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        raise ValueError("unknown time zone (use e.g. Asia/Kolkata)") from None
    return str(v)


def _email(v):
    if v in ("", None):
        return ""
    if not isinstance(v, str) or not _EMAIL.match(v.strip()) or len(v) > 200:
        raise ValueError("not a valid e-mail address")
    return v.strip()


def _choice(*opts):
    def f(v):
        if v not in opts:
            raise ValueError(f"must be one of: {', '.join(repr(o) for o in opts)}")
        return str(v)
    return f


ENV_FIELDS = {   # env var -> (settings attribute, validator)
    "SCHEDULER_TIMEZONE": ("scheduler_timezone", _tz), "SCHEDULER_AUTOSTART": ("scheduler_autostart", _bool),
    "NOTIFICATIONS_ENABLED": ("notifications_enabled", _bool), "NOTIFICATION_RECIPIENT": ("notification_recipient", _email),
    "BROWSER_HEADLESS": ("browser_headless", _bool), "BROWSER_CHANNEL": ("browser_channel", _choice("", "msedge", "chrome")),
    "BROWSER_TIMEOUT_MS": ("browser_timeout_ms", _int(1000, 120000)), "BROWSER_SLOW_MO_MS": ("browser_slow_mo_ms", _int(0, 2000)),
    "PERSIST_BROWSER_SESSIONS": ("persist_browser_sessions", _bool), "SESSION_MAX_AGE_HOURS": ("session_max_age_hours", _int(1, 720)),
    "MAX_SUBMIT_RETRIES": ("max_submit_retries", _int(0, 5)),
    "ENABLE_AI_MATCHING": ("enable_ai_matching", _bool), "AI_EXTRACTION_ENABLED": ("ai_extraction_enabled", _bool),
    "AI_CONSENT_EXTERNAL_PROCESSING": ("ai_consent_external_processing", _bool), "AI_DOCUMENTS_CONSENT": ("ai_documents_consent", _bool),
}
FORBIDDEN_ENV = {"ANTHROPIC_API_KEY", "GEMINI_API_KEY", "AI_PROVIDER","SECRET_KEY", "ADMIN_TOKEN", "MONGODB_URI", "APPLICATION_AUTOMATION_ENABLED", "ALLOW_LOCAL_TEST_FORMS", "NOTIFICATION_CREDENTIALS_FILE"}

AUTOMATION_RULES = {   # key (flat) -> (yaml path, validator, hard ceiling description)
    "max_applications_per_day": (("max_applications_per_day",), _int(0, 100)), "max_applications_per_platform_per_day": (("max_applications_per_platform_per_day",), _int(0, 50)),
    "daily_application_target": (("daily_application_target",), _int(0, 100)), "max_jobs_discovered_per_day": (("max_jobs_discovered_per_day",), _int(0, 2000)),
    "max_jobs_processed_per_day": (("max_jobs_processed_per_day",), _int(0, 500)), "max_retries_per_application": (("max_retries_per_application",), _int(0, 10)),
    "retry_backoff_seconds": (("retry_backoff_seconds",), _int(0, 3600)), "max_concurrent_workers": (("max_concurrent_workers",), _int(1, 4)),
    "min_match_score": (("min_match_score",), _int(0, 100)), "queue_lease_seconds": (("queue_lease_seconds",), _int(30, 86400)),
    "discovery_time": (("scheduler", "discovery_time"), None), "report_time": (("scheduler", "report_time"), None), "catchup_grace_minutes": (("scheduler", "catchup_grace_minutes"), _int(0, 1440)),
    "discovery_window": (("discovery_window",), None), "application_window": (("application_window",), None),
}


def _hhmm(v):
    try:
        parse_hhmm(v)
    except Exception:  # noqa: BLE001
        raise ValueError("must be HH:MM (24-hour)") from None
    return v


class SettingsService:
    def __init__(self, settings: Settings, db, *, env_path: Path | None = None, now_fn=lambda: datetime.now(timezone.utc)):
        self.s, self.db, self.env_path, self.now = settings, db, env_path or PROJECT_ROOT / ".env", now_fn

    # ------------------------------------------------------------------------------------------------- read
    def read(self) -> dict:
        s = self.s
        caps = load_limits(s)
        state = SchedulerRepository(self.db).get_state()
        prefs = load_preferences(s).model_dump()
        cfg = load_scoring_config(s)
        registry = PolicyRegistry.load(s)
        app_policy = ApplicationPolicy.load(s, registry)
        notifier = EmailNotifier(s, repo=None)
        return {
            "preferences": prefs,
            "scoring": {"scoring_version": cfg.scoring_version, "weights": cfg.weights, "thresholds": cfg.thresholds.model_dump(), "recommendation_threshold": cfg.recommendation_threshold,
                        "min_data_coverage": cfg.min_data_coverage, "missing_data_policy": cfg.missing_data_policy, "config_hash": cfg.config_hash},
            "automation": {"caps": caps.as_dict(), "overrides": state.get("limit_overrides", {}), "effective": effective(caps, state.get("limit_overrides")).as_dict(),
                           "adjustable_in_dashboard": sorted(ADJUSTABLE)},
            "scheduler": {"timezone": caps.timezone, "autostart": s.scheduler_autostart, "discovery_time": caps.discovery_time, "report_time": caps.report_time, "enabled": bool(state.get("enabled"))},
            "retry": {"max_submit_retries": s.max_submit_retries, "max_retries_per_application": caps.max_retries_per_application, "retry_backoff_seconds": caps.retry_backoff_seconds},
            "browser": {"headless": s.browser_headless, "channel": s.browser_channel, "timeout_ms": s.browser_timeout_ms, "slow_mo_ms": s.browser_slow_mo_ms,
                        "persist_sessions": s.persist_browser_sessions, "session_max_age_hours": s.session_max_age_hours},
            "notifications": {"enabled": s.notifications_enabled, "recipient": mask_email(s.notification_recipient) if s.notification_recipient else None,
                              "recipient_set": bool(_EMAIL.match(s.notification_recipient or "")), "credentials_stored": notifier.store.exists(), "configured": notifier.is_configured(),
                              "setup_command": "uv run python scripts/gmail_setup.py"},
            "ai": {"provider": s.ai_provider_name, "api_key_configured": bool(s.ai_api_key), "model": s.ai_model, "matching_enabled": s.enable_ai_matching,
                   "matching_consent": s.ai_consent_external_processing, "extraction_enabled": s.ai_extraction_enabled, "documents_consent": s.ai_documents_consent,
                   "active": bool(s.ai_api_key and s.enable_ai_matching and s.ai_consent_external_processing)},
            "sources": registry.describe(),
            "application_platforms": [{"platform": k, "status": (v or {}).get("application_status"), "basis": (v or {}).get("basis")} for k, v in app_policy._data.items()
                                      if isinstance(v, dict) and "application_status" in v] +
                                     [{"platform": f"career_pages/{p}", "status": v.get("application_status"), "basis": v.get("basis")}
                                      for p, v in ((app_policy._data.get("career_pages") or {}).get("providers") or {}).items()],
            "authorized_sites": app_policy._data.get("authorized_sites") or [],
            "companies": [c.model_dump(exclude_none=True) for c in load_company_sources(s)],
            "security": {"admin_token_configured": bool(s.admin_token), "secret_key_configured": len(s.secret_key or "") >= 16, "master_automation_switch": s.application_automation_enabled},
            "env_editable": sorted(ENV_FIELDS), "restart_note": "Changes to .env values take effect after the API is restarted.",
        }

    # ------------------------------------------------------------------------------------------------- preferences
    def update_preferences(self, values: dict) -> dict:
        path = self.s.config_path("job_preferences_file")
        current = load_preferences(self.s).model_dump()
        unknown = [k for k in values if k not in Preferences.model_fields]
        if unknown:
            raise SettingsError([f"unknown preference: {k}" for k in unknown])
        merged = {**current, **values}
        try:
            clean = Preferences.model_validate(merged).model_dump()
        except ValidationError as exc:
            raise SettingsError([f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]) from None
        problems = []
        if clean.get("experience_years_min") is not None and clean.get("experience_years_max") is not None and clean["experience_years_min"] > clean["experience_years_max"]:
            problems.append("experience_years_min cannot exceed experience_years_max")
        if clean.get("min_salary") is not None and clean["min_salary"] < 0:
            problems.append("min_salary cannot be negative")
        if clean.get("min_salary") and not clean.get("salary_currency"):
            problems.append("salary_currency is required when min_salary is set")
        if clean.get("posted_within_days") is not None and not 1 <= clean["posted_within_days"] <= 3650:
            problems.append("posted_within_days must be between 1 and 3650")
        if problems:
            raise SettingsError(problems)
        text = path.read_text(encoding="utf-8")
        changed = [k for k in values if clean[k] != current[k]]
        for k in changed:
            text = set_value(text, (k,), clean[k])
        if changed:
            self._verify_loads(text, lambda p: load_preferences(self.s, p))
            _atomic_write(path, text)
        return {"saved": changed, "preferences": load_preferences(self.s).model_dump()}

    # ------------------------------------------------------------------------------------------------- scoring
    def update_scoring(self, values: dict) -> dict:
        path = self.s.config_path("scoring_config_file")
        before_cfg = load_scoring_config(self.s)
        text = path.read_text(encoding="utf-8")
        problems: list[str] = []
        new_text = text
        before = {"weights": dict(before_cfg.weights), "thresholds": before_cfg.thresholds.model_dump(), "recommendation_threshold": before_cfg.recommendation_threshold,
                  "min_data_coverage": before_cfg.min_data_coverage, "missing_data_policy": before_cfg.missing_data_policy}
        try:
            for k, v in (values.get("weights") or {}).items():
                if k not in before_cfg.weights:
                    problems.append(f"unknown weight: {k}")
                else:
                    new_text = set_value(new_text, ("weights", k), v)
            for k, v in (values.get("thresholds") or {}).items():
                if k not in before["thresholds"]:
                    problems.append(f"unknown threshold: {k}")
                else:
                    new_text = set_value(new_text, ("thresholds", k), v)
            for k in ("recommendation_threshold", "min_data_coverage", "missing_data_policy"):
                if k in values:
                    new_text = set_value(new_text, (k,), values[k])
        except KeyError as exc:
            problems.append(f"{exc.args[0]} is missing from the configuration file")
        unknown = [k for k in values if k not in ("weights", "thresholds", "recommendation_threshold", "min_data_coverage", "missing_data_policy")]
        problems += [f"cannot change: {k}" for k in unknown]
        if problems:
            raise SettingsError(problems)
        try:
            cfg = self._verify_loads(new_text, lambda p: load_scoring_config(self.s, p))
        except SettingsError:
            raise
        t = cfg.thresholds
        if not (t.excellent > t.strong > t.good > t.moderate):
            raise SettingsError(["thresholds must satisfy excellent > strong > good > moderate"])
        after = {"weights": dict(cfg.weights), "thresholds": t.model_dump(), "recommendation_threshold": cfg.recommendation_threshold,
                 "min_data_coverage": cfg.min_data_coverage, "missing_data_policy": cfg.missing_data_policy}
        if after == before:
            return {"saved": False, "scoring_version": before_cfg.scoring_version, "before": before, "after": after, "note": "No change."}
        major, _, minor = before_cfg.scoring_version.partition(".")
        new_version = f"{major}.{int(minor or 0) + 1}" if minor.isdigit() or not minor else before_cfg.scoring_version + ".1"
        new_text = set_value(new_text, ("scoring_version",), new_version)
        backup_dir = path.parent / "backups"
        backup_dir.mkdir(exist_ok=True)
        stamp = self.now().strftime("%Y%m%dT%H%M%S")
        (backup_dir / f"{path.stem}_{before_cfg.scoring_version}_{stamp}{path.suffix}").write_text(text, encoding="utf-8")
        _atomic_write(path, new_text)
        return {"saved": True, "scoring_version": new_version, "before": before, "after": after,
                "note": "scoring_version was bumped and the previous file was backed up under config/backups/. Saved matches are not reused: re-run them to score with the new settings."}

    # ------------------------------------------------------------------------------------------------- automation caps
    def update_automation(self, values: dict) -> dict:
        path = self.s.config_path("automation_limits_file")
        text = path.read_text(encoding="utf-8")
        problems, writes = [], {}
        for k, v in values.items():
            if k not in AUTOMATION_RULES:
                problems.append(f"{k} cannot be changed here" + (" (the courtesy delay between actions is not adjustable)" if k == "delay_seconds_between_actions" else ""))
                continue
            ypath, validator = AUTOMATION_RULES[k]
            try:
                if k in ("discovery_window", "application_window"):
                    if not (isinstance(v, (list, tuple)) and len(v) == 2):
                        raise ValueError("must be [start, end] as HH:MM")
                    writes[ypath] = [_hhmm(v[0]), _hhmm(v[1])]
                elif validator is None:
                    writes[ypath] = _hhmm(v)
                else:
                    writes[ypath] = int(validator(v))
            except ValueError as exc:
                problems.append(f"{k}: {exc}")
        cur = load_limits(self.s)
        flat = {**{k: getattr(cur, k) for k in AUTOMATION_RULES if hasattr(cur, k)}, **{k: v for k, v in values.items() if k in AUTOMATION_RULES}}
        if flat["max_applications_per_platform_per_day"] > flat["max_applications_per_day"]:
            problems.append("max_applications_per_platform_per_day cannot exceed max_applications_per_day")
        if problems:
            raise SettingsError(problems)
        for ypath, v in writes.items():
            text = set_value(text, ypath, v)
        self._verify_loads(text, lambda p: load_limits(self.s.model_copy(update={"automation_limits_file": str(p)})))
        _atomic_write(path, text)
        return {"saved": sorted(values), "automation": load_limits(self.s).as_dict(), "note": "Scheduler times apply after the scheduler is restarted (done automatically if it was running)."}

    # ------------------------------------------------------------------------------------------------- env (non-secret)
    def update_env(self, values: dict) -> dict:
        problems, new = [], {}
        for k, v in values.items():
            if k in FORBIDDEN_ENV:
                problems.append(f"{k} is a secret or master switch and cannot be changed here")
            elif k not in ENV_FIELDS:
                problems.append(f"{k} is not an editable setting")
            else:
                try:
                    new[k] = ENV_FIELDS[k][1](v)
                except ValueError as exc:
                    problems.append(f"{k}: {exc}")
        if new.get("PERSIST_BROWSER_SESSIONS") == "true" and len(self.s.secret_key or "") < 16:
            problems.append("PERSIST_BROWSER_SESSIONS needs SECRET_KEY (16+ characters) in .env first")
        if new.get("NOTIFICATIONS_ENABLED") == "true" and not _EMAIL.match(new.get("NOTIFICATION_RECIPIENT", self.s.notification_recipient or "")):
            problems.append("set a valid NOTIFICATION_RECIPIENT before enabling notifications")
        if problems:
            raise SettingsError(problems)
        lines = self.env_path.read_text(encoding="utf-8").splitlines() if self.env_path.exists() else []
        done = set()
        for i, ln in enumerate(lines):
            m = re.match(r"^\s*([A-Z0-9_]+)\s*=", ln)
            if m and m.group(1) in new:
                lines[i] = f"{m.group(1)}={new[m.group(1)]}"
                done.add(m.group(1))
        lines += [f"{k}={v}" for k, v in new.items() if k not in done]
        _atomic_write(self.env_path, "\n".join(lines) + "\n")
        return {"saved": sorted(new), "restart_required": True, "note": "Restart the API (and Streamlit) for these changes to take effect."}

    # ------------------------------------------------------------------------------------------------- helper
    @staticmethod
    def _verify_loads(text: str, loader):
        """Parse the NEW content with the application's own loader before it replaces the real file."""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "candidate.yaml"
            p.write_text(text, encoding="utf-8")
            try:
                yaml.safe_load(text)
                return loader(p)
            except (ValidationError, yaml.YAMLError, ValueError, KeyError, TypeError) as exc:
                detail = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()] if isinstance(exc, ValidationError) else [str(exc)[:200]]
                raise SettingsError(detail) from None
