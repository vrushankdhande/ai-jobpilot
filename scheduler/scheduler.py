"""A small, dependency-free scheduler: daily jobs at a local time (default Asia/Kolkata) and interval jobs, with persistent state.

* Time zones: slots are computed in the configured zone (zoneinfo), compared as aware datetimes.
* Restart recovery: the last run of every job is stored in MongoDB. After a restart a daily job whose slot was missed is made up ONCE if it was due
  within ``catchup_grace`` minutes, otherwise it waits for the next slot (no burst of stale runs). Runs left 'running' by a crash are marked interrupted.
* No overlapping runs: every execution takes an atomic database lock (works across threads and processes); a second attempt is refused/skipped.
* Pause/stop: nothing runs while stopped or paused; manual runs (``run_now``) obey the same lock.
* ``tick(now)`` is a pure step (no sleeping) so tests drive the scheduler with a fake clock.
"""
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from database.repositories.scheduler_repository import SchedulerRepository

logger = logging.getLogger(__name__)
DEFAULT_TZ = "Asia/Kolkata"


class AlreadyRunning(Exception):
    pass


class UnknownJob(Exception):
    pass


@dataclass
class JobSpec:
    name: str
    fn: Callable[[str], dict]                  # fn(trigger) -> result summary (no personal data)
    at: str | None = None                      # "HH:MM" local time: a daily job
    every_seconds: float | None = None         # interval job (e.g. the application worker)
    lock_ttl: float = 3600
    gate: Callable[[datetime], str | None] | None = None   # returns a reason to SKIP this scheduled run (e.g. outside the time window)
    description: str = ""


def get_zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or DEFAULT_TZ)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        logger.error("Unknown time zone '%s'; using %s", name, DEFAULT_TZ)
        return ZoneInfo(DEFAULT_TZ)


def local_day(now: datetime, tz: ZoneInfo) -> str:
    return now.astimezone(tz).date().isoformat()


def _hhmm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def slot_at_or_before(at: str, now: datetime, tz: ZoneInfo) -> datetime:
    """The most recent scheduled instant <= now for a daily job at local ``at``."""
    local = now.astimezone(tz)
    slot = datetime.combine(local.date(), _hhmm(at), tzinfo=tz)
    return slot if slot <= local else slot - timedelta(days=1)


def next_slot(at: str, now: datetime, tz: ZoneInfo) -> datetime:
    prev = slot_at_or_before(at, now, tz)
    return datetime.combine((prev + timedelta(days=1)).date(), _hhmm(at), tzinfo=tz)


