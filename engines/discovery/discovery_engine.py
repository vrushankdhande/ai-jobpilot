"""Discovery engine: preferences -> permitted sources -> adapters -> normalise -> dedup -> MongoDB -> statistics.

Only sources whose policy is ENABLED *and* whose adapter has an authorised automated method are run; everything
else is reported as skipped with the reason (fail closed). A blocked source (HTTP 403/429, CAPTCHA, login wall) stops
for that source - it is never retried or worked around.
"""
import logging
import re
from datetime import datetime, timedelta, timezone

from config.scoring_config import Preferences
from database.models.job import DedupInfo, JobRecord
from database.repositories.discovery_runs_repository import DiscoveryRunsRepository
from database.repositories.jobs_repository import JobsRepository
from engines.discovery.deduplication import DedupConfig, canonical_url, company_key, decide, location_keys, title_key
from engines.discovery.job_normalizer import InvalidJob, normalize
from engines.matching.skill_extractor import SkillCatalog
from integrations.base_adapter import (
    AdapterError,
    JobAdapter,
    RateLimitExceeded,
    RawJob,
    SearchQuery,
    SourceBlocked,
    WORK_MODES,
)
from integrations.policy import PolicyRegistry, PolicyViolation

logger = logging.getLogger(__name__)

_UPDATE_FIELDS = ["job_title", "company_name", "job_description", "mandatory_skills", "preferred_skills", "experience_min",
                  "experience_max", "location", "location_keys", "work_mode", "employment_type", "salary_min", "salary_max",
                  "currency", "posted_date", "job_url", "title_key", "company_key", "canonical_url"]


def query_from_preferences(prefs: Preferences, overrides: dict | None = None) -> SearchQuery:
    """Preferences -> query. A work mode is excluded only if the user said NO to it (unknown is not 'no')."""
    excluded = {m for m, flag in (("remote", prefs.remote), ("hybrid", prefs.hybrid), ("onsite", prefs.onsite)) if flag is False}
    data = {
        "titles": prefs.titles, "skills": prefs.skills, "locations": prefs.locations, "remote_within_locations": prefs.remote_within_locations, "companies": prefs.companies,
        "work_modes": sorted(set(WORK_MODES) - excluded) if excluded else [], "employment_types": prefs.employment_types,
        "min_salary": prefs.min_salary, "salary_currency": prefs.salary_currency, "experience_min": prefs.experience_years_min,
        "experience_max": prefs.experience_years_max, "posted_within_days": prefs.posted_within_days,
        "keywords_include": prefs.keywords_include, "keywords_exclude": prefs.keywords_exclude,
    }
    data.update({k: v for k, v in (overrides or {}).items() if v not in (None, [], "")})
    return SearchQuery.model_validate(data)


def _places(record: JobRecord) -> set[str]:
    """Every place a job names: its city keys plus each part of its location text, so a preference of "India"
    matches "Bengaluru, India" (a city key alone would not)."""
    parts = [p.strip() for loc in record.location for p in re.split(r"[,(/)]|\s[-–]\s", loc) if p.strip()]
    return set(record.location_keys) | set(location_keys(parts))


def matches_query(record: JobRecord, q: SearchQuery, catalog: SkillCatalog, now: datetime) -> tuple[bool, str]:
    """Unknown values never exclude a job (except a missing title when a title filter is set)."""
    blob = f"{record.job_title or ''}\n{record.job_description or ''}".lower()
    if q.companies and record.company_key not in {company_key(c) for c in q.companies}:
        return False, "company"
    if q.titles:
        if not record.job_title or not any(title_key(t) in record.title_key or t.lower() in record.job_title.lower() for t in q.titles):
            return False, "title"
    if q.skills and (record.mandatory_skills or record.preferred_skills):
        have = {s.lower() for s in record.mandatory_skills + record.preferred_skills}
        if not any(catalog.normalize_or_raw(s).lower() in have for s in q.skills):
            return False, "skills"
    if q.experience_max is not None and record.experience_min is not None and record.experience_min > q.experience_max:
        return False, "experience"
    if q.work_modes and record.work_mode and record.work_mode not in q.work_modes:
        return False, "work_mode"
    if q.employment_types and record.employment_type and record.employment_type not in {e.lower() for e in q.employment_types}:
        return False, "employment_type"
    if q.locations and record.location_keys and (record.work_mode != "remote" or q.remote_within_locations):
        if not _places(record) & set(location_keys(q.locations)):
            return False, "location"
    if q.min_salary and record.salary_max is not None and (
            not q.salary_currency or not record.currency or q.salary_currency.upper() == record.currency):
        if record.salary_max < q.min_salary:
            return False, "salary"
    if q.posted_within_days and record.posted_date and record.posted_date < now - timedelta(days=q.posted_within_days):
        return False, "date_posted"
    if q.keywords_exclude and any(re.search(rf"\b{re.escape(k.lower())}\b", blob) for k in q.keywords_exclude):
        return False, "excluded_keyword"
    if q.keywords_include and record.job_description and not any(re.search(rf"\b{re.escape(k.lower())}\b", blob) for k in q.keywords_include):
        return False, "keywords"
    return True, ""


