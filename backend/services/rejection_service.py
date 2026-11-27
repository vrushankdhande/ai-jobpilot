"""Rejection tracking: finds replies to the candidate's applications in their own Gmail that say "not selected" and marks the application.

* Gmail access is the existing read-only authorisation (gmail.readonly). Gmail is asked ONLY for messages containing fixed rejection
  wording (``GMAIL_PHRASES``) from the last ``rejection_lookback_days``; the mailbox is never listed or browsed, and job-alert senders are excluded.
* Each e-mail is read once, in memory. What is kept: company, role, sender domain, date, the rejection phrase and the decision - never the text.
* An application is updated automatically only when company AND role match exactly one sent application (``rejection_matcher``);
  everything else waits for the candidate. Every change is an audit event and can be undone. The sending ``state`` is never changed.
"""
import logging
from datetime import datetime, timezone

import httpx
from pymongo.database import Database

from config.settings import Settings
from database.repositories.applications_repository import ApplicationsRepository
from database.repositories.rejections_repository import OPEN, RejectionsRepository
from engines.application.rejection_matcher import match_rejection
from integrations.base_adapter import AdapterError, PolicyHttp, RateLimiter, SourceBlocked
from integrations.email.gmail_service import GmailReader
from integrations.email.rejection_mail import ALERT_ONLY, GMAIL_CONTEXT, GMAIL_PHRASES, detect_rejection, text_for_matching
from integrations.policy import PolicyRegistry, PolicyViolation
from notifications.email_notifications import CredentialStore, GmailAuthError, GmailTemporaryError, GmailTransport
from security.audit_logger import AuditLogger

logger = logging.getLogger(__name__)

POLICY = "email_rejections"
MAX_MAILS = 60                       # newest matching messages looked at per check (already-seen ones are not read again)
TRACKED_STATES = ["SUBMITTED", "VERIFIED", "VERIFICATION_PENDING", "MANUAL_HANDOFF_REQUIRED"]
_SOURCE_BY_DOMAIN = {"linkedin.com": "linkedin", "naukri.com": "naukri", "indeed.com": "indeed", "instahyre.com": "instahyre"}
NOT_SET_UP ="Gmail is not connected yet: run 'uv run python scripts/gmail_alerts_setup.py' once (read-only access)."


class RejectionNotFound(Exception):
    pass


class RejectionNotAllowed(Exception):
    pass


def gmail_query(days: int) -> str:
    phrases = " ".join(f'"{p}"' for p in GMAIL_PHRASES)
    context = " ".join(f'"{p}"' for p in GMAIL_CONTEXT)
    excluded = " ".join(f"-from:{a}" for a in ALERT_ONLY)
    return f"newer_than:{int(days)}d -in:sent -in:chats -category:promotions {excluded} {{{phrases}}} {{{context}}}"


