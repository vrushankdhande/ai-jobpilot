"""Reads e-mails from the candidate's OWN Gmail with the official Gmail API (read-only scope). Two uses:

1. job-alert e-mails (discovery source ``email_alerts``): only messages from allow-listed alert senders, see ``search``/``read``;
2. replies to the candidate's applications (rejection tracking, policy ``email_rejections``): only messages that Gmail itself finds for a
   fixed rejection-wording query (``search_query``/``read_message``) - the mailbox is never listed or browsed.

* Only two hosts are contacted: oauth2.googleapis.com (token refresh, via the existing GmailTransport) and gmail.googleapis.com
  (through PolicyHttp, so the ``email_alerts`` policy and its host allow-list apply).
* Job alerts: only messages from allow-listed job-alert senders are opened (the Gmail search is narrowed by sender, and the From header
  is checked again before a message is used).
* Message bodies are used in memory to extract jobs and are never stored or logged; logs carry counts only.
* Links found in a message are never opened.
"""
import base64
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parseaddr

from integrations.base_adapter import AdapterError, PolicyHttp
from notifications.email_notifications import GmailTransport

logger = logging.getLogger(__name__)

API = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


@dataclass
class AlertEmail:
    message_id: str
    sender_domain: str
    subject: str
    html: str
    text: str


@dataclass
class MailMessage:
    message_id: str
    sender_domain: str
    sender_name: str          # display name only ("Acme Careers"); the address itself is not kept
    subject: str
    html: str
    text: str
    received_at: datetime | None


def sender_domain(from_header: str) -> str:
    addr = parseaddr(from_header or "")[1].lower()
    return addr.rsplit("@", 1)[-1] if "@" in addr else ""


def domain_allowed(domain: str, allowed: tuple[str, ...]) -> bool:
    return any(domain == a or domain.endswith("." + a) for a in allowed)


def _decode(data: str | None) -> str:
    if not data:
        return ""
    try:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")
    except (ValueError, TypeError):
        return ""


def _bodies(part: dict, out: dict) -> None:
    mime = (part.get("mimeType") or "").lower()
    if mime in ("text/html", "text/plain") and mime not in out:
        out[mime] = _decode((part.get("body") or {}).get("data"))
    for child in part.get("parts") or []:
        _bodies(child, out)


class GmailReader:
    def __init__(self, http: PolicyHttp, tokens: GmailTransport):
        self.http, self.tokens = http, tokens

    def _get(self, url: str, params: dict | None = None) -> dict:
        resp = self.http.get(url, params=params, headers={"Authorization": f"Bearer {self.tokens._access_token()}"})
        if resp.status_code != 200:
            raise AdapterError(f"Gmail returned HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError:
            raise AdapterError("Gmail returned an unreadable response") from None

    def search(self, senders: tuple[str, ...], days: int, limit: int = 25) -> list[str]:
        """Ids of recent messages from the given senders - domains or exact addresses (newest first)."""
        q = "{" + " ".join(f"from:{d}" for d in senders) + "}" + f" newer_than:{int(days)}d"
        data = self._get(API, {"q": q, "maxResults": max(1, min(limit, 100))})
        return [m["id"] for m in data.get("messages") or [] if re.fullmatch(r"[A-Za-z0-9_-]{6,64}", str(m.get("id", "")))]

    def read(self, message_id: str, senders: tuple[str, ...]) -> AlertEmail | None:
        """The message, or None when its From header is not an allow-listed sender (it is then not used at all)."""
        data = self._get(f"{API}/{message_id}", {"format": "full"})
        payload = data.get("payload") or {}
        headers = {h.get("name", "").lower(): h.get("value", "") for h in payload.get("headers") or []}
        domain = sender_domain(headers.get("from", ""))
        if not domain_allowed(domain, senders):
            return None
        bodies: dict[str, str] = {}
        _bodies(payload, bodies)
        return AlertEmail(message_id, domain, headers.get("subject", ""), bodies.get("text/html", ""), bodies.get("text/plain", ""))

    def search_query(self, query: str, limit: int = 25) -> list[str]:
        """Ids of the messages Gmail finds for a fixed query (newest first). The query is built by the caller from constants, never user input."""
        data = self._get(API, {"q": query, "maxResults": max(1, min(limit, 100))})
        return [m["id"] for m in data.get("messages") or [] if re.fullmatch(r"[A-Za-z0-9_-]{6,64}", str(m.get("id", "")))]

    def read_message(self, message_id: str) -> MailMessage:
        """One message found by ``search_query``. Used in memory only; the caller keeps at most a few extracted words."""
        data = self._get(f"{API}/{message_id}", {"format": "full"})
        payload = data.get("payload") or {}
        headers = {h.get("name", "").lower(): h.get("value", "") for h in payload.get("headers") or []}
        name = parseaddr(headers.get("from", ""))[0].strip().strip('"')
        bodies: dict[str, str] = {}
        _bodies(payload, bodies)
        try:
            received = datetime.fromtimestamp(int(data.get("internalDate")) / 1000, tz=timezone.utc)
        except (TypeError, ValueError):
            received = None
        return MailMessage(message_id, sender_domain(headers.get("from", "")), "" if "@" in name else name[:80], headers.get("subject", ""),
                           bodies.get("text/html", ""), bodies.get("text/plain", ""), received)