@dataclass
class Scheduler:
    repo: SchedulerRepository
    tz: ZoneInfo
    jobs: list[JobSpec] = field(default_factory=list)
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    catchup_grace_minutes: int = 240
    tick_seconds: float = 30.0
    owner: str = field(default_factory=lambda: uuid.uuid4().hex)
    _thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _stop: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _local: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    # ------------------------------------------------------------------------------------------------ lookup
    def job(self, name: str) -> JobSpec:
        for j in self.jobs:
            if j.name == name:
                return j
        raise UnknownJob(name)

    # ------------------------------------------------------------------------------------------------ due logic (pure)
    def is_due(self, spec: JobSpec, last_run: datetime | None, now: datetime) -> bool:
        if spec.every_seconds is not None:
            return last_run is None or (now - _aware(last_run)).total_seconds() >= spec.every_seconds
        if not spec.at:
            return False
        slot = slot_at_or_before(spec.at, now, self.tz)
        if last_run is not None and _aware(last_run) >= slot:
            return False                                                    # already ran for this slot
        return (now - slot) <= timedelta(minutes=self.catchup_grace_minutes)   # missed slots older than the grace are skipped, not replayed

    def next_run(self, spec: JobSpec, last_run: datetime | None, now: datetime) -> datetime | None:
        if spec.every_seconds is not None:
            return now if last_run is None else max(now, _aware(last_run) + timedelta(seconds=spec.every_seconds))
        if not spec.at:
            return None
        return now if self.is_due(spec, last_run, now) else next_slot(spec.at, now, self.tz)

    # ------------------------------------------------------------------------------------------------ execution
    def _execute(self, spec: JobSpec, trigger: str, now: datetime) -> dict:
        lock = f"job:{spec.name}"
        if not self.repo.acquire_lock(lock, self.owner, spec.lock_ttl, now):
            raise AlreadyRunning(f"{spec.name} is already running")
        run = self.repo.create_run(spec.name, trigger, now)
        try:
            result = spec.fn(trigger) or {}
            self.repo.finish_run(run["run_id"], "completed", result, None, self.now_fn())
            status, error = "completed", None
        except Exception as exc:  # noqa: BLE001 - a failing job must never kill the scheduler
            logger.error("Scheduled job %s failed: %s", spec.name, type(exc).__name__)
            self.repo.finish_run(run["run_id"], "failed", {}, f"{type(exc).__name__}", self.now_fn())
            status, error, result = "failed", type(exc).__name__, {}
        finally:
            self.repo.release_lock(lock, self.owner)
        if trigger == "scheduled":
            self.repo.mark_job_ran(spec.name, now)
        return {"run_id": run["run_id"], "job": spec.name, "trigger": trigger, "status": status, "error": error, "result": result}

    def run_now(self, name: str, trigger: str = "manual") -> dict:
        """Manual execution. Refused (AlreadyRunning) while the same job is running anywhere; ignores pause but never overlaps."""
        return self._execute(self.job(name), trigger, self.now_fn())

    def tick(self, now: datetime | None = None) -> list[dict]:
        """One scheduling step: run every job that is due (unless stopped/paused or gated). Returns what ran."""
        now = now or self.now_fn()
        state = self.repo.get_state()
        if not state.get("enabled") or state.get("paused"):
            return []
        ran = []
        for spec in self.jobs:
            last = (state.get("last_runs") or {}).get(spec.name)
            if not self.is_due(spec, last, now):
                continue
            reason = spec.gate(now) if spec.gate else None
            if reason:
                logger.info("Scheduled job %s skipped: %s", spec.name, reason)
                continue
            try:
                ran.append(self._execute(spec, "scheduled", now))
            except AlreadyRunning:
                logger.info("Scheduled job %s skipped: still running", spec.name)
        return ran

    # ------------------------------------------------------------------------------------------------ lifecycle
    def start(self, *, background: bool = True) -> dict:
        """Persist ``enabled`` (so a restart resumes it), mark crash-orphaned runs and begin ticking."""
        self.repo.set_state({"enabled": True, "started_at": self.now_fn()})
        longest = max([j.lock_ttl for j in self.jobs] + [60])
        self.repo.close_orphaned_runs(self.now_fn() - timedelta(seconds=longest))
        if background and not (self._thread and self._thread.is_alive()):
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="jobpilot-scheduler", daemon=True)
            self._thread.start()
        return self.status()

    def stop(self) -> dict:
        self.repo.set_state({"enabled": False})
        self._stop.set()
        t = self._thread
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=5)
        self._thread = None
        return self.status()

    def shutdown(self) -> None:
        """Process is ending: stop the thread but keep the persisted ``enabled`` flag so the next start resumes."""
        self._stop.set()
        t = self._thread
        if t and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=5)
        self._thread = None

    def pause(self) -> dict:
        self.repo.set_state({"paused": True})
        return self.status()

    def resume(self) -> dict:
        self.repo.set_state({"paused": False})
        return self.status()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001
                logger.error("Scheduler tick failed: %s", type(exc).__name__)
            self._stop.wait(self.tick_seconds)

    @property
    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def status(self) -> dict:
        now = self.now_fn()
        state = self.repo.get_state()
        jobs = []
        for spec in self.jobs:
            last = (state.get("last_runs") or {}).get(spec.name)
            nxt = self.next_run(spec, last, now)
            latest = self.repo.latest_run(spec.name)
            jobs.append({"name": spec.name, "description": spec.description, "schedule": spec.at and f"daily at {spec.at} {self.tz.key}" or f"every {spec.every_seconds:g}s",
                         "last_run_at": last, "next_run_at": nxt, "next_run_local": nxt.astimezone(self.tz).isoformat() if nxt else None,
                         "running": self.repo.lock_holder(f"job:{spec.name}", now) is not None,
                         "last_status": (latest or {}).get("status"), "last_error": (latest or {}).get("error")})
        return {"enabled": bool(state.get("enabled")), "paused": bool(state.get("paused")), "thread_alive": self.alive, "timezone": self.tz.key,
                "now_local": now.astimezone(self.tz).isoformat(), "jobs": jobs}


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
