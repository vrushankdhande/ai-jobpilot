"""Job discovery workflow: start runs, import jobs, list/refresh them - always through the policy registry."""
import logging
import re
from datetime import datetime
from urllib.parse import urlparse

from config.scoring_config import Preferences
from database.models.job import JobRecord
from database.repositories.discovery_runs_repository import DiscoveryRunsRepository
from database.repositories.jobs_repository import JobsRepository
from engines.discovery.discovery_engine import DiscoveryEngine, SourceStats, query_from_preferences
from integrations.base_adapter import AdapterError, JobAdapter, JobGone, RawJob, SearchQuery, host_allowed
from integrations.career_pages.ats_detector import safe_token
from integrations.policy import PolicyRegistry, PolicyViolation

logger = logging.getLogger(__name__)
_PLATFORM_HOSTS = {"linkedin": "linkedin.com", "indeed": "indeed.com", "naukri": "naukri.com", "instahyre": "instahyre.com"}


class DiscoveryBusy(Exception):
    pass


class JobNotFound(Exception):
    pass


class NotRefreshable(Exception):
    pass


NOT_JOB_SOURCES = {"email_rejections"}     # policy entries that are not job sources (rejection tracking reads replies, not jobs)


class DiscoveryService:
    def __init__(self, jobs: JobsRepository, runs: DiscoveryRunsRepository, registry: PolicyRegistry,
                 adapters: dict[str, JobAdapter], engine: DiscoveryEngine, prefs: Preferences):
        self.jobs, self.runs, self.registry, self.adapters, self.engine, self.prefs = jobs, runs, registry, adapters, engine, prefs

    # ---- sources -----------------------------------------------------------------------------------------
    def sources(self) -> list[dict]:
        out = []
        for row in self.registry.describe():
            name = row["source"]
            if name in NOT_JOB_SOURCES:
                continue
            adapter = self.adapters.get(name)
            row["adapter_available"] = adapter is not None
            row["automated_discovery"] = bool(row["automated_discovery"] and adapter and adapter.automated_discovery_supported)
            if not row["automated_discovery"]:
                p = self.registry.get(name)
                row["reason"] = (p.deny_reason() if not p.automated_allowed
                                 else f"{name}: no authorised automated method is implemented")
            else:
                row["reason"] = None
            if adapter is not None and hasattr(adapter, "companies"):
                row["companies_configured"] = len(adapter.companies)
            stored_as = {"career_pages": "company_career_page", "manual": "manual_paste"}.get(name)   # jobs record the source under these names
            row["jobs_stored"] = self.jobs.count(source=name) + (self.jobs.count(source=stored_as) if stored_as else 0)
            out.append(row)
        return out

    # ---- discovery runs -------------------------------------------------------------------------------------
    def start(self, overrides: dict | None, sources: list[str] | None, use_preferences: bool = True):
        if self.runs.running():
            raise DiscoveryBusy("A discovery run is already in progress")
        query = query_from_preferences(self.prefs if use_preferences else Preferences(), overrides)
        runnable, skipped = self.engine.plan(sources)
        if not runnable:
            return None, skipped, query, runnable
        run = self.runs.create(query.model_dump(mode="json"), runnable, skipped)
        return run, skipped, query, runnable

    def execute(self, run_id: str, query: SearchQuery, sources: list[str]) -> None:
        self.engine.run(query, sources, run_id)

    def status(self, run_id: str | None = None) -> dict:
        run = self.runs.get(run_id) if run_id else self.runs.latest()
        return run or {"status": "never_run"}

    # ---- import ----------------------------------------------------------------------------------------------
    def import_job(self, url: str | None, fields: dict | None, source: str | None = None) -> dict:
        fields = {k: v for k, v in (fields or {}).items() if v not in (None, "")}
        raw, adapter = self._build_import(url, fields, source)
        outcome = self.engine.ingest(raw, adapter, None, SourceStats(), manual=True)
        if outcome["action"] == "invalid":
            raise ValueError("; ".join(outcome["reasons"]))
        doc = self.jobs.get_job(outcome["job_id"]) if outcome.get("job_id") else None
        return {"action": outcome["action"], "confidence": outcome.get("confidence"), "reasons": outcome.get("reasons", []),
                "job": doc, "source": raw.source}

    def _build_import(self, url: str | None, fields: dict, source: str | None = None) -> tuple[RawJob, JobAdapter]:
        host = (urlparse(url).hostname or "").lower() if url else ""
        # an ATS job URL can be completed through the official public API (only if that provider is ENABLED)
        if url and host:
            gh = re.match(r"^/([A-Za-z0-9_\-]+)/jobs/(\d+)", urlparse(url).path) if host in (
                "boards.greenhouse.io", "job-boards.greenhouse.io", "boards.eu.greenhouse.io") else None
            lv = re.match(r"^/([A-Za-z0-9_\-]+)/([0-9a-fA-F\-]{20,})", urlparse(url).path) if host in (
                "jobs.lever.co", "jobs.eu.lever.co") else None
            if gh or lv:
                m, provider = (gh, "greenhouse") if gh else (lv, "lever")
                adapter = self.adapters["career_pages"]
                if not self.registry.get("career_pages", provider).automated_allowed:
                    raise PolicyViolation(self.registry.get("career_pages", provider).deny_reason())
                stub = RawJob(source="company_career_page", provider=provider, source_job_id=m.group(2), url=url,
                              company=fields.get("company_name"), original={"board_token": safe_token(m.group(1))})
                try:
                    raw = adapter.fetch_details(stub)
                except JobGone:
                    raise ValueError("That job is not listed any more") from None
                for key, attr in (("job_title", "title"), ("company_name", "company"), ("job_description", "description")):
                    if not getattr(raw, attr) and fields.get(key):
                        setattr(raw, attr, fields[key])
                return raw, adapter
        # everything else: user-supplied details, nothing is fetched
        name = next((n for n, suffix in _PLATFORM_HOSTS.items() if host and host_allowed(host, [suffix])), "manual")
        if not url and source in _PLATFORM_HOSTS:   # copied from that site by the user without a job link (e.g. a pasted results page): keep the site as the source
            name = source
        adapter = self.adapters.get(name)
        if adapter is None:
            raise PolicyViolation(f"{name}: not available")
        return adapter.import_manual(url, fields), adapter

    # ---- read / refresh ----------------------------------------------------------------------------------------
    def list_jobs(self, **filters) -> list[dict]:
        return self.jobs.list_filtered(**filters)

    def get_job(self, job_id: str) -> dict:
        doc = self.jobs.get_job(job_id)
        if doc is None:
            raise JobNotFound(job_id)
        return doc

    def refresh(self, job_id: str) -> dict:
        doc = self.get_job(job_id)
        record = JobRecord.from_document(doc)
        ref = next((r for r in record.source_refs if r.source == "company_career_page" and r.provider in ("greenhouse", "lever", "generic")), None)
        if ref is None:
            raise NotRefreshable("This job came from a manual-only source (or was pasted): automated refresh is not permitted. "
                                 "Re-import it with updated details.")
        adapter = self.adapters["career_pages"]
        stub = RawJob(source=ref.source, provider=ref.provider, source_job_id=ref.source_job_id, url=ref.url,
                      company=record.company_name, original={"board_token": ref.original.get("board_token")})
        try:
            raw = adapter.fetch_details(stub)
        except JobGone:
            self.jobs.update_job(job_id, {"status": "closed", "last_updated_at": self.engine.now_fn()})
            return {"action": "closed", "job": self.jobs.get_job(job_id)}
        outcome = self.engine.ingest(raw, adapter, None, SourceStats(), manual=True)
        return {"action": outcome["action"], "job": self.jobs.get_job(job_id)}
