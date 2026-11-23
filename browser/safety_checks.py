"""Browser safety checks: where the browser may go, and when it must STOP.

Nothing here tries to get past a protection. A CAPTCHA, bot check, login wall or HTTP 401/403/429 simply ends the automated run
(the application is handed to the candidate).
"""
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from integrations.base_adapter import host_allowed, is_private_host

LOOPBACK = ("127.0.0.1", "localhost", "::1")


class UnsafeUrl(Exception):
    """The browser was asked to open something that policy does not allow. Safe to show to users."""


def check_url(url: str | None, *, allow_local: bool = False, allowed_hosts: list[str] | None = None) -> str:
    """Return the URL if the browser may open it. https only (http only for loopback test forms), never private hosts / credentials."""
    if not url or not isinstance(url, str):
        raise UnsafeUrl("No URL given.")
    try:
        p = urlparse(url.strip())
    except ValueError:
        raise UnsafeUrl("The URL is invalid.") from None
    host = (p.hostname or "").lower()
    if not host or p.username or p.password:
        raise UnsafeUrl("The URL is invalid or contains credentials.")
    if host in LOOPBACK or is_private_host(host):
        if allow_local and host in LOOPBACK and p.scheme in ("http", "https"):
            if allowed_hosts is not None and not host_allowed(host, allowed_hosts):   # loopback is not a free pass: the host list still applies
                raise UnsafeUrl(f"{host} is not an allowed host for this application.")
            return url.strip()
        raise UnsafeUrl("Local and private addresses are not allowed.")
    if p.scheme != "https":
        raise UnsafeUrl("Only https pages can be opened.")
    if allowed_hosts is not None and not host_allowed(host, allowed_hosts):
        raise UnsafeUrl(f"{host} is not an allowed host for this application.")
    return url.strip()


_CAPTCHA = re.compile(r"captcha|recaptcha|hcaptcha|are you a (?:robot|human)|verify (?:that )?you(?:'| a)re (?:a )?human|unusual traffic|"
                      r"checking your browser|cf-chl|turnstile|press (?:and|&) hold|bot (?:check|detection)", re.I)
_LOGIN = re.compile(r"sign in to (?:continue|apply|your account)|log ?in to (?:continue|apply|your account)|please (?:sign|log) ?in|"
                    r"create an account to apply|session (?:has )?expired|you have been logged out|authwall", re.I)
_AUTH_FAIL = re.compile(r"incorrect (?:password|username)|invalid (?:credentials|password|username)|authentication failed|login failed|"
                        r"access denied|not authori[sz]ed|forbidden", re.I)


@dataclass
class PageVerdict:
    captcha: bool = False
    login_required: bool = False
    auth_failed: bool = False
    blocked_status: int | None = None

    @property
    def must_stop(self) -> bool:
        return self.captcha or self.login_required or self.auth_failed or self.blocked_status is not None

    def reason(self) -> str:
        if self.blocked_status:
            return f"The site refused access (HTTP {self.blocked_status}); stopping - complete this application yourself."
        if self.captcha:
            return "A CAPTCHA/bot check is shown. It is never solved or bypassed; complete this application yourself."
        if self.login_required:
            return "A sign-in is required. Log in yourself (this tool never enters credentials); continue with manual handoff."
        if self.auth_failed:
            return "Authentication failed or access was denied; stopping."
        return ""


def assess_page(text: str | None, *, status: int | None = None, has_captcha_dom: bool = False, has_password: bool = False) -> PageVerdict:
    text = (text or "")[:30000]
    return PageVerdict(
        captcha=has_captcha_dom or bool(_CAPTCHA.search(text)),
        login_required=has_password or bool(_LOGIN.search(text)),
        auth_failed=bool(_AUTH_FAIL.search(text)) and status in (401, 403),
        blocked_status=status if status in (401, 403, 429, 451) else None)