class SourceStats(dict):
    """Plain dict (stored in MongoDB) with the counters initialised."""

    def __init__(self):
        super().__init__(state="pending", fetched=0, invalid=0, filtered_out=0, new=0, updated=0, unchanged=0, merged=0,
                         flagged=0, errors=[], notes=[], blocked=False, requests=0)


class DiscoveryEngine:
    def __init__(self, adapters: dict[str, JobAdapter], jobs: JobsRepository, runs: DiscoveryRunsRepository,
                 registry: PolicyRegistry, catalog: SkillCatalog, bands: dict[str, float] | None = None,
                 dedup: DedupConfig | None = None, now_fn=lambda: datetime.now(timezone.utc)):
        self.adapters, self.jobs, self.runs, self.registry, self.catalog = adapters, jobs, runs, registry, catalog
        self.bands, self.dedup, self.now_fn = bands, dedup or DedupConfig(), now_fn

    # ---- planning (permission check happens before anything runs) -------------------------------------
    def plan(self, requested: list[str] | None = None) -> tuple[list[str], dict[str, str]]:
        runnable: list[str] = []
        skipped: dict[str, str] = {}
        for name in requested or list(self.adapters):
            adapter = self.adapters.get(name)
            if adapter is None:
                skipped[name] = "unknown source"
                continue
            policy = self.registry.get(name)
            if not policy.automated_allowed:
                skipped[name] = policy.deny_reason()
            elif not adapter.automated_discovery_supported:
                skipped[name] = f"{name}: no authorised automated method is implemented; import jobs manually"
            else:
                runnable.append(name)
        return runnable, skipped

    # ---- run ----------------------------------------------------------------------------------------------
    def run(self, query: SearchQuery, sources: list[str] | None = None, run_id: str | None = None) -> dict:
        runnable, skipped = self.plan(sources)
        run = self.runs.get(run_id) if run_id else None
        run = run or self.runs.create(query.model_dump(mode="json"), runnable, skipped)
        rid, stats = run["run_id"], {}
        try:
            for name in runnable:
                st = stats[name] = SourceStats()
                st["state"] = "running"
                self.runs.update(rid, {"sources": dict(stats)})
                self._run_source(name, query, st, rid, stats)
            totals = {k: sum(s[k] for s in stats.values()) for k in
                      ("fetched", "invalid", "filtered_out", "new", "updated", "unchanged", "merged", "flagged")}
            totals["duplicates"] = totals["unchanged"] + totals["updated"] + totals["merged"]
            problems = any(s["state"] in ("error", "blocked") for s in stats.values())
            self.runs.update(rid, {"status": "completed_with_errors" if problems else "completed", "sources": dict(stats),
                                   "totals": totals, "finished_at": self.now_fn()})
        except Exception as exc:  # never leave a run "running"
            logger.exception("Discovery run %s failed", rid)
            self.runs.update(rid, {"status": "failed", "error": f"Discovery failed ({type(exc).__name__})",
                                   "sources": dict(stats), "finished_at": self.now_fn()})
        return self.runs.get(rid)

    def _run_source(self, name: str, query: SearchQuery, st: SourceStats, rid: str, all_stats: dict) -> None:
        adapter = self.adapters[name]
        try:
            result = adapter.search(query)
        except PolicyViolation as exc:
            st["state"], st["notes"] = "skipped", [str(exc)]
            return
        except SourceBlocked as exc:
            st["state"], st["blocked"], st["errors"] = "blocked", True, [str(exc)]
            return
        except RateLimitExceeded as exc:
            st["state"], st["errors"] = "error", [str(exc)]
            return
        except AdapterError as exc:
            st["state"], st["errors"] = "error", [str(exc)]
            return
        except Exception as exc:
            logger.exception("Unexpected error in source %s", name)
            st["state"], st["errors"] = "error", [f"Unexpected error ({type(exc).__name__})"]
            return
        st["notes"], st["errors"], st["blocked"], st["requests"] = result.notes, result.errors, result.blocked, result.requests_made
        st["fetched"] += result.prefiltered      # listed by the source, then dropped by the adapter's title filter
        st["filtered_out"] += result.prefiltered
        cap = query.max_jobs or self.registry.get(name).max_jobs_per_run or 10**9
        kept = done = 0                           # the cap counts jobs that pass your filters, so jobs dropped by the location /
        for i, raw in enumerate(result.jobs):     # work-mode filter no longer use up the allowance before a wanted job is reached
            if kept >= cap:
                break
            before = st["filtered_out"] + st["unchanged"]
            st["fetched"] += 1
            self.ingest(raw, adapter, query, st)
            done += 1
            kept += st["filtered_out"] + st["unchanged"] == before     # jobs already stored and unchanged do not use the allowance either
            if i % 25 == 24:
                self.runs.update(rid, {"sources": dict(all_stats)})
        if len(result.jobs) > done:
            st["notes"] = st["notes"] + [f"Job cap of {cap} reached; {len(result.jobs) - done} job(s) not processed"]
        st["state"] = "blocked" if result.blocked else ("error" if result.errors and not st["fetched"] else "done")

    # ---- ingest one raw job ------------------------------------------------------------------------------------
    def ingest(self, raw: RawJob, adapter: JobAdapter, query: SearchQuery | None, st: dict, manual: bool = False) -> dict:
        now = self.now_fn()
        if not manual:
            problems = adapter.validate(raw)
            if problems:
                st["invalid"] += 1
                return {"action": "invalid", "reasons": problems}
        try:
            record = normalize(raw, self.catalog, self.bands, now)
        except InvalidJob as exc:
            st["invalid"] += 1
            return {"action": "invalid", "reasons": [str(exc)]}
        except Exception:
            st["invalid"] += 1
            return {"action": "invalid", "reasons": ["record could not be validated"]}
        if query is not None:
            keep, why = matches_query(record, query, self.catalog, now)
            if not keep:
                st["filtered_out"] += 1
                return {"action": "filtered_out", "reasons": [why]}
        return self._store(record, st, now)

    def _store(self, record: JobRecord, st: dict, now: datetime) -> dict:
        candidates = self.jobs.candidates_for(record)
        decision = decide(record, candidates, self.dedup)
        out = {"action": decision.action, "confidence": decision.confidence, "reasons": decision.reasons,
               "match_job_id": decision.match_job_id, "job_id": decision.match_job_id}
        existing = next((c for c in candidates if c["job_id"] == decision.match_job_id), None) if decision.match_job_id else None
        if decision.action == "unchanged" and existing:
            self.jobs.touch_seen(existing["job_id"], record.source_refs[0].model_dump(), now)
            st["unchanged"] += 1
        elif decision.action == "update" and existing:
            self._apply_update(existing, record, now)
            st["updated"] += 1
        elif decision.action == "merge" and existing:
            self._apply_merge(existing, record, decision, now)
            st["merged"] += 1
        else:
            if decision.action == "flag":
                record.dedup = DedupInfo(status="possible_duplicate", confidence=decision.confidence,
                                         matched_job_id=decision.match_job_id, reasons=decision.reasons, needs_review=True)
            doc, inserted = self.jobs.insert_discovered(record)
            out["job_id"] = doc["job_id"]
            if inserted:
                st["flagged" if decision.action == "flag" else "new"] += 1
                out["action"] = "flag" if decision.action == "flag" else "insert"
            else:
                st["unchanged"] += 1
                out["action"] = "unchanged"
        return out

    # ---- applying decisions ---------------------------------------------------------------------------------------------
    @staticmethod
    def _same_ref(r: dict, ref) -> bool:
        return (r.get("source") == ref.source and (r.get("source_job_id") == ref.source_job_id
                                                    or (ref.canonical_url and r.get("canonical_url") == ref.canonical_url)))

    def _apply_update(self, existing: dict, record: JobRecord, now: datetime) -> None:
        ref = record.source_refs[0]
        refs = [dict(r) for r in existing.get("source_refs") or []]
        primary = existing.get("source") == record.source and (
            existing.get("source_job_id") == record.source_job_id or existing.get("canonical_url") == record.canonical_url)
        changed = []
        fields: dict = {}
        for f in _UPDATE_FIELDS:
            new, old = getattr(record, f), existing.get(f)
            empty = new in (None, "", [])
            if primary and not empty and new != old:
                fields[f] = new
                changed.append(f)
            elif not primary and old in (None, "", []) and not empty:
                fields[f] = new
                changed.append(f)
        for r in refs:
            if self._same_ref(r, ref):
                r.update(last_seen_at=now, content_hash=ref.content_hash, original=ref.original, url=ref.url or r.get("url"))
        fields.update(source_refs=refs, last_updated_at=now, status="active",
                      last_change={"at": now, "fields": changed, "source": record.source})
        if primary:
            fields["content_hash"] = record.content_hash
        try:
            self.jobs.update_job(existing["job_id"], fields)
        except Exception:  # e.g. a unique content_hash collision: keep everything except the hash
            fields.pop("content_hash", None)
            self.jobs.update_job(existing["job_id"], fields)

    def _apply_merge(self, existing: dict, record: JobRecord, decision, now: datetime) -> None:
        ref = record.source_refs[0]
        refs = [dict(r) for r in existing.get("source_refs") or []]
        if not any(self._same_ref(r, ref) for r in refs):
            refs.append(ref.model_dump())
        fields: dict = {}
        for f in _UPDATE_FIELDS:  # fill gaps only - never overwrite what the first source already provided
            if existing.get(f) in (None, "", []) and getattr(record, f) not in (None, "", []):
                fields[f] = getattr(record, f)
        fields.update(source_refs=refs, last_updated_at=now, status="active",
                      dedup={"status": "merged", "confidence": decision.confidence, "matched_job_id": None,
                             "reasons": decision.reasons, "needs_review": False})
        self.jobs.update_job(existing["job_id"], fields)
