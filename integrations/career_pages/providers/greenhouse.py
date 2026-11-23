"""Greenhouse: the public Job Board API (documented GET endpoints for a company's published jobs; no login).

Only boards the user listed in config/career_sources.yaml are read.
"""
import json
import logging
import re

from integrations.base_adapter import AdapterError, AdapterResult, JobAdapter, JobGone, PolicyHttp, RawJob, SearchQuery
from integrations.career_pages.ats_detector import safe_token
from integrations.html_text import html_to_text
from integrations.policy import Policy

logger = logging.getLogger(__name__)
API = "https://boards-api.greenhouse.io/v1/boards"


def parse_json(resp) -> dict | list:
    try:
        return resp.json()
    except (json.JSONDecodeError, ValueError):
        raise AdapterError("The source returned data that is not valid JSON") from None


def _board_name(board: str | None, configured: str | None) -> str | None:
    """Greenhouse sometimes names the BOARD ("Rubrik Job Board", "Acme Careers") instead of the company: then the name you configured wins."""
    if board and configured and re.search(r"\b(?:job board|jobs board|careers?|jobs)\s*$", board, re.I):
        return configured
    return board or configured

class GreenhouseAdapter(JobAdapter):
    name = "greenhouse"

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
        token = safe_token(company.board_token)
        if not token:
            raise AdapterError(f"{company.name}: no valid Greenhouse board token configured")
        resp = self.http.get(f"{API}/{token}/jobs", params={"content": "true"})
        if resp.status_code == 404:
            if result is not None:
                result.notes.append(f"{company.name}: Greenhouse board '{token}' not found")
            return []
        data = parse_json(resp)
        items = data.get("jobs") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise AdapterError("Unexpected Greenhouse response shape")
        return [self.to_raw(i, company=company.name, token=token) for i in items if isinstance(i, dict)]

    def fetch_details(self, job: RawJob) -> RawJob:
        self.require_discovery()
        token = safe_token(job.original.get("board_token"))
        if not token or not job.source_job_id or not str(job.source_job_id).isdigit():
            raise AdapterError("Cannot refresh: missing Greenhouse board token or job id")
        resp = self.http.get(f"{API}/{token}/jobs/{job.source_job_id}")
        if resp.status_code == 404:
            raise JobGone("The job is no longer listed (HTTP 404)")
        data = parse_json(resp)
        if not isinstance(data, dict):
            raise AdapterError("Unexpected Greenhouse response shape")
        return self.to_raw(data, company=job.company, token=token)

    def to_raw(self, payload: dict, company: str | None = None, token: str | None = None, **_) -> RawJob:
        loc = payload.get("location") or {}
        offices = [o.get("name") or (o.get("location") or "") for o in payload.get("offices") or [] if isinstance(o, dict)]
        locations = [loc.get("name")] if isinstance(loc, dict) and loc.get("name") else []
        depts = [d.get("name") for d in payload.get("departments") or [] if isinstance(d, dict) and d.get("name")]
        return RawJob(
            source="company_career_page", provider="greenhouse",
            source_job_id=str(payload["id"]) if payload.get("id") is not None else None,
            url=payload.get("absolute_url"), title=payload.get("title"),
            company=_board_name(payload.get("company_name"), company),
            description=html_to_text(payload.get("content")) or None,
            location_text=locations or [o for o in offices if o],
            department=depts[0] if depts else None,
            posted_raw=payload.get("first_published") or payload.get("updated_at"),
            updated_raw=payload.get("updated_at"),
            original={"board_token": token, "location": loc.get("name") if isinstance(loc, dict) else None,
                      "requisition_id": payload.get("requisition_id"), "updated_at": payload.get("updated_at")},
        )
