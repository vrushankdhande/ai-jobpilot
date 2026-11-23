"""Daily report: what was discovered, matched, prepared and - only with evidence - submitted. Stored in MongoDB (append-only history), optionally e-mailed.

Counting rules (never inflated):
* ``submitted``  = applications whose state is SUBMITTED/VERIFIED with a page-confirmed outcome, submitted today;
  ``submitted_self_reported`` = recorded by the candidate after applying manually. Prepared, approved or attempted applications are NOT submissions.
* ``verified`` = state VERIFIED today. ``pending_verification`` / ``awaiting_approval`` / ``manual_handoff`` are current snapshots.
"""
import logging
from datetime import datetime, time, timedelta, timezone
from typing import Callable

from pymongo.database import Database

from ai.schemas.candidate_profile import new_id
from config.automation_config import AutomationLimits
from database.repositories.queue_repository import QueueRepository
from database.repositories.scheduler_repository import SchedulerRepository
from scheduler.scheduler import get_zone, local_day

logger = logging.getLogger(__name__)
_NO_ID = {"_id": 0}


def day_bounds(day: str, tz) -> tuple[datetime, datetime]:
    d = datetime.fromisoformat(day).date()
    start = datetime.combine(d, time.min, tzinfo=tz)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


class DailyReport:
    def __init__(self, db: Database, sched: SchedulerRepository, queue: QueueRepository, limits_fn: Callable[[], AutomationLimits], tz_name: str, *,
                 notifier=None, now_fn=lambda: datetime.now(timezone.utc)):
        self.db, self.sched, self.queue, self.limits_fn, self.notifier, self.now = db, sched, queue, limits_fn, notifier, now_fn
        self.tz = get_zone(tz_name)

    def build(self, day: str | None = None) -> dict:
        now = self.now()
        day = day or local_day(now, self.tz)
        start, end = day_bounds(day, self.tz)
        lim, db = self.limits_fn(), self.db
        in_day = {"$gte": start, "$lt": end}

        by_source = {d["_id"] or "unknown": d["n"] for d in db["jobs"].aggregate([{"$match": {"discovered_at": in_day}}, {"$group": {"_id": "$source", "n": {"$sum": 1}}}])}
        runs = list(db["discovery_runs"].find({"started_at": in_day}, _NO_ID))
        duplicates = sum((r.get("totals") or {}).get("duplicates", 0) for r in runs)
        discovery = {"discovered": sum(by_source.values()), "by_source": by_source, "duplicates_removed": duplicates, "runs": len(runs)}

        latest: dict[str, dict] = {}
        for m in db["job_matches"].find({"created_at": in_day}, {"_id": 0, "job_id": 1, "classification": 1, "eligibility_status": 1, "match_score": 1, "created_at": 1}):
            if m["job_id"] not in latest or m["created_at"] > latest[m["job_id"]]["created_at"]:
                latest[m["job_id"]] = m
        cls = lambda name: sum(1 for m in latest.values() if m.get("classification") == name)  # noqa: E731
        matching = {"matched": len(latest), "excellent": cls("Excellent Match"), "strong": cls("Strong Match"), "good": cls("Good Match"),
                    "eligible": sum(1 for m in latest.values() if m.get("eligibility_status") == "PASS"),
                    "eligibility_unknown": sum(1 for m in latest.values() if m.get("eligibility_status") == "UNKNOWN")}

        apps = db["applications"]
        count = lambda q: apps.count_documents(q)  # noqa: E731
        confirmed = {"state": {"$in": ["SUBMITTED", "VERIFIED"]}, "submitted_at": in_day}
        failed_apps = count({"state": "SUBMISSION_FAILED", "updated_at": in_day})
        failed_tasks = db["application_queue"].count_documents({"status": "failed", "updated_at": in_day})
        applications = {
            "awaiting_approval": count({"state": {"$in": ["READY_FOR_REVIEW", "AWAITING_USER_INPUT", "APPROVED"]}}),
            "submitted": count({**confirmed, "submission.outcome": "confirmed"}), "submitted_self_reported": count({**confirmed, "submission.outcome": "user_reported"}),
            "verified": count({"state": "VERIFIED", "verified_at": in_day}), "failed": failed_apps + failed_tasks, "failed_applications": failed_apps, "failed_tasks": failed_tasks,
            "manual_handoff": count({"state": "MANUAL_HANDOFF_REQUIRED"}), "pending_verification": count({"state": "VERIFICATION_PENDING"}),
            "prepared_today": count({"created_at": in_day}), "note": "prepared or attempted applications are not counted as submitted"}

        counts = self.sched.counts(day)
        submitted_auto = count({"mode": "automated", "submitted_at": in_day, "state": {"$in": ["SUBMITTING", "SUBMITTED", "VERIFICATION_PENDING", "VERIFIED"]}})
        limits = {"jobs_discovered": {"used": counts.get("jobs_discovered", 0), "limit": lim.max_jobs_discovered_per_day},
                  "jobs_processed": {"used": counts.get("jobs_processed", 0), "limit": lim.max_jobs_processed_per_day},
                  "applications_submitted": {"used": submitted_auto, "limit": lim.max_applications_per_day}}
        return {"report_id": new_id(), "date": day, "timezone": self.tz.key, "generated_at": now, "discovery": discovery, "matching": matching, "applications": applications,
                "queue": self.queue.counts(), "limits": limits, "daily_target": lim.daily_application_target,
                "target_note": "A target to aim for, not a quota: limits, permissions, eligibility and your approvals always take priority."}

    def generate(self, day: str | None = None, *, email: bool = False, trigger: str = "manual") -> dict:
        rep = self.build(day)
        rep["trigger"] = trigger
        self.sched.save_report(dict(rep))
        if email and self.notifier is not None:
            from notifications.notification_templates import daily_report
            subject, body = daily_report(rep)
            rec = self.notifier.notify("daily_report", subject, body, dedupe_key=f"report:{rep['date']}:{rep['report_id']}")
            rep["email"] = {"status": rec.get("status"), "notification_id": rec.get("notification_id")}
        return rep
