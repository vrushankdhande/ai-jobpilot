"""E-mail notifications through the Gmail API (OAuth 2.0), optional and failure-tolerant.

* Credentials (client id/secret + refresh token) are stored ONLY Fernet-encrypted (key derived from SECRET_KEY) - never plain text, never logged,
  never returned by an API. No Gmail password is ever used. Obtain the refresh token yourself (e.g. Google OAuth Playground with scope gmail.send)
  and store it with ``scripts/gmail_setup.py``.
* Only two hosts are ever contacted: oauth2.googleapis.com (token refresh) and gmail.googleapis.com (send).
* If notifications are not configured, or Gmail fails, ``notify`` records the outcome and returns normally: the rest of the system is unaffected.
* Every message gets a history record (queued / sent / failed / skipped) with its attempt count; a dedupe key stops the same event from being mailed twice.
"""
import base64
import json
import logging
import re
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

import httpx
from cryptography.fernet import Fernet, InvalidToken

from config.settings import Settings
from database.repositories.notifications_repository import NotificationsRepository
from security.secrets_manager import SecretUnavailable, derive_fernet_key

logger = logging.getLogger(__name__)
TOKEN_URL = "https://oauth2.googleapis.com/token"
SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_ATTEMPTS = 3


class GmailAuthError(Exception):
    """Credentials are missing, revoked or invalid - not retried until you re-authorise."""


class GmailTemporaryError(Exception):
    """Network/5xx/429 - safe to retry later."""


def mask_email(addr: str) -> str:
    name, _, domain = (addr or "").partition("@")
    return f"{name[:1]}***@{domain}" if domain else "***"


# ----------------------------------------------------------------------------------------------------------- credentials
class CredentialStore:
    def __init__(self, path: Path, secret: str):
        self.path, self.secret = Path(path), secret

    def _fernet(self) -> Fernet:
        return Fernet(derive_fernet_key(self.secret, "gmail"))

    def save(self, client_id: str, client_secret: str, refresh_token: str) -> None:
        if not (client_id and client_secret and refresh_token):
            raise ValueError("client id, client secret and refresh token are all required")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_bytes(self._fernet().encrypt(json.dumps({"client_id": client_id, "client_secret": client_secret, "refresh_token": refresh_token}).encode()))
        tmp.replace(self.path)

    def load(self) -> dict | None:
        try:
            return json.loads(self._fernet().decrypt(self.path.read_bytes()))
        except (OSError, InvalidToken, ValueError, SecretUnavailable):
            return None

    def exists(self) -> bool:
        return self.path.is_file()

    def delete(self) -> bool:
        try:
            self.path.unlink()
            return True
        except OSError:
            return False


# ----------------------------------------------------------------------------------------------------------- transport
class GmailTransport:
    """Sends one RFC 822 message. Refreshes the access token as needed (kept in memory only)."""

    def __init__(self, store: CredentialStore, client: httpx.Client | None = None, timeout: float = 15.0, clock=time.monotonic):
        self.store, self.client, self.timeout, self._clock = store, client or httpx.Client(), timeout, clock
        self._token: str | None = None
        self._expires = 0.0

    def _access_token(self) -> str:
        if self._token and self._clock() < self._expires - 30:
            return self._token
        creds = self.store.load()
        if not creds:
            raise GmailAuthError("Gmail credentials are not available.")
        try:
            r = self.client.post(TOKEN_URL, data={"client_id": creds["client_id"], "client_secret": creds["client_secret"], "refresh_token": creds["refresh_token"],
                                                  "grant_type": "refresh_token"}, timeout=self.timeout)
        except httpx.HTTPError:
            raise GmailTemporaryError("network error while refreshing the Gmail token") from None
        if r.status_code in (400, 401):
            raise GmailAuthError("Gmail rejected the credentials (re-authorise).")
        if r.status_code >= 500 or r.status_code == 429:
            raise GmailTemporaryError(f"token endpoint returned HTTP {r.status_code}")
        if r.status_code != 200 or "access_token" not in r.json():
            raise GmailAuthError("unexpected token response")
        self._token, self._expires = r.json()["access_token"], self._clock() + float(r.json().get("expires_in", 3600))
        return self._token

    def send(self, raw: bytes) -> str:
        for attempt in (1, 2):
            token = self._access_token()
            try:
                r = self.client.post(SEND_URL, headers={"Authorization": f"Bearer {token}"}, json={"raw": base64.urlsafe_b64encode(raw).decode()}, timeout=self.timeout)
            except httpx.HTTPError:
                raise GmailTemporaryError("network error while sending") from None
            if r.status_code == 401 and attempt == 1:
                self._token = None                      # the access token expired: refresh once and retry the SAME send
                continue
            if r.status_code in (401, 403):
                raise GmailAuthError(f"Gmail refused the request (HTTP {r.status_code}).")
            if r.status_code == 429 or r.status_code >= 500:
                raise GmailTemporaryError(f"Gmail returned HTTP {r.status_code}")
            if r.status_code >= 400:
                raise GmailAuthError(f"Gmail rejected the message (HTTP {r.status_code}).")
            return str(r.json().get("id", ""))
        raise GmailAuthError("Gmail refused the request.")  # pragma: no cover


