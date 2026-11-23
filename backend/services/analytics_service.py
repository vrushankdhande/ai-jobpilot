"""Dashboard metrics and analytics - every number is computed from stored records (jobs, matches, applications, interviews, runs); nothing is estimated.

Counting rules (the same definitions are used by the dashboard, the analytics page, the exports and the daily report):
* a *match* is the LATEST saved match report per job (for the active resume when there is one);
* "matching the profile" = latest score >= the effective minimum match score AND eligibility is not FAIL;
* *submitted* = application state SUBMITTED or VERIFIED (both need evidence); *confirmed* = page-confirmed, *self-reported* = recorded by the candidate;
* *attempts* = confirmed + self-reported + pending verification + failed-after-sending; success/failure/unknown rates use those attempts as the denominator,
  so they always add up to 100 %;
* days are local days in the scheduler time zone (default Asia/Kolkata).
"""
import csv
import io
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone

from pymongo.database import Database

from config.automation_config import effective, load_limits
from config.settings import Settings
from database.repositories.scheduler_repository import SchedulerRepository
from database.repositories.users_repository import UsersRepository
from scheduler.scheduler import get_zone

_NO_ID = {"_id": 0}
AWAITING = ["READY_FOR_REVIEW", "AWAITING_USER_INPUT", "APPROVED"]
SUBMITTED_STATES = ["SUBMITTED", "VERIFIED"]
BUCKETS = [(lo, lo + 9) for lo in range(0, 100, 10)]
MAX_RANGE_DAYS = 366



def _idle_run(r: dict) -> bool:
    """A completed scheduled tick that changed nothing (empty worker run, retry with nothing to retry)."""
    res = r.get("result") or {}
    if r.get("status") != "completed" or r.get("trigger") != "scheduled":
        return False
    if r.get("job") == "application_worker":
        return not res.get("processed") and not res.get("recovered") and not res.get("synced")
    if r.get("job") == "notification_retry":
        return not res.get("retried") and not res.get("sent") and not res.get("failed")
    return False


def _run_summary(r: dict) -> str:
    res = r.get("result") or {}
    if r.get("job") == "daily_discovery" and "new" in res:
        return f": {res.get('new', 0)} new job(s), {res.get('queued', 0)} queued"
    if r.get("job") == "application_worker" and res.get("processed"):
        return f": {len(res['processed'])} application(s) prepared"
    return ""

class RangeError(ValueError):
    pass


def _csv_safe(v):
    return "'" + v if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r") else v


