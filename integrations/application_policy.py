"""May the engine AUTOMATE an application on this site? Fails closed.

Manual handoff (the candidate applies in their own browser) is always possible and is the default; automation needs ALL of:
master switch on, a policy status that allows it, a host on the allow-list (or an authorised site), and a discovery policy that
does not forbid auto-apply for the source. Messages are safe to show to users.
"""
import logging
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from urllib.parse import urlparse

import yaml

from config.settings import Settings
from config.yaml_cache import load_yaml
from integrations.base_adapter import host_allowed, is_private_host
from integrations.policy import PolicyRegistry

logger = logging.getLogger(__name__)


class ApplicationStatus(str, Enum):
    AUTOMATION_ALLOWED = "AUTOMATION_ALLOWED"
    AUTHORIZATION_REQUIRED = "AUTHORIZATION_REQUIRED"
    MANUAL_HANDOFF = "MANUAL_HANDOFF"
    DISABLED = "DISABLED"


@dataclass
class ApplicationDecision:
    source: str
    provider: str | None
    status: str                       # effective ApplicationStatus value
    automation_allowed: bool
    handoff_allowed: bool
    reason: str                       # one user-safe sentence
    host: str | None = None
    needs_human_confirmation: bool = True
    checks: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


class ApplicationPolicy:
    def __init__(self, data: dict | None, registry: PolicyRegistry, settings: Settings):
        self._data = data if isinstance(data, dict) else {}
        self.registry, self.settings = registry, settings

    @classmethod
    def load(cls, settings: Settings, registry: PolicyRegistry | None = None, path: Path | None = None) -> "ApplicationPolicy":
        path = path or settings.config_path("application_policies_file")
        try:
            data = load_yaml(path) or {}
            if not isinstance(data, dict):
                raise ValueError("not a mapping")
        except Exception as exc:  # unreadable -> everything manual
            logger.error("Application policies unreadable (%s); automation disabled", type(exc).__name__)
            data = {}
        return cls(data, registry or PolicyRegistry.load(settings), settings)

    # ---------------------------------------------------------------------------------------------- lookups
    def _entry(self, source: str, provider: str | None) -> dict | None:
        entry = self._data.get(source)
        if not isinstance(entry, dict):
            return None
        if provider:
            prov = (entry.get("providers") or {}).get(provider)
            return prov if isinstance(prov, dict) else None
        return entry

    @staticmethod
    def _status(entry: dict | None) -> ApplicationStatus:
        if not entry:
            return ApplicationStatus.MANUAL_HANDOFF
        try:
            return ApplicationStatus(str(entry.get("application_status", "")).upper())
        except ValueError:
            return ApplicationStatus.MANUAL_HANDOFF  # unknown value -> no automation

    def authorized_site(self, host: str, path: str, provider: str | None) -> dict | None:
        for site in self._data.get("authorized_sites") or []:
            if not isinstance(site, dict) or not site.get("host"):
                continue
            if provider and site.get("provider") and site["provider"] != provider:
                continue
            if host_allowed(host, [str(site["host"])]) and path.startswith(str(site.get("path_prefix") or "/")):
                return site
        return None

    # ---------------------------------------------------------------------------------------------- decision
    def decide(self, source: str, provider: str | None, url: str | None) -> ApplicationDecision:
        def result(status, auto, reason, host=None, entry=None, checks=None):
            return ApplicationDecision(source=source, provider=provider, status=status.value, automation_allowed=auto, handoff_allowed=True,
                                       reason=reason, host=host, checks=checks or [],
                                       needs_human_confirmation=bool((entry or {}).get("needs_human_confirmation", True)))

        # The Phase 4 source policy still applies: a source that forbids auto-apply can never be automated here.
        src_policy = self.registry.get(source) if source != "local_test" else None
        entry = self._entry(source, provider)   # unknown source/provider -> None -> manual handoff
        status = self._status(entry)
        checks: list[str] = []

        if status == ApplicationStatus.DISABLED:
            return ApplicationDecision(source, provider, status.value, False, False, f"{source}: applications are disabled", checks=checks)
        if status == ApplicationStatus.MANUAL_HANDOFF:
            return result(status, False, f"{source}{('/' + provider) if provider else ''}: automated applications are not permitted - complete this application yourself "
                                         "(manual handoff)", entry=entry)

        # --- a candidate for automation: every gate must pass ---
        if not self.settings.application_automation_enabled:
            return result(status, False, "Application automation is switched off (APPLICATION_AUTOMATION_ENABLED=false); use manual handoff.", entry=entry)
        checks.append("master switch on")
        if src_policy is not None and not src_policy.auto_apply and source not in ("career_pages",):
            return result(status, False, f"{source}: the platform policy does not allow automated applying", entry=entry, checks=checks)
        if not url:
            return result(status, False, "This job has no application URL; use manual handoff.", entry=entry, checks=checks)
        try:
            p = urlparse(url.strip())
        except ValueError:
            return result(status, False, "The application URL is invalid.", entry=entry, checks=checks)
        host = (p.hostname or "").lower()
        if not host or p.username or p.password:
            return result(status, False, "The application URL is invalid.", entry=entry, checks=checks)

        if source == "local_test":
            if not self.settings.allow_local_test_forms:
                return result(status, False, "Local test forms are not enabled.", host, entry, checks)
            if p.scheme != "http" or host not in ("127.0.0.1", "localhost", "::1"):
                return result(status, False, "Local test forms must be on http://127.0.0.1.", host, entry, checks)
            checks.append("loopback test form")
            return result(status, True, "Controlled local test form: automation permitted.", host, entry, checks)

        if p.scheme != "https" or is_private_host(host):
            return result(status, False, "Only public https application pages can be automated.", host, entry, checks)
        checks.append("public https URL")
        hosts = [str(h) for h in (entry or {}).get("application_hosts") or []]
        site = self.authorized_site(host, p.path or "/", provider)
        if status == ApplicationStatus.AUTHORIZATION_REQUIRED:
            if site is None:
                return result(status, False, f"Automation for {host} needs your explicit authorisation: add it under authorized_sites in "
                                             "config/application_policies.yaml after checking the employer's terms. Use manual handoff meanwhile.",
                              host, entry, checks)
            checks.append("site authorised by the user")
        if hosts and not host_allowed(host, hosts) and site is None:
            return result(status, False, f"{host} is not an approved application host for {provider or source}.", host, entry, checks)
        return result(status, True, "Automation permitted for this site (candidate approval is still required before submitting).", host, entry, checks)
