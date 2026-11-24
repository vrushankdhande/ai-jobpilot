"""Reads job-alert e-mails from the candidate's OWN Gmail with the official Gmail API (read-only scope).

* Only two hosts are contacted: oauth2.googleapis.com (token refresh, via the existing GmailTransport) and gmail.googleapis.com
  (through PolicyHttp, so the ``email_alerts`` policy and its host allow-list apply).
* Only messages from allow-listed job-alert senders are opened (the Gmail search is narrowed by sender, and the From header is
  checked again before a message is used). Nothing else in the mailbox is read.
* Message bodies are used in memory to extract jobs and are never stored or logged; logs carry counts only.
* Links found in a message are never opened.
"""
import base64
import logging
import re
from dataclasses import dataclass
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
        """Ids of recent messages from the given sender domains (newest first)."""
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