class RejectionService:
    def __init__(self, db: Database, settings: Settings, *, reader: GmailReader | None = None, client: httpx.Client | None = None, now_fn=None):
        self.settings, self.db = settings, db
        self.apps, self.rej = ApplicationsRepository(db), RejectionsRepository(db)
        self.audit = AuditLogger(self.apps)
        self._reader, self._client = reader, client
        self.now = now_fn or (lambda: datetime.now(timezone.utc))

    # ---------------------------------------------------------------------------------------------- Gmail
    def _store(self) -> CredentialStore:
        return CredentialStore(self.settings.job_alert_credentials_path, self.settings.secret_key)

    def connected(self) -> bool:
        if self._reader is not None:
            return True
        store = self._store()
        return store.exists() and bool(store.load())

    def _gmail(self) -> GmailReader:
        if self._reader is not None:
            return self._reader
        registry = PolicyRegistry.load(self.settings)
        policy = registry.get(POLICY)
        client = self._client or httpx.Client()
        http = PolicyHttp(policy, client, RateLimiter(policy.requests_per_minute, policy.max_requests_per_run), registry.user_agent,
                          max_bytes=policy.max_response_bytes)
        return GmailReader(http, GmailTransport(self._store(), client=client))

    def _tracked_apps(self) -> list[dict]:
        cur = self.apps.col.find({"state": {"$in": TRACKED_STATES}, "dismissed_at": None},
                                 {"_id": 0, "application_id": 1, "company_name": 1, "job_title": 1, "state": 1, "employer_response": 1})
        return list(cur)

    def scan(self, trigger: str = "manual") -> dict:
        """Read new rejection-looking e-mails and record them. Never raises for Gmail problems - they are reported in the summary."""
        summary = {"at": self.now(), "trigger": trigger, "status": "ok", "messages_found": 0, "read": 0, "already_seen": 0, "not_rejection": 0,
                   "auto_marked": 0, "needs_confirmation": 0, "unmatched": 0, "already_recorded": 0, "message": ""}
        if not self.connected():
            summary.update(status="not_set_up", message=NOT_SET_UP)
            self.rej.save_check(summary)
            return summary
        try:
            reader = self._gmail()
            ids = reader.search_query(gmail_query(self.settings.rejection_lookback_days), limit=MAX_MAILS)
        except (GmailAuthError, SourceBlocked, PolicyViolation) as exc:
            summary.update(status="error", message=f"Gmail refused access ({type(exc).__name__}) - re-run scripts/gmail_alerts_setup.py.")
            self.rej.save_check(summary)
            return summary
        except (GmailTemporaryError, AdapterError) as exc:
            summary.update(status="error", message=f"Gmail could not be read right now ({exc}); will try again on the next check.")
            self.rej.save_check(summary)
            return summary
        summary["messages_found"] = len(ids)
        apps = self._tracked_apps()
        by_id = {a["application_id"]: a for a in apps}
        for mid in ids:
            if self.rej.seen(mid):
                summary["already_seen"] += 1
                continue
            try:
                mail = reader.read_message(mid)
            except (GmailAuthError, GmailTemporaryError, AdapterError, SourceBlocked, PolicyViolation) as exc:
                summary["message"] = f"Some e-mails could not be read ({type(exc).__name__}); they are tried again next time."
                continue
            summary["read"] += 1
            sig = detect_rejection(mail.subject, mail.text, mail.html, sender_domain=mail.sender_domain, sender_name=mail.sender_name, received_at=mail.received_at)
            if sig is None:
                self.rej.insert(mid, {"status": "not_rejection", "received_at": mail.received_at})
                summary["not_rejection"] += 1
                continue
            match = match_rejection(sig, text_for_matching(mail.subject, mail.text, mail.html), apps)
            row = {"company": sig.company or sig.company_from_sender, "role": sig.role, "phrase": sig.phrase, "sender_domain": sig.sender_domain,
                   "received_at": sig.received_at, "candidates": match.application_ids, "reason": match.reason, "application_id": None,
                   "decided_at": None, "decided_by": None}
            if match.decision == "auto":
                app = by_id[match.application_ids[0]]
                if (app.get("employer_response") or {}).get("outcome") == "rejected":
                    row.update(status="already_recorded", application_id=app["application_id"])
                    summary["already_recorded"] += 1
                    self.rej.insert(mid, row)
                    continue
                row.update(status="applied", application_id=app["application_id"], decided_at=self.now(), decided_by="email_check")
                saved = self.rej.insert(mid, row)
                if saved:
                    self._mark(app["application_id"], saved, source="email", actor="email_check")
                    app["employer_response"] = {"outcome": "rejected"}
                    summary["auto_marked"] += 1
            else:
                row["status"] = "needs_confirmation" if match.decision == "confirm" else "unmatched"
                self.rej.insert(mid, row)
                summary["needs_confirmation" if match.decision == "confirm" else "unmatched"] += 1
        logger.info("rejection check: %d found, %d read, %d auto, %d to confirm, %d unmatched", summary["messages_found"], summary["read"],
                    summary["auto_marked"], summary["needs_confirmation"], summary["unmatched"])
        self.rej.save_check(summary)
        return summary

    # ---------------------------------------------------------------------------------------------- marking
    def _mark(self, application_id: str, signal: dict | None, *, source: str, actor: str) -> None:
        resp = {"outcome": "rejected", "source": source, "recorded_at": self.now(), "signal_id": (signal or {}).get("signal_id"),
                "at": (signal or {}).get("received_at") or self.now(), "company": (signal or {}).get("company"), "role": (signal or {}).get("role"),
                "sender_domain": (signal or {}).get("sender_domain"), "phrase": (signal or {}).get("phrase")}
        self.apps.update(application_id, {"employer_response": resp})
        self.audit.record(application_id, "employer_rejected", actor=actor, source=source, sender_domain=resp["sender_domain"])

    def _app(self, application_id: str) -> dict:
        app = self.apps.get(application_id)
        if app is None:
            raise RejectionNotFound("Application not found.")
        return app

    def confirm(self, signal_id: str, application_id: str) -> dict:
        """The candidate says: this rejection e-mail is about that application."""
        sig = self.rej.get(signal_id)
        if sig is None:
            raise RejectionNotFound("Rejection e-mail not found.")
        if sig["status"] not in OPEN:
            raise RejectionNotAllowed("This e-mail was already handled.")
        app = self._app(application_id)
        if app["state"] not in TRACKED_STATES or app.get("dismissed_at"):
            raise RejectionNotAllowed("Only an application you sent (or applied to yourself) can be marked as rejected.")
        updated = self.rej.update(signal_id, {"status": "applied", "application_id": application_id, "decided_at": self.now(), "decided_by": "candidate"}, only_if=OPEN)
        if updated is None:
            raise RejectionNotAllowed("This e-mail was already handled.")
        self._mark(application_id, updated, source="email", actor="candidate")
        return updated

    def add_as_applied(self, signal_id: str, *, import_job, record_applied) -> dict:
        """For a rejection e-mail about a job the candidate applied to OUTSIDE JobPilot: adds that job (only the company and role the e-mail
        names - nothing is fetched) and records "I applied myself", then marks it rejected by the employer. ``import_job(fields, source)`` and
        ``record_applied(job_id, notes)`` are the normal Discovery / Applications steps, so the record is the candidate's word, never verified."""
        sig = self.rej.get(signal_id)
        if sig is None:
            raise RejectionNotFound("Rejection e-mail not found.")
        if sig["status"] not in OPEN:
            raise RejectionNotAllowed("This e-mail was already handled.")
        if not (sig.get("company") and sig.get("role")):
            raise RejectionNotAllowed("This e-mail does not name both the company and the role, so it cannot be added. Pick an application instead.")
        source = _SOURCE_BY_DOMAIN.get((sig.get("sender_domain") or "").lower())
        job = import_job({"job_title": sig["role"], "company_name": sig["company"]}, source)
        if not job or not job.get("job_id"):
            raise RejectionNotAllowed("The job could not be added.")
        app = record_applied(job["job_id"], f"Added from a rejection e-mail ({sig.get('sender_domain')}): you applied outside JobPilot; the date you applied is not known.")
        return self.confirm(signal_id, app["application_id"])

    def dismiss(self, signal_id: str) -> dict:
        """Not about any of my applications."""
        updated = self.rej.update(signal_id, {"status": "dismissed", "decided_at": self.now(), "decided_by": "candidate"}, only_if=OPEN)
        if updated is None:
            if self.rej.get(signal_id) is None:
                raise RejectionNotFound("Rejection e-mail not found.")
            raise RejectionNotAllowed("This e-mail was already handled.")
        return updated

    def mark_rejected(self, application_id: str) -> dict:
        """The candidate records a rejection themselves (e.g. a phone call)."""
        app = self._app(application_id)
        if app["state"] not in TRACKED_STATES or app.get("dismissed_at"):
            raise RejectionNotAllowed("Only an application you sent (or applied to yourself) can be marked as rejected.")
        if (app.get("employer_response") or {}).get("outcome") == "rejected":
            return self.apps.get(application_id)
        self._mark(application_id, None, source="candidate", actor="candidate")
        return self.apps.get(application_id)

    def undo(self, application_id: str) -> dict:
        """Takes the rejection off the application (wrong match or a mistake). The e-mail is not processed again."""
        app = self._app(application_id)
        resp = app.get("employer_response") or {}
        if resp.get("outcome") != "rejected":
            raise RejectionNotAllowed("This application is not marked as rejected.")
        self.apps.update(application_id, {"employer_response": None})
        if resp.get("signal_id"):
            self.rej.update(resp["signal_id"], {"status": "undone", "decided_at": self.now(), "decided_by": "candidate"})
        self.audit.record(application_id, "employer_rejection_undone", actor="candidate", source=resp.get("source"))
        return self.apps.get(application_id)

    # ---------------------------------------------------------------------------------------------- listing
    def overview(self) -> dict:
        def brief(app_id: str) -> dict | None:
            a = self.apps.get(app_id)
            return None if a is None else {"application_id": a["application_id"], "company_name": a.get("company_name"), "job_title": a.get("job_title"), "state": a["state"]}

        def rows(statuses: tuple[str, ...], limit: int) -> list[dict]:
            out = []
            for r in self.rej.list(statuses, limit=limit):
                r["candidates"] = [b for b in (brief(i) for i in r.get("candidates") or []) if b]
                r["application"] = brief(r["application_id"]) if r.get("application_id") else None
                out.append(r)
            return out

        return {"connected": self.connected(), "lookback_days": self.settings.rejection_lookback_days, "last_check": self.rej.last_check(),
                "needs_confirmation": rows(("needs_confirmation",), 50), "unmatched": rows(("unmatched",), 50),
                "recent": rows(("applied",), 20), "rejected_total": self.apps.count(states=TRACKED_STATES, employer_outcome="rejected")}
