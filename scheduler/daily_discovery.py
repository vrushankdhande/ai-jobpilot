"""Daily discovery: permitted sources only -> normalise + deduplicate (Phase 4 engine) -> match (Phase 3) -> queue eligible jobs (never apply).

* Sources come from the Phase 4 policy registry: a source that does not permit automated discovery is skipped, exactly as in the manual flow.
* Your preferences (config/job_preferences.yaml) define the query. Daily discovered-jobs cap is applied to the query and tracked atomically.
* Overlap is prevented twice: by the scheduler's lock and by the discovery-run guard (a second run reports 'busy').
* Matching deterministic; jobs below ``min_match_score`` or failing eligibility are not queued.
"""
import logging
from datetime import datetime, timezone
from typing import Callable

from backend.services.discovery_service import DiscoveryBusy, DiscoveryService
from backend.services.matching_service import MatchingService, NoActiveResume, ProfileMissing, UnreviewedProfile
from config.automation_config import AutomationLimits
from database.repositories.scheduler_repository import SchedulerRepository
from scheduler.application_queue import ApplicationQueue, NotQueueable
from scheduler.scheduler import get_zone, local_day

logger = logging.getLogger(__name__)


class DailyDiscovery:
    def __init__(self, discovery: DiscoveryService, matching: MatchingService, queue: ApplicationQueue, sched: SchedulerRepository,
                 limits_fn: Callable[[], AutomationLimits], tz_name: str, *, notifier=None, now_fn=lambda: datetime.now(timezone.utc)):
        self.discovery, self.matching, self.queue, self.sched, self.limits_fn, self.notifier, self.now = discovery, matching, queue, sched, limits_fn, notifier, now_fn
        self.tz = get_zone(tz_name)

    def run(self, trigger: str = "scheduled") -> dict:
        now, lim = self.now(), self.limits_fn()
        day = local_day(now, self.tz)
        out: dict = {"trigger": trigger, "day": day, "status": "completed", "new": 0, "duplicates": 0, "fetched": 0, "matched": 0, "queued": 0, "not_queued": 0,
                     "skipped_sources": {}, "notes": []}
        remaining = lim.max_jobs_discovered_per_day - self.sched.counts(day).get("jobs_discovered", 0)
        if remaining <= 0:
            out.update(status="skipped", notes=["daily discovery limit reached"])
            return out
        try:
            run, skipped, query, runnable = self.discovery.start(None, None, True)
        except DiscoveryBusy:
            out.update(status="skipped", notes=["a discovery run is already in progress"])
            return out
        out["skipped_sources"] = skipped
        if run is None:
            out.update(status="nothing_to_run", notes=["no source permits automated discovery (manual import only)"])
            return out
        query.max_jobs = min(query.max_jobs or remaining, remaining)
        self.discovery.execute(run["run_id"], query, runnable)                      # synchronous: the scheduler thread owns this run
        doc = self.discovery.runs.get(run["run_id"]) or {}
        totals = doc.get("totals") or {}
        out.update(run_id=run["run_id"], new=totals.get("new", 0), duplicates=totals.get("duplicates", 0), fetched=totals.get("fetched", 0),
                   sources={k: {"new": v.get("new", 0), "fetched": v.get("fetched", 0), "state": v.get("state")} for k, v in (doc.get("sources") or {}).items()})
        if doc.get("status") == "failed":
            out["status"] = "failed"
        self.sched.add(day, "jobs_discovered", int(out["new"]))
        self._match_and_queue(run["run_id"], doc.get("started_at"), lim, out)
        if self.notifier is not None:
            try:
                from notifications.notification_templates import discovery_summary
                subject, body = discovery_summary({"totals": totals, "queued": out["queued"], "matched": out["matched"]})
                self.notifier.notify("discovery_summary", subject, body, dedupe_key=f"discovery:{run['run_id']}")
            except Exception as exc:  # noqa: BLE001
                logger.warning("Discovery summary not sent: %s", type(exc).__name__)
        return out

    def _match_and_queue(self, run_id: str, started_at, lim: AutomationLimits, out: dict) -> None:
        try:
            profile = self.matching.profile_for_matching(None, False)
        except (NoActiveResume, ProfileMissing, UnreviewedProfile) as exc:
            out["notes"].append(f"matching skipped: {exc}")                       # discovery still succeeded; matching needs a reviewed active profile
            return
        new_jobs = list(self.discovery.jobs._jobs.find({"discovered_at": {"$gte": started_at}, "status": {"$ne": "closed"}}, {"_id": 0, "job_id": 1})) if started_at else []
        for j in new_jobs:
            try:
                report, _ = self.matching.score(job_id=j["job_id"], resume_id=profile.resume_id, allow_unreviewed=False, use_ai=False)
            except Exception as exc:  # noqa: BLE001 - one bad job never stops the run
                logger.warning("Matching failed for one job: %s", type(exc).__name__)
                continue
            out["matched"] += 1
            try:
                self.queue.enqueue(job_id=j["job_id"], resume_id=profile.resume_id, match_score=report.match_score, eligibility=report.eligibility.status,
                                   job_title=report.job_title, company_name=report.company_name, source=None)
                out["queued"] += 1
            except NotQueueable:
                out["not_queued"] += 1
