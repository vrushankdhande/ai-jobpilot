"""Platform capability registry (config/platform_policies.yaml). Fails closed."""
import logging
from enum import Enum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from config.settings import Settings
from config.yaml_cache import load_yaml

logger = logging.getLogger(__name__)


class SourceStatus(str, Enum):
    ENABLED = "ENABLED"
    MANUAL_ONLY = "MANUAL_ONLY"
    DISABLED = "DISABLED"
    PENDING_POLICY_REVIEW = "PENDING_POLICY_REVIEW"


class PolicyViolation(Exception):
    """An action is not permitted by the platform policy. Messages are safe to show to users."""


class Policy(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    status: SourceStatus = SourceStatus.DISABLED
    manual_import: bool = False
    auto_apply: bool = False
    allowed_hosts: list[str] = Field(default_factory=list)
    requests_per_minute: float = Field(default=0, ge=0)
    max_requests_per_run: int = Field(default=100, ge=0)
    max_jobs_per_run: int = Field(default=200, ge=0)
    # Largest response body accepted from this source. Raise it only for a documented JSON API whose full listing is
    # genuinely large (a big Greenhouse board); the ceiling keeps one response from exhausting memory.
    max_response_bytes: int = Field(default=5_000_000, ge=1, le=60_000_000)
    require_robots_txt: bool = False
    reviewed_on: str | None = None
    needs_human_confirmation: bool = True
    basis: str = ""
    reason: str = ""  # why the effective status is what it is (e.g. an invalid YAML value)

    @property
    def automated_allowed(self) -> bool:
        return self.status == SourceStatus.ENABLED

    @property
    def manual_allowed(self) -> bool:
        return self.manual_import and self.status != SourceStatus.DISABLED

    def deny_reason(self) -> str:
        if self.status == SourceStatus.MANUAL_ONLY:
            return f"{self.name}: automated discovery is not permitted; import jobs manually"
        if self.status == SourceStatus.PENDING_POLICY_REVIEW:
            return f"{self.name}: automated discovery is disabled until its policy is reviewed and confirmed"
        return f"{self.name}: this source is disabled" + (f" ({self.reason})" if self.reason else "")


def _policy_from(name: str, data: dict | None, base: dict | None = None) -> Policy:
    merged = {**(base or {}), **(data or {})}
    raw_status = merged.get("discovery_status")
    reason = ""
    try:
        status = SourceStatus(str(raw_status).upper()) if raw_status is not None else SourceStatus.DISABLED
        if raw_status is None:
            reason = "no discovery_status configured"
    except ValueError:
        status, reason = SourceStatus.DISABLED, f"unknown status '{raw_status}'"  # fail closed
    fields = {k: merged[k] for k in ("manual_import", "auto_apply", "allowed_hosts", "requests_per_minute",
                                     "max_requests_per_run", "max_jobs_per_run", "max_response_bytes", "require_robots_txt", "reviewed_on",
                                     "needs_human_confirmation", "basis") if k in merged}
    try:
        return Policy(name=name, status=status, reason=reason, **fields)
    except Exception:  # malformed numbers etc. -> disabled
        return Policy(name=name, status=SourceStatus.DISABLED, reason="invalid policy entry")


class PolicyRegistry:
    """Looks up the effective policy of a source/provider. Unknown names are DISABLED."""

    def __init__(self, data: dict | None = None):
        self._data = data or {}
        self.user_agent = str(self._data.get("user_agent") or "AI-JobPilot/0.4")

    @classmethod
    def load(cls, settings: Settings | None = None, path: Path | None = None) -> "PolicyRegistry":
        path = path or (settings or Settings()).config_path("platform_policies_file")
        try:
            data = load_yaml(path) or {}
            if not isinstance(data, dict):
                raise ValueError("policy file must be a mapping")
        except Exception as exc:  # missing/garbled file -> everything disabled
            logger.error("Platform policies unreadable (%s); all sources disabled", type(exc).__name__)
            data = {}
        return cls(data)

    @property
    def source_names(self) -> list[str]:
        return [k for k, v in self._data.items() if isinstance(v, dict)]

    def get(self, name: str, provider: str | None = None) -> Policy:
        entry = self._data.get(name)
        if not isinstance(entry, dict):
            return Policy(name=name, status=SourceStatus.DISABLED, reason="not listed in platform_policies.yaml")
        if provider is None:
            return _policy_from(name, entry)
        providers = entry.get("providers")
        pdata = providers.get(provider) if isinstance(providers, dict) else None
        if not isinstance(pdata, dict):
            return Policy(name=f"{name}/{provider}", status=SourceStatus.DISABLED, reason="provider not listed")
        parent = _policy_from(name, entry)
        child = _policy_from(f"{name}/{provider}", pdata, base={"manual_import": entry.get("manual_import", False)})
        if not parent.automated_allowed:  # a provider can never be more permissive than its source
            child.status = parent.status
            child.reason = child.reason or f"parent source {name} is {parent.status.value}"
        return child

    def describe(self) -> list[dict]:
        out = []
        for name in self.source_names:
            p = self.get(name)
            providers = {}
            for prov in (self._data[name].get("providers") or {}):
                pp = self.get(name, prov)
                providers[prov] = {"status": pp.status.value, "reason": pp.reason or pp.basis}
            out.append({"source": name, "status": p.status.value, "automated_discovery": p.automated_allowed,
                        "manual_import": p.manual_allowed, "reviewed_on": p.reviewed_on,
                        "needs_human_confirmation": p.needs_human_confirmation, "basis": p.basis,
                        "requests_per_minute": p.requests_per_minute, "providers": providers})
        return out
