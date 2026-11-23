"""Lever: the public Postings API (published postings of a company site; no login).

Only sites the user listed in config/career_sources.yaml are read.
"""
import logging

from integrations.base_adapter import AdapterError, AdapterResult, JobAdapter, JobGone, PolicyHttp, RawJob, SearchQuery
from integrations.career_pages.ats_detector import safe_token
from integrations.career_pages.providers.greenhouse import parse_json
from integrations.html_text import html_to_text
from integrations.policy import Policy

logger = logging.getLogger(__name__)
API = "https://api.lever.co/v0/postings"
_WORKPLACE = {"remote": "remote", "hybrid": "hybrid", "on-site": "onsite", "onsite": "onsite", "on site": "onsite"}


class LeverAdapter(JobAdapter):
    name = "lever"

    def __init__(self, policy: Policy, http: PolicyHttp, companies=()):
        super().__init__(policy, http)
        self.companies = list(companies)

    def search(self, query: SearchQuery) -> AdapterResult:
        self.require_discovery()
        result = AdapterResult(source=self.name)
        for c in self.companies:
            result.jobs += self.list_company(c, result)
        result.requests_made = self.http.limiter.count
        return result

    def list_company(self, company, result: AdapterResult | None = None) -> list[RawJob]:
        self.require_discovery()
        slug = safe_token(company.board_token)
        if not slug:
            raise AdapterError(f"{company.name}: no valid Lever site configured")
        resp = self.http.get(f"{API}/{slug}", params={"mode": "json"})
        if resp.status_code == 404:
            if result is not None:
                result.notes.append(f"{company.name}: Lever site '{slug}' not found")
            return []
        data = parse_json(resp)
        if not isinstance(data, list):
            raise AdapterError("Unexpected Lever response shape")
        return [self.to_raw(i, company=company.name, token=slug) for i in data if isinstance(i, dict)]

    def fetch_details(self, job: RawJob) -> RawJob:
        self.require_discovery()
        slug = safe_token(job.original.get("board_token"))
        if not slug or not job.source_job_id or not safe_token(job.source_job_id):
            raise AdapterError("Cannot refresh: missing Lever site or posting id")
        resp = self.http.get(f"{API}/{slug}/{job.source_job_id}", params={"mode": "json"})
        if resp.status_code == 404:
            raise JobGone("The job is no longer listed (HTTP 404)")
        data = parse_json(resp)
        if not isinstance(data, dict):
            raise AdapterError("Unexpected Lever response shape")
        return self.to_raw(data, company=job.company, token=slug)

    def to_raw(self, payload: dict, company: str | None = None, token: str | None = None, **_) -> RawJob:
        cats = payload.get("categories") or {}
        parts = [payload.get("descriptionPlain") or html_to_text(payload.get("description"))]
        for block in payload.get("lists") or []:
            if isinstance(block, dict):
                parts.append(f"{block.get('text', '')}:\n{html_to_text(block.get('content'))}")
        parts.append(payload.get("additionalPlain") or html_to_text(payload.get("additional")))
        description = "\n\n".join(p for p in parts if p and p.strip()) or None
        locations = cats.get("allLocations") if isinstance(cats.get("allLocations"), list) else None
        locations = locations or ([cats["location"]] if cats.get("location") else [])
        sal = payload.get("salaryRange") if isinstance(payload.get("salaryRange"), dict) else {}
        annual = str(sal.get("interval", "")).lower() in ("per-year-salary", "per-year", "yearly", "annual")
        created = payload.get("createdAt")
        return RawJob(
            source="company_career_page", provider="lever", source_job_id=payload.get("id"),
            url=payload.get("hostedUrl"), title=payload.get("text"), company=company,
            description=description, location_text=[l for l in locations if isinstance(l, str)],
            employment_text=cats.get("commitment"), department=cats.get("team") or cats.get("department"),
            work_mode_text=_WORKPLACE.get(str(payload.get("workplaceType", "")).lower()),
            salary_min=sal.get("min") if annual else None, salary_max=sal.get("max") if annual else None,
            salary_currency=sal.get("currency") if annual else None,
            posted_raw=created, original={"board_token": token, "commitment": cats.get("commitment"),
                                         "workplaceType": payload.get("workplaceType")},
        )
