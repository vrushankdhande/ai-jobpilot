"""Builds the adapter set for the discovery engine from the policy registry and config."""
import time

import httpx

from config.settings import Settings
from integrations.base_adapter import JobAdapter, PolicyHttp, RateLimiter
from integrations.career_pages.adapter import CareerPagesAdapter, load_company_sources
from integrations.email.alerts_adapter import JobAlertEmailAdapter
from integrations.indeed.adapter import IndeedAdapter
from integrations.instahyre.adapter import InstahyreAdapter
from integrations.linkedin.adapter import LinkedInAdapter
from integrations.manual_adapter import ManualImportAdapter
from integrations.naukri.adapter import NaukriAdapter
from integrations.policy import Policy, PolicyRegistry
from notifications.email_notifications import CredentialStore


def build_adapters(settings: Settings, registry: PolicyRegistry | None = None, client: httpx.Client | None = None,
                   sleep=time.sleep, clock=time.monotonic) -> dict[str, JobAdapter]:
    """One adapter per source. ``client``/``sleep``/``clock`` are injectable so tests never touch the network."""
    registry = registry or PolicyRegistry.load(settings)
    client = client or httpx.Client()

    def http_factory(policy: Policy, extra_hosts: tuple[str, ...] = ()) -> PolicyHttp:
        limiter = RateLimiter(policy.requests_per_minute, policy.max_requests_per_run, clock=clock, sleep=sleep)
        return PolicyHttp(policy, client, limiter, registry.user_agent, extra_hosts=tuple(h for h in extra_hosts if h),
                          max_bytes=policy.max_response_bytes)

    parent = registry.get("career_pages")
    # the shared request budget of the career_pages source is applied per provider limiter above (per-run objects)
    adapters: dict[str, JobAdapter] = {
        "linkedin": LinkedInAdapter(registry.get("linkedin")),
        "naukri": NaukriAdapter(registry.get("naukri")),
        "indeed": IndeedAdapter(registry.get("indeed")),
        "instahyre": InstahyreAdapter(registry.get("instahyre")),
        "manual": ManualImportAdapter(registry.get("manual")),
        "career_pages": CareerPagesAdapter(parent, registry, http_factory, load_company_sources(settings),
                                           user_agent=registry.user_agent),
        "email_alerts": JobAlertEmailAdapter(registry.get("email_alerts"), http_factory(registry.get("email_alerts")),
                                             CredentialStore(settings.job_alert_credentials_path, settings.secret_key),
                                             lookback_days=settings.job_alert_lookback_days),
    }
    return adapters
