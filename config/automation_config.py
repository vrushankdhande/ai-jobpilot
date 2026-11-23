"""Automation limits (config/automation_limits.yaml). The YAML values are hard caps; the dashboard can only lower them (see effective())."""
import logging
from dataclasses import asdict, dataclass, field, replace
from datetime import time

import yaml

from config.settings import Settings
from config.yaml_cache import load_yaml

logger = logging.getLogger(__name__)

# limits the dashboard may lower: name -> minimum allowed (0 = can be switched off)
ADJUSTABLE = {"max_applications_per_day": 0, "max_applications_per_platform_per_day": 0, "max_jobs_discovered_per_day": 0, "max_jobs_processed_per_day": 0,
              "max_retries_per_application": 0, "max_concurrent_workers": 1, "min_match_score": 0}
HIGHER_IS_STRICTER = {"min_match_score"}   # for these a larger override is the safer direction (and cannot go below the YAML value)


@dataclass(frozen=True)
class AutomationLimits:
    max_applications_per_day: int = 20
    max_applications_per_platform_per_day: int = 10
    delay_seconds_between_actions: float = 3.0   # a fixed, minimum pause - courtesy rate limiting, NOT randomised "human-like" timing
    daily_application_target: int = 25
    max_jobs_discovered_per_day: int = 300
    max_jobs_processed_per_day: int = 60
    max_retries_per_application: int = 3
    retry_backoff_seconds: float = 60.0
    max_concurrent_workers: int = 1
    min_match_score: float = 75.0
    queue_lease_seconds: int = 900
    discovery_window: tuple[str, str] = ("06:00", "22:00")
    application_window: tuple[str, str] = ("09:00", "21:00")
    timezone: str = "Asia/Kolkata"
    discovery_time: str = "07:00"
    report_time: str = "20:00"
    catchup_grace_minutes: int = 240

    def as_dict(self) -> dict:
        return asdict(self)


def parse_hhmm(value: str) -> time:
    h, m = str(value).split(":")
    t = time(int(h), int(m))
    return t


def in_window(window: tuple[str, str], local_time: time) -> bool:
    start, end = parse_hhmm(window[0]), parse_hhmm(window[1])
    return start <= local_time <= end if start <= end else (local_time >= start or local_time <= end)


def load_limits(settings: Settings) -> AutomationLimits:
    try:
        data = load_yaml(settings.config_path("automation_limits_file")) or {}
        sched = data.get("scheduler") or {}
        delay = data.get("delay_seconds_between_actions", [3, 8])
        lo = float(delay[0] if isinstance(delay, (list, tuple)) and delay else delay)
        base = AutomationLimits()
        win = lambda k, d: tuple(str(x) for x in (data.get(k) or d))[:2]  # noqa: E731
        lim = AutomationLimits(
            max_applications_per_day=max(0, int(data.get("max_applications_per_day", base.max_applications_per_day))),
            max_applications_per_platform_per_day=max(0, int(data.get("max_applications_per_platform_per_day", base.max_applications_per_platform_per_day))),
            delay_seconds_between_actions=max(0.0, lo), daily_application_target=max(0, int(data.get("daily_application_target", base.daily_application_target))),
            max_jobs_discovered_per_day=max(0, int(data.get("max_jobs_discovered_per_day", base.max_jobs_discovered_per_day))),
            max_jobs_processed_per_day=max(0, int(data.get("max_jobs_processed_per_day", base.max_jobs_processed_per_day))),
            max_retries_per_application=max(0, int(data.get("max_retries_per_application", base.max_retries_per_application))),
            retry_backoff_seconds=max(0.0, float(data.get("retry_backoff_seconds", base.retry_backoff_seconds))),
            max_concurrent_workers=max(1, int(data.get("max_concurrent_workers", base.max_concurrent_workers))),
            min_match_score=min(100.0, max(0.0, float(data.get("min_match_score", base.min_match_score)))),
            queue_lease_seconds=max(30, int(data.get("queue_lease_seconds", base.queue_lease_seconds))),
            discovery_window=win("discovery_window", base.discovery_window), application_window=win("application_window", base.application_window),
            timezone=settings.scheduler_timezone or str(sched.get("timezone") or base.timezone), discovery_time=str(sched.get("discovery_time", base.discovery_time)),
            report_time=str(sched.get("report_time", base.report_time)), catchup_grace_minutes=max(0, int(sched.get("catchup_grace_minutes", base.catchup_grace_minutes))))
        for t in (*lim.discovery_window, *lim.application_window, lim.discovery_time, lim.report_time):
            parse_hhmm(t)
        return lim
    except Exception as exc:  # noqa: BLE001 - unreadable/invalid -> conservative defaults
        logger.error("automation_limits unreadable (%s); using defaults", type(exc).__name__)
        return AutomationLimits()


def effective(caps: AutomationLimits, overrides: dict | None) -> AutomationLimits:
    """Caps from YAML, lowered (never raised) by dashboard overrides. min_match_score can only be raised."""
    changes = {}
    for k, v in (overrides or {}).items():
        if k not in ADJUSTABLE or isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        cap = getattr(caps, k)
        v = max(ADJUSTABLE[k], v)
        changes[k] = max(v, cap) if k in HIGHER_IS_STRICTER else min(v, cap)
        if isinstance(cap, int) and not isinstance(cap, bool):
            changes[k] = int(changes[k])
    return replace(caps, **changes)