# ----------------------------------------------------------------------------------------------------------- notifier
class EmailNotifier:
    def __init__(self, settings: Settings, repo: NotificationsRepository, transport=None, *, now_fn=lambda: datetime.now(timezone.utc)):
        self.settings, self.repo, self.now = settings, repo, now_fn
        self.store = CredentialStore(settings.notification_credentials_path, settings.secret_key)
        self.transport = transport

    @property
    def recipient(self) -> str:
        return (self.settings.notification_recipient or "").strip()

    def status(self) -> dict:
        """Configuration status for the dashboard - never any secret."""
        return {"enabled": bool(self.settings.notifications_enabled), "recipient_set": bool(_EMAIL.match(self.recipient)), "recipient": mask_email(self.recipient) if self.recipient else None,
                "credentials_stored": self.store.exists(), "configured": self.is_configured()}

    def is_configured(self) -> bool:
        if not (self.settings.notifications_enabled and _EMAIL.match(self.recipient)):
            return False
        return self.transport is not None or self.store.load() is not None

    def _transport(self):
        return self.transport or GmailTransport(self.store)

    def _message(self, subject: str, body: str) -> bytes:
        msg = EmailMessage()
        msg["To"], msg["Subject"] = self.recipient, subject.replace("\n", " ")[:200]
        msg.set_content(body)
        return bytes(msg)

    def notify(self, type_: str, subject: str, body: str, dedupe_key: str | None = None) -> dict:
        """Record and (if configured) send. Never raises: returns the history record."""
        configured = self.is_configured()
        rec, created = self.repo.create(type_=type_, subject=subject, recipient_masked=mask_email(self.recipient), dedupe_key=dedupe_key,
                                        status="queued" if configured else "skipped", payload={"body": body} if configured else {})
        if not created:
            return rec                                              # this event was already handled: no second e-mail
        if not configured:
            self.repo.update(rec["notification_id"], {"error": "notifications are not configured"})
            return self.repo.get(rec["notification_id"])
        return self._deliver(rec["notification_id"], subject, body)

    def _deliver(self, notification_id: str, subject: str, body: str) -> dict:
        self.repo.bump_attempts(notification_id)
        try:
            mid = self._transport().send(self._message(subject, body))
            self.repo.update(notification_id, {"status": "sent", "sent_at": self.now(), "provider_message_id": mid or None, "error": None, "payload": {}})
        except GmailAuthError as exc:
            self.repo.update(notification_id, {"status": "failed", "error": f"authentication: {exc}", "permanent": True})
        except GmailTemporaryError as exc:
            self.repo.update(notification_id, {"status": "failed", "error": f"temporary: {exc}"})
        except Exception as exc:  # noqa: BLE001
            self.repo.update(notification_id, {"status": "failed", "error": f"unexpected: {type(exc).__name__}"})
        return self.repo.get(notification_id)

    def retry_failed(self) -> dict:
        """Re-send temporarily failed messages (bounded attempts). Authentication failures are not retried."""
        sent = failed = 0
        if not self.is_configured():
            return {"retried": 0, "sent": 0, "failed": 0}
        for n in self.repo.failed_retryable(MAX_ATTEMPTS):
            if n.get("permanent") or not (n.get("payload") or {}).get("body"):
                continue
            res = self._deliver(n["notification_id"], n["subject"], n["payload"]["body"])
            sent += res["status"] == "sent"
            failed += res["status"] != "sent"
        return {"retried": sent + failed, "sent": sent, "failed": failed}

    def history(self, **filters) -> list[dict]:
        return self.repo.find_all(**filters)
