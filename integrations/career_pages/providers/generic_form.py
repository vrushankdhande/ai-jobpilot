"""Generic career pages: only structured schema.org ``JobPosting`` data (JSON-LD), only on pages the user approved,
only where robots.txt allows (fails closed when robots.txt cannot be read). No guessing from arbitrary page layout.
"""
import json
import logging
import re
from urllib.parse import urljoin, urlparse

from integrations.base_adapter import AdapterError, AdapterResult, JobAdapter, PolicyHttp, RawJob, SearchQuery
from integrations.html_text import html_to_text
from integrations.policy import Policy, PolicyViolation

logger = logging.getLogger(__name__)
_LD = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.I | re.S)
_JOB_LINK = re.compile(r"/(?:jobs?|careers?|positions?|openings?|vacanc\w+)/[^\"'#?\s]+", re.I)
MAX_PAGES = 25


class RobotsGate:
    """robots.txt check (cached per host). Unreadable robots.txt => disallow (fail closed)."""

    def __init__(self, http: PolicyHttp, user_agent: str):
        self.http, self.ua = http, user_agent
        self._cache: dict[str, object] = {}

    def allowed(self, url: str) -> bool:
        from urllib.robotparser import RobotFileParser

        host = urlparse(url).hostname or ""
        if host not in self._cache:
            try:
                resp = self.http.get(f"https://{host}/robots.txt", accept="text/plain")
                if resp.status_code in (404, 410):
                    self._cache[host] = True  # no robots.txt -> nothing is disallowed (RFC 9309)
                elif resp.status_code == 200:
                    rp = RobotFileParser()
                    rp.parse(resp.text.splitlines())
                    self._cache[host] = rp
                else:
                    self._cache[host] = False
            except Exception:  # blocked, timeout, 5xx, ... -> fail closed
                self._cache[host] = False
        gate = self._cache[host]
        return gate if isinstance(gate, bool) else gate.can_fetch(self.ua, url)


def extract_job_postings(html: str) -> list[dict]:
    """All JobPosting nodes in the page's JSON-LD blocks."""
    out: list[dict] = []

    def walk(node):
        if isinstance(node, list):
            for n in node:
                walk(n)
        elif isinstance(node, dict):
            t = node.get("@type")
            types = t if isinstance(t, list) else [t]
            if "JobPosting" in types:
                out.append(node)
            for key in ("@graph", "itemListElement", "item"):
                if key in node:
                    walk(node[key])

    for block in _LD.findall(html or ""):
        try:
            walk(json.loads(block.strip()))
        except (json.JSONDecodeError, ValueError):
            continue
    return out


def _s(v) -> str | None:
    if isinstance(v, dict):
        v = v.get("name") or v.get("value")
    return str(v).strip() if v not in (None, "") else None


class GenericCareerPageAdapter(JobAdapter):
    name = "generic_page"

    def __init__(self, policy: Policy, http: PolicyHttp, companies=(), user_agent: str = "AI-JobPilot"):
        super().__init__(policy, http)
        self.companies = list(companies)
        self.robots = RobotsGate(http, user_agent)

    def search(self, query: SearchQuery) -> AdapterResult:
        self.require_discovery()
        result = AdapterResult(source=self.name)
        for c in self.companies:
            result.jobs += self.list_company(c, result)
        result.requests_made = self.http.limiter.count
        return result

    def list_company(self, company, result: AdapterResult | None = None) -> list[RawJob]:
        self.require_discovery()
        notes = result.notes if result is not None else []
        if not company.approved:
            notes.append(f"{company.name}: generic page not approved (set approved: true after checking the site's terms)")
            return []
        if not company.careers_url:
            notes.append(f"{company.name}: no careers_url configured")
            return []
        base = company.careers_url
        if not self.robots.allowed(base):
            notes.append(f"{company.name}: robots.txt does not allow (or could not be read for) {urlparse(base).hostname}")
            return []
        pages = self._sitemap_urls(company) if company.sitemap else None
        jobs: list[RawJob] = []
        if pages is None:
            resp = self.http.get(base, accept="text/html")
            html = resp.text
            jobs += [self.to_raw(p, company=company.name, page_url=base) for p in extract_job_postings(html)]
            host = urlparse(base).hostname
            links = []
            for m in _JOB_LINK.finditer(html):
                u = urljoin(base, m.group(0))
                if urlparse(u).hostname == host and u not in links and u != base:
                    links.append(u)
            pages = links
        for url in pages[:MAX_PAGES]:
            if urlparse(url).hostname != urlparse(base).hostname or not self.robots.allowed(url):
                continue
            found = extract_job_postings(self.http.get(url, accept="text/html").text)
            if not found:
                notes.append(f"{company.name}: no structured JobPosting data on {urlparse(url).path[:60]}")
            jobs += [self.to_raw(p, company=company.name, page_url=url) for p in found]
        return jobs

    def _sitemap_urls(self, company) -> list[str]:
        host = urlparse(company.careers_url).hostname
        url = f"https://{host}/sitemap.xml"
        if not self.robots.allowed(url):
            return []
        locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", self.http.get(url, accept="application/xml").text)
        return [u for u in locs if _JOB_LINK.search(urlparse(u).path or "")][:MAX_PAGES]

    def fetch_details(self, job: RawJob) -> RawJob:
        self.require_discovery()
        if not job.url:
            raise AdapterError("Cannot refresh: the job has no URL")
        if not self.robots.allowed(job.url):
            raise PolicyViolation("robots.txt does not allow refreshing this page")
        found = extract_job_postings(self.http.get(job.url, accept="text/html").text)
        if not found:
            raise AdapterError("No structured job data found on the page any more")
        return self.to_raw(found[0], company=job.company, page_url=job.url)

    def to_raw(self, payload: dict, company: str | None = None, page_url: str | None = None, **_) -> RawJob:
        locations, remote = [], str(payload.get("jobLocationType", "")).upper() == "TELECOMMUTE"
        locs = payload.get("jobLocation")
        for loc in locs if isinstance(locs, list) else [locs] if locs else []:
            addr = loc.get("address") if isinstance(loc, dict) else None
            if isinstance(addr, dict):
                parts = [_s(addr.get("addressLocality")), _s(addr.get("addressRegion")), _s(addr.get("addressCountry"))]
                text = ", ".join(p for p in parts if p)
            else:
                text = _s(addr)
            if text:
                locations.append(text)
        emp = payload.get("employmentType")
        emp = ", ".join(emp) if isinstance(emp, list) else _s(emp)
        sal = payload.get("baseSalary") if isinstance(payload.get("baseSalary"), dict) else {}
        val = sal.get("value") if isinstance(sal.get("value"), dict) else {}
        annual = str(val.get("unitText", "")).upper() in ("YEAR", "ANNUAL", "YEARLY")
        ident = payload.get("identifier")
        return RawJob(
            source="company_career_page", provider="generic", source_job_id=_s(ident),
            url=page_url or _s(payload.get("url")), title=_s(payload.get("title")),
            company=_s(payload.get("hiringOrganization")) or company, description=html_to_text(payload.get("description")) or None,
            location_text=locations, employment_text=emp, work_mode_text="remote" if remote else None,
            salary_min=val.get("minValue") if annual else None, salary_max=val.get("maxValue") if annual else None,
            salary_currency=_s(sal.get("currency")) if annual else None,
            posted_raw=_s(payload.get("datePosted")), updated_raw=None,
            original={"valid_through": _s(payload.get("validThrough")), "employment_type": emp},
        )
