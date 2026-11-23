"""Authorised browser sessions: detect login walls, let the CANDIDATE log in, optionally keep the session encrypted.

* No passwords are ever accepted, typed or stored (see security/credential_vault.py). Logging in is manual, in a visible window.
* A saved session (cookies/localStorage from Playwright ``storage_state``) is only written when PERSIST_BROWSER_SESSIONS=true and SECRET_KEY
  is set; it is Fernet-encrypted, named by a hash of the host, expires after SESSION_MAX_AGE_HOURS and can be forgotten at any time.
* Nothing is read from the candidate's own browser profile - no session extraction.
* Expiry and authentication failures are detected from the page and reported; they end the run (they are never worked around).
"""
import hashlib
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from browser.safety_checks import PageVerdict, assess_page
from config.settings import Settings
from security.secrets_manager import SecretUnavailable, derive_fernet_key

logger = logging.getLogger(__name__)


class SessionManager:
    def __init__(self, settings: Settings, *, now=lambda: datetime.now(timezone.utc)):
        self.settings, self._now = settings, now
        self.dir: Path = settings.sessions_path

    # ---------------------------------------------------------------------------------------------- policy
    @property
    def persistence_enabled(self) -> bool:
        return bool(self.settings.persist_browser_sessions and self.settings.secret_key and len(self.settings.secret_key) >= 16)

    def _fernet(self) -> Fernet:
        return Fernet(derive_fernet_key(self.settings.secret_key))

    def _path(self, site: str) -> Path:
        return self.dir / f"{hashlib.sha256(site.lower().encode()).hexdigest()[:24]}.session"

    # ---------------------------------------------------------------------------------------------- store
    def save(self, context, site: str) -> bool:
        """Encrypt and store the context's session for ``site`` (a host). False if persistence is off."""
        if not self.persistence_enabled:
            return False
        try:
            f = self._fernet()
        except SecretUnavailable:
            return False
        payload = json.dumps({"saved_at": self._now().isoformat(), "site": site.lower(), "state": context.storage_state()}).encode()
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path(site).with_suffix(".tmp")
        tmp.write_bytes(f.encrypt(payload))
        tmp.replace(self._path(site))
        logger.info("Browser session saved for a site (encrypted)")
        return True

    def load(self, site: str) -> dict | None:
        """The stored storage_state for ``site`` or None (missing, expired, unreadable, or persistence off). Expired sessions are deleted."""
        if not self.persistence_enabled:
            return None
        path = self._path(site)
        if not path.is_file():
            return None
        try:
            data = json.loads(self._fernet().decrypt(path.read_bytes()))
            saved = datetime.fromisoformat(data["saved_at"])
        except (InvalidToken, ValueError, KeyError, SecretUnavailable, OSError):
            logger.warning("A stored browser session could not be read; ignoring it")
            return None
        if data.get("site") != site.lower() or self._now() - saved > timedelta(hours=self.settings.session_max_age_hours):
            self.forget(site)  # expired (or for another site): remove it
            return None
        return data["state"]

    def status(self, site: str) -> dict:
        path = self._path(site)
        if not self.persistence_enabled or not path.is_file():
            return {"persistent": self.persistence_enabled, "stored": False, "expired": False}
        return {"persistent": True, "stored": self.load(site) is not None, "expired": not path.is_file()}

    def forget(self, site: str) -> bool:
        try:
            self._path(site).unlink()
            return True
        except OSError:
            return False

    # ---------------------------------------------------------------------------------------------- detection
    @staticmethod
    def assess(page, status: int | None = None) -> PageVerdict:
        """Login required / CAPTCHA / refused? Read-only look at the current page."""
        try:
            text = page.evaluate("() => (document.body && document.body.innerText || '').slice(0, 20000)")
            dom = page.evaluate("""() => ({c: !!document.querySelector('iframe[src*="recaptcha"],iframe[src*="hcaptcha"],iframe[src*="turnstile"],.g-recaptcha,.h-captcha,[data-sitekey]'),
                p: !!Array.from(document.querySelectorAll('input[type=password]')).find(e => e.offsetParent !== null)})""")
        except Exception:  # noqa: BLE001
            return PageVerdict()
        return assess_page(text, status=status, has_captcha_dom=dom["c"], has_password=dom["p"])

    # ---------------------------------------------------------------------------------------------- manual login
    def manual_login(self, browser_manager, login_url: str, site: str, *, timeout_s: float = 300, poll_s: float = 2.0,
                     sleep=time.sleep, clock=time.monotonic, while_waiting=None) -> bool:
        """Open ``login_url`` in a VISIBLE window and wait while the candidate logs in themselves.

        Returns True once the window was closed or the password box disappeared (login assumed done) and, if persistence is on, the session was
        saved. ``while_waiting`` is a hook for tests that simulate the human. Credentials never pass through this code.
        """
        if browser_manager.headless and while_waiting is None:
            raise PermissionError("Manual login needs a visible browser window (set BROWSER_HEADLESS=false).")
        ctx = browser_manager.new_context(self.load(site))
        page = ctx.new_page()
        browser_manager.goto(page, login_url)
        deadline, last_state = clock() + timeout_s, None
        try:
            while clock() < deadline:
                if while_waiting is not None:
                    while_waiting(page, ctx)
                if page.is_closed():
                    break
                try:
                    last_state = ctx.storage_state()
                    still_login = self.assess(page).login_required
                except Exception:  # noqa: BLE001 - the user closed the window mid-poll
                    break
                if not still_login and while_waiting is not None:
                    break
                sleep(poll_s)
        finally:
            if self.persistence_enabled and last_state is not None:
                try:
                    class _Ctx:  # reuse save() with the last captured state
                        @staticmethod
                        def storage_state():
                            return last_state
                    self.save(_Ctx, site)
                except Exception:  # noqa: BLE001
                    logger.warning("Could not save the browser session")
            try:
                ctx.close()
            except Exception:  # noqa: BLE001
                pass
        return last_state is not None
