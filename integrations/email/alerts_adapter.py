"""Discovery source ``email_alerts``: jobs from job-alert e-mails in the candidate's own Gmail (official Gmail API, read-only).

This is how Naukri jobs arrive automatically without touching naukri.com: Naukri e-mails the candidate the jobs matching their saved
alert, and this source reads those e-mails. It never visits a job site, never opens a link from an e-mail, never stores e-mail
bodies, and reads only messages from the allow-listed alert senders below. It does nothing until the candidate has authorised
read-only Gmail access (scripts/gmail_alerts_setup.py).
"""
import logging

from integrations.base_adapter import AdapterError, AdapterResult, JobAdapter, PolicyHttp, RawJob, SearchQuery, SourceBlocked
from integrations.email.gmail_service import GmailReader
from integrations.email.naukri_alerts import parse_naukri_alert
from integrations.policy import Policy
from notifications.email_notifications import CredentialStore, GmailAuthError, GmailTemporaryError, GmailTransport

logger = logging.getLogger(__name__)

# sender domain -> parser. Only e-mail from these domains is ever opened.
PARSERS = {"naukri.com": parse_naukri_alert}
NOT_SET_UP = "Job-alert e-mails are not set up yet: run 'uv run python scripts/gmail_alerts_setup.py' once (read-only Gmail access)."


class JobAlertEmailAdapter(JobAdapter):
    name = "email_alerts"

    def __init__(self, policy: Policy, http: PolicyHttp, store: CredentialStore, lookback_days: int = 3, tokens: GmailTransport | None = None):
        super().__init__(policy, http)
        self.store, self.lookback_days = store, lookback_days
        self.tokens = tokens or GmailTransport(store, client=http.client)

    def search(self, query: SearchQuery) -> AdapterResult:
        self.require_discovery()
        result = AdapterResult(source=self.name)
        if not self.store.exists() or not self.store.load():
            result.notes.append(NOT_SET_UP)
            return result
        reader = GmailReader(self.http, self.tokens)
        senders = tuple(PARSERS)
        try:
            ids = reader.search(senders, self.lookback_days, limit=min(self.policy.max_requests_per_run - 1, 40))
        except (GmailAuthError, SourceBlocked):
            result.errors.append("Gmail refused access - re-run scripts/gmail_alerts_setup.py (the authorisation may have expired or been revoked).")
            return result
        except (GmailTemporaryError, AdapterError) as exc:
            result.errors.append(f"Gmail could not be read right now ({exc}); will try again on the next run.")
            return result
        read = skipped_sender = hidden = 0
        for mid in ids:
            try:
                mail = reader.read(mid, senders)
            except (GmailAuthError, GmailTemporaryError, AdapterError, SourceBlocked) as exc:
                result.errors.append(f"One alert e-mail could not be read ({type(exc).__name__}).")
                continue
            if mail is None:
                skipped_sender += 1
                continue
            parser = next(p for d, p in PARSERS.items() if mail.sender_domain == d or mail.sender_domain.endswith("." + d))
            jobs, h = parser(mail.html, mail.text)
            hidden += h
            read += 1
            result.jobs += jobs
        result.requests_made = getattr(self.http.limiter, "count", 0)
        result.notes.append(f"{read} alert e-mail(s) read from the last {self.lookback_days} day(s); {len(result.jobs)} job link(s) found.")
        if skipped_sender:
            result.notes.append(f"{skipped_sender} message(s) were not from an alert sender and were not used.")
        if hidden:
            result.notes.append(f"{hidden} link(s) hid the job behind a redirect and were skipped (links are never opened).")
        logger.info("email_alerts: %d messages, %d jobs", read, len(result.jobs))
        return result

    def fetch_details(self, job: RawJob) -> RawJob:
        self.require_discovery()
        return job                               # everything known came from the e-mail; the job page is never fetched

    def to_raw(self, payload: dict, **context) -> RawJob:
        return RawJob(**payload)
