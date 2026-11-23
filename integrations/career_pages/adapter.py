"""Company career pages: dispatches each user-listed company to the right (permitted) provider adapter."""
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, field_validator

from config.yaml_cache import load_yaml
from config.settings import Settings
from integrations.base_adapter import (
    AdapterError,
    AdapterResult,
    JobAdapter,
    PolicyHttp,
    RawJob,
    SearchQuery,
    SourceBlocked,
    public_http_url,
)
from integrations.career_pages.ats_detector import MIN_CONFIDENCE, detect, safe_token
from integrations.career_pages.providers.generic_form import GenericCareerPageAdapter
from integrations.career_pages.providers.greenhouse import GreenhouseAdapter
from integrations.career_pages.providers.lever import LeverAdapter
from integrations.career_pages.providers.workday import WorkdayAdapter
from integrations.policy import Policy, PolicyRegistry, PolicyViolation

logger = logging.getLogger(__name__)
PROVIDER_POLICY = {"greenhouse": "greenhouse", "lever": "lever", "workday": "workday", "generic": "generic_page"}


class CompanySource(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    name: str
    ats: Literal["greenhouse", "lever", "workday", "generic"] | None = None
    board_token: str | None = None
    careers_url: str | None = None
    approved: bool = False
    sitemap: bool = False

    @field_validator("name")
    @classmethod
    def _name(cls, v):
        if not v:
            raise ValueError("company name is required")
        return v

    @field_validator("board_token")
    @classmethod
    def _token(cls, v):
        if v is not None and safe_token(v) is None:
            raise ValueError("board_token may only contain letters, digits, '-' and '_'")
        return v

    @field_validator("careers_url")
    @classmethod
    def _url(cls, v):
        if v is not None and (public_http_url(v) is None or not v.lower().startswith("https://")):
            raise ValueError("careers_url must be a public https URL")
        return v


def load_company_sources(settings: Settings | None = None, path: Path | None = None) -> list[CompanySource]:
    path = path or (settings or Settings()).config_path("career_sources_file")
    try:
        data = load_yaml(path) or {}
    except Exception as exc:
        logger.error("career_sources unreadable (%s); no companies configured", type(exc).__name__)
        return []
    out = []
    for entry in data.get("companies") or []:
        try:
            out.append(CompanySource.model_validate(entry))
        except Exception:
            logger.warning("Ignoring an invalid career_sources entry")
    return out


HttpFactory = Callable[[Policy, tuple[str, ...]], PolicyHttp]


def title_wanted(title: str | None, wanted: list[str]) -> bool:
    """The discovery engine's own title test (``matches_query``), applied early. It must never drop a job the engine would keep."""
    from engines.discovery.deduplication import title_key  # imported here: engines.discovery imports this package
    from engines.discovery.job_normalizer import clean_title

    cleaned = clean_title(title)
    if not cleaned:
        return False
    key = title_key(cleaned)
    return any(title_key(t) in key or t.lower() in cleaned.lower() for t in wanted)


class CareerPagesAdapter(JobAdapter):
    name = "career_pages"

    def __init__(self, policy: Policy, registry: PolicyRegistry, http_factory: HttpFactory, companies: list[CompanySource],
                 user_agent: str = "AI-JobPilot"):
        super().__init__(policy, None)
        self.registry, self.http_factory, self.companies, self.user_agent = registry, http_factory, companies, user_agent
        self._providers: dict[tuple[str, tuple[str, ...]], JobAdapter] = {}

    # ---- provider resolution (detection is separate from extraction) ----
    def provider_for(self, company: CompanySource) -> tuple[str, list[str]]:
        notes = []
        if company.ats:
            return company.ats, notes
        det = detect(company.careers_url)
        if det.supported and det.provider != "generic":
            if det.board_token and not company.board_token:
                company.board_token = det.board_token
            return det.provider, notes
        if company.careers_url:
            return "generic", notes  # not a known ATS URL: only usable if the user approved the page
        notes.append(f"{company.name}: provider could not be identified (confidence {det.confidence:.2f}); unsupported")
        return "unsupported", notes

    def _adapter(self, provider: str, company: CompanySource) -> JobAdapter | None:
        pol = self.registry.get("career_pages", PROVIDER_POLICY[provider])
        hosts = (urlparse(company.careers_url).hostname,) if provider == "generic" and company.careers_url else ()
        key = (provider, hosts)
        if key not in self._providers:
            if provider == "workday":
                self._providers[key] = WorkdayAdapter(pol)
            else:
                http = self.http_factory(pol, hosts)
                cls = {"greenhouse": GreenhouseAdapter, "lever": LeverAdapter}.get(provider)
                self._providers[key] = cls(pol, http) if cls else GenericCareerPageAdapter(pol, http, user_agent=self.user_agent)
        return self._providers[key]

    def search(self, query: SearchQuery) -> AdapterResult:
        self.require_discovery()
        result = AdapterResult(source=self.name)
        wanted = {c.lower() for c in query.companies}
        if not self.companies:
            result.notes.append("No companies are configured in config/career_sources.yaml - nothing to discover")
        for company in self.companies:
            if wanted and company.name.lower() not in wanted:
                continue
            provider, notes = self.provider_for(company)
            result.notes += notes
            if provider == "unsupported":
                continue
            adapter = self._adapter(provider, company)
            try:
                adapter.require_discovery()
                jobs = adapter.list_company(company, result) if hasattr(adapter, "list_company") else []
                if query.titles:  # drop wrong titles BEFORE the cap, or one large board uses the whole run's allowance
                    kept = [j for j in jobs if title_wanted(j.title, query.titles)]
                    result.prefiltered += len(jobs) - len(kept)
                    if len(kept) < len(jobs):
                        result.notes.append(f"{company.name}: {len(jobs) - len(kept)} of {len(jobs)} job(s) skipped by the title filter")
                    jobs = kept
                result.jobs += jobs
            except PolicyViolation as exc:
                result.notes.append(f"{company.name}: {exc}")
            except SourceBlocked as exc:
                result.blocked = True
                result.errors.append(f"{company.name}: {exc}")
            except AdapterError as exc:
                result.errors.append(f"{company.name}: {exc}")
            # No early stop here: these counts are BEFORE the location / age / work-mode filters, so stopping at the cap let the first
            # companies' worldwide listings crowd out every company later in the list. The engine applies the per-run cap to jobs that pass
            # your filters; each company is still one listing request (bounded by max_requests_per_run).
        result.requests_made = sum(getattr(a.http.limiter, "count", 0) for a in self._providers.values() if getattr(a, "http", None))
        return result

    def fetch_details(self, job: RawJob) -> RawJob:
        self.require_discovery()
        provider = {"greenhouse": "greenhouse", "lever": "lever", "generic": "generic"}.get(job.provider or "")
        if not provider:
            raise PolicyViolation("This job's provider does not support automated refresh")
        stub = CompanySource(name=job.company or "company", careers_url=job.url if provider == "generic" else None)
        adapter = self._adapter(provider, stub)
        return adapter.fetch_details(job)

    def to_raw(self, payload: dict, **context) -> RawJob:
        provider = context.get("provider")
        stub = CompanySource(name=context.get("company") or "company")
        return self._adapter(provider, stub).to_raw(payload, **context)

    def metadata(self) -> dict:
        m = super().metadata()
        m["companies_configured"] = len(self.companies)
        m["providers"] = {p: self.registry.get("career_pages", pp).status.value for p, pp in PROVIDER_POLICY.items()}
        return m
