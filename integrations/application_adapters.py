"""Application adapters: Greenhouse, Lever, Workday, generic forms, the local test provider, and manual-only platforms.

Which adapter applies is decided from the job's source and its application URL (no request is made). Whether it may AUTOMATE is
decided by ``integrations.application_policy`` (default: no - manual handoff). Workday and the social/job-board platforms have
``automation_supported = False``: they need a sign-in or forbid automation, so they are handoff-only whatever the YAML says.
"""
from urllib.parse import urlparse, urlunparse

from integrations.application_policy import ApplicationDecision, ApplicationPolicy
from integrations.base_adapter import ApplicationAdapter
from integrations.career_pages.ats_detector import detect

MANUAL_SOURCES = ("linkedin", "indeed", "naukri", "instahyre", "manual", "manual_paste")


class GreenhouseApplicationAdapter(ApplicationAdapter):
    provider, source = "greenhouse", "career_pages"
    # The public boards use reCAPTCHA: the engine stops at it and hands the application to the candidate.


class LeverApplicationAdapter(ApplicationAdapter):
    provider, source = "lever", "career_pages"

    def application_url(self, job_url: str) -> str:
        p = urlparse(job_url)
        return job_url if p.path.rstrip("/").endswith("/apply") else urlunparse(p._replace(path=p.path.rstrip("/") + "/apply", query="", fragment=""))


class WorkdayApplicationAdapter(ApplicationAdapter):
    provider, source = "workday", "career_pages"
    automation_supported = False           # employer-specific candidate account / sign-in: automated login is out of scope


class GenericFormAdapter(ApplicationAdapter):
    provider, source = "generic_page", "career_pages"


class LocalTestAdapter(ApplicationAdapter):
    provider, source = "local_test", "local_test"


class ManualApplicationAdapter(ApplicationAdapter):
    automation_supported = False

    def __init__(self, decision, source: str):
        super().__init__(decision)
        self.provider, self.source = source, source


_PROVIDERS = {"greenhouse": GreenhouseApplicationAdapter, "lever": LeverApplicationAdapter, "workday": WorkdayApplicationAdapter}


def resolve_application_adapter(job: dict, policy: ApplicationPolicy) -> ApplicationAdapter:
    """Pick the adapter (and policy decision) for a stored job. Anything unrecognised becomes a manual handoff."""
    source = (job.get("source") or "manual").lower()
    url = job.get("job_url") or next((r.get("url") for r in job.get("source_refs") or [] if r.get("url")), None)
    if source == "local_test":
        return LocalTestAdapter(policy.decide("local_test", None, url))
    if source in MANUAL_SOURCES:
        key = "manual" if source == "manual_paste" else source
        return ManualApplicationAdapter(policy.decide(key, None, url), key)
    # career pages (or any other source): identify the ATS from the URL only
    det = detect(url)
    provider = det.provider if det.supported else None
    if provider is None:  # fall back to what discovery recorded
        provider = next((r.get("provider") for r in job.get("source_refs") or [] if r.get("provider") in _PROVIDERS), None)
    if provider in _PROVIDERS:
        return _PROVIDERS[provider](policy.decide("career_pages", provider, url))
    return GenericFormAdapter(policy.decide("career_pages", "generic_page", url))
