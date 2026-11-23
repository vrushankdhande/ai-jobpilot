"""Playwright browser lifecycle for PERMITTED application automation (sync API, one browser per operation).

* Chromium family only. Uses Playwright's bundled Chromium, or an installed Edge/Chrome via ``BROWSER_CHANNEL`` (automatic fallback),
  so a failed browser download does not block local development on Windows.
* Headless/headed is configurable (``BROWSER_HEADLESS``); headed lets the candidate watch or log in manually.
* Every main-frame navigation is checked against policy (https only, allowed hosts, no private addresses) - a page cannot redirect
  the automation somewhere else. Nothing here spoofs the user agent, hides automation, solves CAPTCHAs or evades rate limits.
* Errors are mapped to ``BrowserError`` with user-safe messages; cleanup always runs.
"""
import logging
import re
import time
from contextlib import contextmanager
from urllib.parse import urlparse

from browser.safety_checks import LOOPBACK, UnsafeUrl, check_url
from config.settings import Settings
from integrations.base_adapter import host_allowed

logger = logging.getLogger(__name__)


class BrowserError(Exception):
    """User-safe description of a browser failure."""

    def __init__(self, message: str, *, transient: bool = False):
        super().__init__(message)
        self.transient = transient  # safe to retry (nothing was submitted)


class BrowserUnavailable(BrowserError):
    pass


def _import_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise BrowserUnavailable("Playwright is not installed (run: uv sync).") from None
    return sync_playwright


class BrowserManager:
    def __init__(self, settings: Settings, *, allowed_hosts: list[str] | None = None, headless: bool | None = None, sleep=time.sleep):
        self.settings = settings
        self.allowed_hosts = [h.lower() for h in (allowed_hosts or [])]
        self.headless = settings.browser_headless if headless is None else headless
        self.allow_local = settings.allow_local_test_forms
        self._pw = self._browser = None
        self._contexts: list = []
        self.channel_used: str | None = None
        self.blocked_navigations: list[str] = []   # hosts we refused to navigate to (host only - never full URLs)
        self._sleep = sleep

    # ---------------------------------------------------------------------------------------------- lifecycle
    def start(self) -> "BrowserManager":
        if self._browser is not None:
            return self
        sync_playwright = _import_playwright()
        try:
            self._pw = sync_playwright().start()
        except Exception as exc:  # noqa: BLE001
            raise BrowserUnavailable(f"Could not start Playwright ({type(exc).__name__}).") from None
        channels: list[str | None] = [self.settings.browser_channel or None]
        if not self.settings.browser_channel:
            channels += ["msedge", "chrome"]   # fall back to an installed browser if the bundled Chromium is missing
        last = None
        for ch in channels:
            try:
                kwargs = {"headless": self.headless, "slow_mo": self.settings.browser_slow_mo_ms}
                if ch:
                    kwargs["channel"] = ch
                self._browser = self._pw.chromium.launch(**kwargs)
                self.channel_used = ch or "chromium"
                logger.info("Browser started: %s headless=%s", self.channel_used, self.headless)
                return self
            except Exception as exc:  # noqa: BLE001
                last = exc
        self.close()
        raise BrowserUnavailable("No Chromium-based browser could be started (install Playwright's Chromium with "
                                 f"'uv run playwright install chromium', or set BROWSER_CHANNEL=msedge). [{type(last).__name__}]")

    def close(self) -> None:
        """Safe to call repeatedly, and after failures."""
        for ctx in reversed(self._contexts):
            try:
                ctx.close()
            except Exception:  # noqa: BLE001
                pass
        self._contexts.clear()
        for obj, method in ((self._browser, "close"), (self._pw, "stop")):
            if obj is not None:
                try:
                    getattr(obj, method)()
                except Exception:  # noqa: BLE001
                    pass
        self._browser = self._pw = None

    def __enter__(self) -> "BrowserManager":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------------------------------------------------------------------------------------------- contexts / pages
    def new_context(self, storage_state: dict | None = None):
        if self._browser is None:
            raise BrowserError("The browser is not started.")
        ctx = self._browser.new_context(storage_state=storage_state, accept_downloads=False, service_workers="block", permissions=[],
                                        viewport={"width": 1280, "height": 1600}, locale="en-US")
        ctx.set_default_timeout(self.settings.browser_timeout_ms)
        ctx.set_default_navigation_timeout(self.settings.browser_timeout_ms * 2)
        ctx.route("**/*", self._guard)
        self._contexts.append(ctx)
        return ctx

    def _guard(self, route) -> None:
        """Main-frame navigations may only go to allowed hosts; nothing may reach private addresses (except loopback test forms)."""
        req = route.request
        try:
            host = (urlparse(req.url).hostname or "").lower()
            scheme = urlparse(req.url).scheme
            if scheme in ("data", "blob", "about"):
                return route.continue_()
            try:
                top_level = req.frame.parent_frame is None
            except Exception:  # noqa: BLE001 - the frame of a redirected navigation may be unavailable: treat it as top-level (stricter)
                top_level = True
            if req.is_navigation_request() and top_level:
                check_url(req.url, allow_local=self.allow_local, allowed_hosts=self.allowed_hosts or None)
            elif host in LOOPBACK:
                if not self.allow_local:
                    raise UnsafeUrl("local")
            elif host and host_allowed(host, ["localhost"]) is False:
                from integrations.base_adapter import is_private_host
                if is_private_host(host):
                    raise UnsafeUrl("private")
        except UnsafeUrl:
            self.blocked_navigations.append((urlparse(req.url).hostname or "?")[:100])
            return route.abort("blockedbyclient")
        except Exception:  # noqa: BLE001
            return route.abort("failed")
        return route.continue_()

    def new_page(self, context=None):
        ctx = context or (self._contexts[-1] if self._contexts else self.new_context())
        return ctx.new_page()

    # ---------------------------------------------------------------------------------------------- navigation
    def goto(self, page, url: str) -> int | None:
        """Open ``url``. Returns the HTTP status. Raises BrowserError (transient=True for timeouts/network errors before anything is submitted)."""
        try:
            check_url(url, allow_local=self.allow_local, allowed_hosts=self.allowed_hosts or None)
        except UnsafeUrl as exc:
            raise BrowserError(str(exc)) from None
        try:
            resp = page.goto(url, wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("load", timeout=self.settings.browser_timeout_ms)
            except Exception:  # noqa: BLE001 - slow assets are not an error
                pass
            return resp.status if resp is not None else None
        except Exception as exc:  # noqa: BLE001
            name = type(exc).__name__
            if "Timeout" in name:
                raise BrowserError("The page took too long to load.", transient=True) from None
            if re.search(r"blocked_?by_?client", str(exc), re.I):
                raise BrowserError("The page tried to navigate somewhere that is not allowed.") from None
            raise BrowserError("The page could not be loaded.", transient=True) from None

    @contextmanager
    def session(self, storage_state: dict | None = None):
        """``with manager.session() as page`` - a fresh context + page, always closed afterwards."""
        ctx = self.new_context(storage_state)
        page = ctx.new_page()
        try:
            yield page
        finally:
            try:
                ctx.close()
            except Exception:  # noqa: BLE001
                pass
            if ctx in self._contexts:
                self._contexts.remove(ctx)
