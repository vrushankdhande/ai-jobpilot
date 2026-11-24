"""Common interface for every job source + the policy-enforcing HTTP layer.

Rules baked in here (adapters cannot opt out):
* every request passes the platform policy, a host allow-list, a rate limiter and a request budget;
* a 401/403/429 or a CAPTCHA/"verify you are human" page marks the source BLOCKED - the run stops for that
  source; there are no retries, no header tricks, no proxies, no logins (nothing is ever bypassed);
* private/loopback addresses are refused (no SSRF);
* error messages are sanitised - never URLs with credentials, never raw response bodies.
"""
import ipaddress
import logging
import re
import time
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field

from integrations.policy import Policy, PolicyViolation

logger = logging.getLogger(__name__)

WORK_MODES = ("remote", "hybrid", "onsite")


class AdapterError(Exception):
    """A source failed. The message is safe to show to users."""


class SourceBlocked(AdapterError):
    """The source refused us (401/403/429/CAPTCHA). Never retried or worked around."""


class JobGone(AdapterError):
    """The posting no longer exists at the source (HTTP 404 on refresh)."""


class RateLimitExceeded(AdapterError):
    """The configured request budget for this run is used up (or the rate is configured as 0)."""


# ---- data contract ---------------------------------------------------------------------------------
class SearchQuery(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")

    titles: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    companies: list[str] = Field(default_factory=list)
    remote_within_locations: bool = False                        # a remote job that names a place must be in `locations`
    work_modes: list[str] = Field(default_factory=list)          # remote | hybrid | onsite
    employment_types: list[str] = Field(default_factory=list)    # full-time | part-time | contract | internship | ...
    min_salary: float | None = Field(default=None, ge=0)
    salary_currency: str | None = None
    experience_min: float | None = Field(default=None, ge=0)
    experience_max: float | None = Field(default=None, ge=0)
    posted_within_days: int | None = Field(default=None, ge=1, le=3650)
    keywords_include: list[str] = Field(default_factory=list)
    keywords_exclude: list[str] = Field(default_factory=list)
    max_jobs: int | None = Field(default=None, ge=1, le=5000)


class RawJob(BaseModel):
    """A source response mapped to one common shape (plain text, no normalisation yet)."""

    model_config = ConfigDict(extra="ignore")

    source: str
    provider: str | None = None
    source_job_id: str | None = None
    url: str | None = None
    title: str | None = None
    company: str | None = None
    description: str | None = None
    location_text: list[str] = Field(default_factory=list)
    employment_text: str | None = None
    work_mode_text: str | None = None
    department: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    salary_text: str | None = None
    posted_raw: Any = None
    updated_raw: Any = None
    original: dict = Field(default_factory=dict)  # source values kept for traceability


class AdapterResult(BaseModel):
    source: str
    jobs: list[RawJob] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    requests_made: int = 0
    blocked: bool = False
    prefiltered: int = 0   # postings dropped by the adapter's early title filter; counted as "filtered out" in the run totals


# ---- safety helpers -----------------------------------------------------------------------------------
def is_private_host(host: str | None) -> bool:
    if not host:
        return True
    host = host.strip("[]").lower()
    if host in ("localhost", "localhost.localdomain") or host.endswith((".local", ".internal", ".localhost")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified


def public_http_url(url: str | None) -> str | None:
    """Return the URL if it is a plain http(s) URL to a public host, else None."""
    if not url:
        return None
    try:
        p = urlparse(url.strip())
    except ValueError:
        return None
    if p.scheme not in ("http", "https") or not p.hostname or is_private_host(p.hostname) or p.username or p.password:
        return None
    return url.strip()


def host_allowed(host: str, allowed: list[str] | tuple[str, ...]) -> bool:
    host = host.lower()
    return any(host == a.lower() or host.endswith("." + a.lower()) for a in allowed)


# ---- rate limiting ----------------------------------------------------------------------------------------
class RateLimiter:
    """Spaces requests at ``requests_per_minute`` and enforces a per-run request budget."""

    def __init__(self, requests_per_minute: float, max_requests: int = 0, clock=time.monotonic, sleep=time.sleep):
        self.rpm, self.max_requests = requests_per_minute, max_requests
        self._clock, self._sleep = clock, sleep
        self._last: float | None = None
        self.count = 0

    def acquire(self) -> None:
        if self.rpm <= 0:
            raise RateLimitExceeded("Requests are not permitted for this source (rate limit is 0)")
        if self.max_requests and self.count >= self.max_requests:
            raise RateLimitExceeded("Request budget for this run is used up")
        if self._last is not None:
            wait = self._last + 60.0 / self.rpm - self._clock()
            if wait > 0:
                self._sleep(wait)
        self._last = self._clock()
        self.count += 1


_BLOCK_MARKERS = re.compile(
    r"captcha|are you a robot|verify (?:that )?you are (?:a )?human|unusual traffic|access denied|cf-chl|"
    r"enable javascript and cookies to continue|please sign in to continue|authwall", re.I)


class PolicyHttp:
    """The ONLY way adapters touch the network."""

    def __init__(self, policy: Policy, client: httpx.Client, limiter: RateLimiter, user_agent: str,
                 extra_hosts: tuple[str, ...] = (), max_bytes: int = 5_000_000, timeout: float = 15.0):
        self.policy, self.client, self.limiter, self.user_agent = policy, client, limiter, user_agent
        self.extra_hosts, self.max_bytes, self.timeout = extra_hosts, max_bytes, timeout

    def _check(self, url: str) -> None:
        if not self.policy.automated_allowed:
            raise PolicyViolation(self.policy.deny_reason())
        p = urlparse(url)
        if p.scheme != "https" or not p.hostname or p.username or p.password:
            raise PolicyViolation("Only plain https URLs are allowed")
        if is_private_host(p.hostname):
            raise PolicyViolation("Requests to private or local addresses are not allowed")
        if not host_allowed(p.hostname, list(self.policy.allowed_hosts) + list(self.extra_hosts)):
            raise PolicyViolation(f"Host '{p.hostname}' is not on the allow-list for {self.policy.name}")

    def get(self, url: str, params: dict | None = None, accept: str = "application/json", headers: dict | None = None) -> httpx.Response:
        """``headers`` adds request headers (e.g. an OAuth bearer token for an official API); it cannot replace the User-Agent."""
        for _hop in range(4):  # follow at most 3 redirects, re-validating every hop
            self._check(url)
            self.limiter.acquire()
            try:
                resp = self.client.get(url, params=params, timeout=self.timeout, follow_redirects=False,
                                       headers={**(headers or {}), "User-Agent": self.user_agent, "Accept": accept})
            except httpx.TimeoutException:
                raise AdapterError("The source timed out") from None
            except httpx.HTTPError:
                raise AdapterError("Network error while contacting the source") from None
            if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                url, params = str(httpx.URL(url).join(resp.headers["location"])), None
                continue
            break
        else:
            raise AdapterError("Too many redirects")
        if resp.status_code in (401, 403, 429, 451, 999):
            raise SourceBlocked(f"The source refused the request (HTTP {resp.status_code}); stopping for this source")
        if resp.status_code >= 500:
            raise AdapterError(f"The source returned a server error (HTTP {resp.status_code})")
        if len(resp.content) > self.max_bytes:
            raise AdapterError("Response too large; ignored")
        if "html" in resp.headers.get("content-type", "") and _BLOCK_MARKERS.search(resp.text[:20000]):
            raise SourceBlocked("The source presented a bot-check or login wall; stopping for this source")
        return resp


# ---- adapter interface ----------------------------------------------------------------------------------------
class JobAdapter(ABC):
    """Every job source implements this. ``name`` is the key in config/platform_policies.yaml."""

    name: str = ""
    automated_discovery_supported: bool = True  # False = no authorised automated method exists in code

    def __init__(self, policy: Policy, http: PolicyHttp | None = None):
        self.policy = policy
        self.http = http

    # --- permission gates (fail closed) ---
    def require_discovery(self) -> None:
        if not self.policy.automated_allowed:  # the policy reason is the most useful one to show
            raise PolicyViolation(self.policy.deny_reason())
        if not self.automated_discovery_supported:
            raise PolicyViolation(f"{self.name}: no authorised automated method is implemented; import jobs manually")

    def require_manual(self) -> None:
        if not self.policy.manual_allowed:
            raise PolicyViolation(f"{self.name}: manual import is not enabled for this source")

    # --- interface ---
    @abstractmethod
    def search(self, query: SearchQuery) -> AdapterResult:
        """Discover jobs. Must call require_discovery() first."""

    @abstractmethod
    def fetch_details(self, job: RawJob) -> RawJob:
        """Fetch/complete one job's details. Must call require_discovery() first."""

    @abstractmethod
    def to_raw(self, payload: dict, **context) -> RawJob:
        """Map one source response item to the common RawJob format."""

    def validate(self, job: RawJob) -> list[str]:
        """Problems that make a record unusable (empty = ok)."""
        problems = []
        if not (job.title and job.title.strip()):
            problems.append("missing title")
        if not (job.url or job.source_job_id):
            problems.append("missing url and source id")
        if job.url and public_http_url(job.url) is None:
            problems.append("url is not a public http(s) URL")
        return problems

    def import_manual(self, url: str | None, fields: dict | None = None) -> RawJob:
        """User-directed import: builds a record from what the user supplied. Never fetches anything."""
        raise PolicyViolation(f"{self.name}: manual import is not supported")

    def metadata(self) -> dict:
        return {"source": self.name, "status": self.policy.status.value, "automated_discovery": self.policy.automated_allowed
                and self.automated_discovery_supported, "manual_import": self.policy.manual_allowed,
                "requests_per_minute": self.policy.requests_per_minute}


# ---- application adapters (Phase 6) -------------------------------------------------------------------------------
class ApplicationAdapter(ABC):
    """How to APPLY on one platform / ATS provider. Never touches the network itself: the engine drives the browser, the adapter only
    supplies provider knowledge (which URL to open, what a confirmation looks like) and enforces the application policy.

    ``automation_supported = False`` providers (LinkedIn, Indeed, Naukri, Workday ...) can only be handed off to the candidate.
    """

    provider: str = ""
    source: str = ""                       # key in config/application_policies.yaml
    automation_supported: bool = True      # False = no authorised automated method exists in code, whatever the YAML says
    confirmation_patterns: tuple[str, ...] = (r"application (?:has been )?(?:submitted|received|sent)", r"thank you for (?:applying|your application)",
                                              r"thanks for applying", r"we(?:'ve| have) received your application", r"successfully (?:submitted|applied)")
    # "Application reference: APP-1", "Confirmation number: 123456", "Reference ID #A12B", "Your application number is 99812"
    reference_patterns: tuple[str, ...] = (
        r"\b(?:application|confirmation|tracking|reference)\s*(?:ref(?:erence)?|id|number|no\.?|code|#)?\s*(?:is\s*)?[:#]\s*([A-Za-z0-9][A-Za-z0-9\-_/]{3,39})",
        r"\b(?:application|confirmation|reference)\s*(?:id|number|no\.?|code)\s+is\s+([A-Za-z0-9][A-Za-z0-9\-_/]{3,39})",
    )
    confirmation_url_patterns: tuple[str, ...] = (r"/(?:confirm(?:ation)?|thank-?you|thanks|submitted|success)\b",)

    def __init__(self, decision):
        self.decision = decision           # integrations.application_policy.ApplicationDecision

    def require_automation(self) -> None:
        if not self.automation_supported:
            raise PolicyViolation(f"{self.provider or self.source}: no authorised automated application method is implemented; "
                                  "use manual handoff")
        if not self.decision.automation_allowed:
            raise PolicyViolation(self.decision.reason)

    def application_url(self, job_url: str) -> str:
        """The page that shows the application form (default: the job URL itself)."""
        return job_url

    def metadata(self) -> dict:
        return {"provider": self.provider, "source": self.source, "automation_supported": self.automation_supported,
                "automation_allowed": self.automation_supported and self.decision.automation_allowed, "status": self.decision.status,
                "reason": self.decision.reason}


class ManualOnlyAdapter(JobAdapter):
    """For platforms that do not permit automated collection (LinkedIn, Indeed, Naukri).

    There is deliberately NO network code here: discovery always raises PolicyViolation, whatever the YAML says.
    """

    automated_discovery_supported = False
    host_suffixes: tuple[str, ...] = ()
    id_patterns: tuple[str, ...] = ()

    def search(self, query: SearchQuery) -> AdapterResult:
        self.require_discovery()
        raise PolicyViolation("unreachable")  # pragma: no cover

    def fetch_details(self, job: RawJob) -> RawJob:
        self.require_discovery()
        raise PolicyViolation("unreachable")  # pragma: no cover

    def to_raw(self, payload: dict, **context) -> RawJob:
        return self._build(context.get("url"), payload)

    def job_id_from_url(self, url: str) -> str | None:
        for pat in self.id_patterns:
            m = re.search(pat, url)
            if m:
                return m.group(1)
        return None

    def _build(self, url: str | None, f: dict) -> RawJob:
        loc = f.get("location")
        return RawJob(
            source=self.name, source_job_id=self.job_id_from_url(url) if url else None, url=url,
            title=f.get("job_title"), company=f.get("company_name"), description=f.get("job_description"),
            location_text=[loc] if isinstance(loc, str) and loc else list(loc or []),
            employment_text=f.get("employment_type"), work_mode_text=f.get("work_mode"), salary_text=f.get("salary"),
            posted_raw=f.get("posted_date"), original={"manual_import": True, "experience_requirement": f.get("experience_requirement")},
        )

    def import_manual(self, url: str | None, fields: dict | None = None) -> RawJob:
        self.require_manual()
        fields = fields or {}
        if url:
            p = urlparse(url.strip())
            if p.scheme not in ("http", "https") or not p.hostname or is_private_host(p.hostname) or p.username or p.password:
                raise ValueError("The URL must be a public http(s) address")
            if self.host_suffixes and not host_allowed(p.hostname, list(self.host_suffixes)):
                raise ValueError(f"This URL does not belong to {self.name}")
        elif not (fields.get("job_title") or fields.get("job_description")):
            raise ValueError("Provide a job URL or the job details")
        return self._build(url.strip() if url else None, fields)