class AnalyticsService:
    def __init__(self, db: Database, settings: Settings, now_fn=lambda: datetime.now(timezone.utc)):
        self.db, self.settings, self.now = db, settings, now_fn
        self.tz = get_zone(settings.scheduler_timezone)

    # ----------------------------------------------------------------------------------------------- helpers
    def _min_score(self) -> float:
        caps = load_limits(self.settings)
        return effective(caps, SchedulerRepository(self.db).get_state().get("limit_overrides")).min_match_score

    def _bounds(self, d: date) -> tuple[datetime, datetime]:
        start = datetime.combine(d, time.min, tzinfo=self.tz)
        return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)

    def resolve_range(self, date_from: str | None, date_to: str | None, default_days: int = 30) -> tuple[date, date]:
        today = self.now().astimezone(self.tz).date()
        try:
            end = date.fromisoformat(date_to) if date_to else today
            start = date.fromisoformat(date_from) if date_from else end - timedelta(days=default_days - 1)
        except ValueError:
            raise RangeError("Dates must be YYYY-MM-DD.") from None
        if start > end:
            raise RangeError("The start date is after the end date.")
        if (end - start).days + 1 > MAX_RANGE_DAYS:
            raise RangeError(f"The range is limited to {MAX_RANGE_DAYS} days.")
        return start, end

    def _local_day(self, dt: datetime) -> str:
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(self.tz).date().isoformat()

    def _days(self, start: date, end: date) -> list[str]:
        return [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]

    def _bucket(self, col: str, field: str, start_utc: datetime, end_utc: datetime, extra: dict | None = None) -> Counter:
        q = {field: {"$gte": start_utc, "$lt": end_utc}, **(extra or {})}
        return Counter(self._local_day(d[field]) for d in self.db[col].find(q, {"_id": 0, field: 1}) if d.get(field))

    def latest_matches(self, start_utc: datetime | None = None, end_utc: datetime | None = None) -> list[dict]:
        resume = UsersRepository(self.db).get_active_resume_id()
        match: dict = {}
        if resume:
            match["resume_id"] = resume
        if start_utc and end_utc:
            match["created_at"] = {"$gte": start_utc, "$lt": end_utc}
        pipe = ([{"$match": match}] if match else []) + [{"$sort": {"created_at": -1}}, {"$group": {"_id": "$job_id", "match_score": {"$first": "$match_score"},
                "classification": {"$first": "$classification"}, "eligibility_status": {"$first": "$eligibility_status"}, "created_at": {"$first": "$created_at"}}}]
        return [{**d, "job_id": d.pop("_id")} for d in self.db["job_matches"].aggregate(pipe)]

    # ----------------------------------------------------------------------------------------------- shared application maths
    def application_counts(self, start_utc: datetime | None = None, end_utc: datetime | None = None) -> dict:
        """Attempts/success/failure from the SAME counters everywhere. Range filters on the date the event happened."""
        apps = self.db["applications"]
        rng = lambda f: {f: {"$gte": start_utc, "$lt": end_utc}} if start_utc else {}  # noqa: E731
        confirmed = apps.count_documents({"state": {"$in": SUBMITTED_STATES}, "submission.outcome": "confirmed", **rng("submitted_at")})
        self_reported = apps.count_documents({"state": {"$in": SUBMITTED_STATES}, "submission.outcome": "user_reported", **rng("submitted_at")})
        pending = apps.count_documents({"state": "VERIFICATION_PENDING", **rng("submitted_at")})
        failed = apps.count_documents({"state": "SUBMISSION_FAILED", "failure_stage": {"$in": ["after_submit", "rejected"]}, **rng("updated_at")})
        attempts = confirmed + self_reported + pending + failed
        pct = lambda n: round(100.0 * n / attempts, 1) if attempts else None  # noqa: E731
        return {"confirmed": confirmed, "self_reported": self_reported, "submitted": confirmed + self_reported, "pending_verification": pending, "failed_after_send": failed,
                "attempts": attempts, "success_rate": pct(confirmed + self_reported), "failure_rate": pct(failed), "unknown_rate": pct(pending),
                "note": "Rates use attempts (submitted + pending verification + rejected/failed after sending) as the denominator. Prepared applications are not attempts."}

    # ----------------------------------------------------------------------------------------------- dashboard
    def dashboard(self, days: int = 14) -> dict:
        now = self.now()
        today = now.astimezone(self.tz).date()
        start_d = today - timedelta(days=days - 1)
        s_utc, e_utc = self._bounds(start_d)[0], self._bounds(today)[1]
        db, min_score = self.db, self._min_score()
        latest = self.latest_matches()
        scores = [m["match_score"] for m in latest]
        # same rule as Job Matching's "Good matches": fits your preferences, eligibility PASS, score at or above the queue minimum
        from backend.services.matching_service import preferred_job_ids_in
        from config.scoring_config import load_preferences
        from engines.matching.skill_extractor import get_catalog
        fits = set(preferred_job_ids_in(db, load_preferences(self.settings), get_catalog(self.settings)))
        matching = [m for m in latest if m["job_id"] in fits and m["match_score"] >= min_score and m.get("eligibility_status") == "PASS"]
        apps = db["applications"]
        count = lambda q: apps.count_documents({**q, "dismissed_at": None})  # noqa: E731
        by_state = {d["_id"]: d["n"] for d in apps.aggregate([{"$match": {"dismissed_at": None}}, {"$group": {"_id": "$state", "n": {"$sum": 1}}}])}
        pending = {"needs_your_input": by_state.get("AWAITING_USER_INPUT", 0), "ready_to_approve": by_state.get("READY_FOR_REVIEW", 0), "approved_not_submitted": by_state.get("APPROVED", 0),
                   "verify_outcome": by_state.get("VERIFICATION_PENDING", 0), "manual_handoff": by_state.get("MANUAL_HANDOFF_REQUIRED", 0)}
        interviews = list(db["interviews"].find({"status": "scheduled", "scheduled_at": {"$gte": now, "$lt": now + timedelta(days=7)}}, _NO_ID).sort("scheduled_at", 1))
        discovered = self._bucket("jobs", "discovered_at", s_utc, e_utc)
        prepared = self._bucket("applications", "created_at", s_utc, e_utc)
        submitted = self._bucket("applications", "submitted_at", s_utc, e_utc, {"state": {"$in": SUBMITTED_STATES}})
        series = [{"date": d, "jobs_discovered": discovered.get(d, 0), "applications_prepared": prepared.get(d, 0), "applications_submitted": submitted.get(d, 0)} for d in self._days(start_d, today)]
        st = SchedulerRepository(db).get_state()
        return {
            "generated_at": now, "timezone": self.tz.key, "min_match_score": min_score,
            "jobs": {"total": db["jobs"].count_documents({}), "discovered_today": discovered.get(today.isoformat(), 0), "scored": len(latest), "matching_profile": len(matching),
                     "average_match_score": round(sum(scores) / len(scores), 1) if scores else None},
            "applications": {"awaiting_approval": sum(by_state.get(s, 0) for s in AWAITING), "submitted": sum(by_state.get(s, 0) for s in SUBMITTED_STATES), "verified": by_state.get("VERIFIED", 0),
                             "failed": by_state.get("SUBMISSION_FAILED", 0), "by_state": by_state, "pending_actions": pending, "pending_actions_total": sum(pending.values())},
            "interviews": {"upcoming_7_days": len(interviews), "next": [{k: i.get(k) for k in ("interview_id", "company_name", "job_title", "round", "scheduled_at", "meeting_link")} for i in interviews[:5]]},
            "queue": {d["_id"]: d["n"] for d in db["application_queue"].aggregate([{"$group": {"_id": "$status", "n": {"$sum": 1}}}])},
            "activity": series, "recent": self._recent(),
            "system": {"database": "connected", "scheduler_enabled": bool(st.get("enabled")), "scheduler_paused": bool(st.get("paused")),
                       "last_discovery": (SchedulerRepository(db).latest_run("daily_discovery") or {}).get("started_at"),
                       "notifications_enabled": bool(self.settings.notifications_enabled), "ai_key_configured": bool(self.settings.ai_api_key),
                       "automation_enabled": bool(self.settings.application_automation_enabled)}}

    def _recent(self, limit: int = 12) -> list[dict]:
        db, rows = self.db, []
        events = list(db["application_events"].find({}, _NO_ID).sort("at", -1).limit(limit))
        names = {a["application_id"]: f"{a.get('job_title') or 'a job'} at {a.get('company_name') or 'a company'}"
                 for a in db["applications"].find({"application_id": {"$in": [e["application_id"] for e in events]}}, {"_id": 0, "application_id": 1, "job_title": 1, "company_name": 1})}
        for e in events:
            rows.append({"at": e["at"], "kind": "application", "text": f"{e['type'].replace('_', ' ')}: {names.get(e['application_id'], 'application')}" + (f" → {e['to_state']}" if e.get("to_state") else "")})
        shown = 0
        for r in db["scheduler_runs"].find({}, _NO_ID).sort("started_at", -1).limit(limit * 40):
            if _idle_run(r):
                continue                   # a worker / retry tick that found nothing to do is not activity (it runs every minute)
            rows.append({"at": r["started_at"], "kind": "scheduler", "text": f"{r['job'].replace('_', ' ')} ({r['trigger']}) {r['status']}" + _run_summary(r)})
            shown += 1
            if shown >= limit:
                break
        for i in db["interviews"].find({}, _NO_ID).sort("created_at", -1).limit(limit):
            rows.append({"at": i["created_at"], "kind": "interview", "text": f"interview added: {i['job_title']} at {i['company_name']} ({i['round']})"})
        return sorted(rows, key=lambda r: r["at"], reverse=True)[:limit]

    # ----------------------------------------------------------------------------------------------- analytics
    def overview(self, date_from: str | None = None, date_to: str | None = None) -> dict:
        start, end = self.resolve_range(date_from, date_to)
        s_utc, e_utc = self._bounds(start)[0], self._bounds(end)[1]
        days = self._days(start, end)
        db = self.db
        discovered = self._bucket("jobs", "discovered_at", s_utc, e_utc)
        prepared = self._bucket("applications", "created_at", s_utc, e_utc)
        submitted = self._bucket("applications", "submitted_at", s_utc, e_utc, {"state": {"$in": SUBMITTED_STATES}})
        failed = self._bucket("applications", "updated_at", s_utc, e_utc, {"state": "SUBMISSION_FAILED", "failure_stage": {"$in": ["after_submit", "rejected"]}})
        interviews = self._bucket("interviews", "scheduled_at", s_utc, e_utc)
        matched = Counter(self._local_day(m["created_at"]) for m in self.latest_matches(s_utc, e_utc))
        daily = [{"date": d, "jobs_discovered": discovered.get(d, 0), "jobs_matched": matched.get(d, 0), "applications_prepared": prepared.get(d, 0),
                  "applications_submitted": submitted.get(d, 0), "applications_failed": failed.get(d, 0), "interviews": interviews.get(d, 0)} for d in days]
        weekly: dict[str, dict] = defaultdict(lambda: {k: 0 for k in daily[0] if k != "date"})
        for row in daily:
            iso = date.fromisoformat(row["date"]).isocalendar()
            wk = weekly[f"{iso[0]}-W{iso[1]:02d}"]
            for k, v in row.items():
                if k != "date":
                    wk[k] += v
        latest = self.latest_matches(s_utc, e_utc)
        dist = [{"range": f"{lo}-{hi if hi < 99 else 100}", "jobs": sum(1 for m in latest if lo <= m["match_score"] < (hi + 1 if hi < 99 else 101))} for lo, hi in BUCKETS]
        by_status = {d["_id"]: d["n"] for d in db["applications"].aggregate([{"$match": {"created_at": {"$gte": s_utc, "$lt": e_utc}}}, {"$group": {"_id": "$state", "n": {"$sum": 1}}}])}
        counts = self.application_counts(s_utc, e_utc)
        sources = self._sources(s_utc, e_utc)
        iv = list(db["interviews"].find({"scheduled_at": {"$gte": s_utc, "$lt": e_utc}}, _NO_ID))
        done = [i for i in iv if i["status"] == "completed"]
        conversion = {"interviews": len(iv), "completed": len(done), "offers": sum(1 for i in iv if i["outcome"] == "offer"), "rejected": sum(1 for i in iv if i["outcome"] == "rejected"),
                      "advanced": sum(1 for i in iv if i["outcome"] == "advanced"), "submitted_applications": counts["submitted"],
                      "interviews_per_submission": round(len(iv) / counts["submitted"], 2) if counts["submitted"] else None,
                      "offer_rate_of_completed": round(100.0 * sum(1 for i in done if i["outcome"] == "offer") / len(done), 1) if done else None}
        return {"from": start.isoformat(), "to": end.isoformat(), "timezone": self.tz.key, "days": len(days), "daily": daily,
                "weekly": [{"week": k, **v} for k, v in sorted(weekly.items())], "score_distribution": dist, "scored_jobs": len(latest),
                "applications_by_status": by_status, "application_outcomes": counts, "sources": sources, "interview_conversion": conversion,
                "totals": {k: sum(r[k] for r in daily) for k in daily[0] if k != "date"}}

    def _sources(self, s_utc: datetime, e_utc: datetime) -> list[dict]:
        db = self.db
        jobs = {d["_id"] or "unknown": d for d in db["jobs"].aggregate([{"$match": {"discovered_at": {"$gte": s_utc, "$lt": e_utc}}}, {"$group": {"_id": "$source", "jobs": {"$sum": 1}}}])}
        matches = self.latest_matches(s_utc, e_utc)
        src_of = {d["job_id"]: d.get("source") or "unknown" for d in db["jobs"].find({"job_id": {"$in": [m["job_id"] for m in matches]}}, {"_id": 0, "job_id": 1, "source": 1})}
        scores: dict[str, list[float]] = defaultdict(list)
        for m in matches:
            scores[src_of.get(m["job_id"], "unknown")].append(m["match_score"])
        apps: dict[str, Counter] = defaultdict(Counter)
        for a in db["applications"].find({"created_at": {"$gte": s_utc, "$lt": e_utc}}, {"_id": 0, "source": 1, "state": 1, "submission.outcome": 1}):
            c = apps[a.get("source") or "unknown"]
            c["applications"] += 1
            if a["state"] in SUBMITTED_STATES:
                c["submitted"] += 1
        names = sorted(set(jobs) | set(scores) | set(apps))
        return [{"source": n, "jobs": jobs.get(n, {}).get("jobs", 0), "scored": len(scores[n]), "avg_match_score": round(sum(scores[n]) / len(scores[n]), 1) if scores[n] else None,
                 "applications": apps[n]["applications"], "submitted": apps[n]["submitted"]} for n in names]

    # ----------------------------------------------------------------------------------------------- export
    def export_csv(self, kind: str, date_from: str | None, date_to: str | None) -> str:
        data = self.overview(date_from, date_to)
        rows: list[dict]
        if kind == "daily":
            rows = data["daily"]
        elif kind == "weekly":
            rows = data["weekly"]
        elif kind == "status":
            rows = [{"status": k, "applications": v} for k, v in sorted(data["applications_by_status"].items())]
        elif kind == "sources":
            rows = data["sources"]
        elif kind == "scores":
            rows = data["score_distribution"]
        else:
            raise RangeError("kind must be one of: daily, weekly, status, sources, scores")
        buf = io.StringIO()
        if rows:
            w = csv.DictWriter(buf, fieldnames=list(rows[0]), lineterminator="\n")
            w.writeheader()
            for r in rows:
                w.writerow({k: _csv_safe(v) for k, v in r.items()})
        return buf.getvalue()
