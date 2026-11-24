"""Job-alert e-mails: Naukri jobs from the candidate's own Gmail (read-only API). All HTTP is mocked; nothing contacts Naukri.

The alert below is a made-up e-mail in the usual job-alert shape (title link, company, experience, salary, location, skills, posted,
apply button, footer). Real Naukri alerts must still be checked against this parser before relying on it.
"""
import base64
import json
from urllib.parse import quote

import httpx
import pytest

from config.settings import Settings
from engines.discovery.job_normalizer import normalize
from engines.matching.skill_extractor import get_catalog
from integrations.base_adapter import PolicyHttp, RateLimiter, SearchQuery
from integrations.email.alerts_adapter import NOT_SET_UP, JobAlertEmailAdapter
from integrations.email.gmail_service import domain_allowed, sender_domain
from integrations.email.naukri_alerts import naukri_job_ref, parse_naukri_alert
from integrations.policy import Policy, PolicyRegistry, PolicyViolation, SourceStatus
from notifications.email_notifications import CredentialStore

pytestmark = pytest.mark.usefixtures("no_network")

J1 = "https://www.naukri.com/job-listings-generative-ai-engineer-bizbrolly-noida-pune-4-to-8-years-231126915481?src=alert&sid=1"
J2 = "https://www.naukri.com/job-listings-ml-engineer-beta-labs-pune-3-to-6-years-211125099999?src=alert"
TRACKED_J2 = "https://click.naukri.com/c?u=" + quote(quote(J2, safe=""), safe="")
ALERT = f"""<html><head><style>.x{{color:red}}</style></head><body>
<p>Hi, here are 3 new jobs matching your alert "AI Engineer - Pune"</p>
<table>
 <tr><td><a href="{J1}">Generative AI Engineer</a></td></tr>
 <tr><td>BizBrolly</td></tr><tr><td>4.1</td></tr><tr><td>4-8 Yrs</td></tr><tr><td>Not disclosed</td></tr><tr><td>Noida, Pune</td></tr>
 <tr><td>Python, LangChain, LLM, RAG, PyTorch</td></tr><tr><td>1 day ago</td></tr><tr><td><a href="{J1}">Apply</a></td></tr>
 <tr><td><a href="{TRACKED_J2}">ML Engineer</a></td></tr>
 <tr><td>Beta Labs</td></tr><tr><td>3 - 6 years</td></tr><tr><td>12-18 Lacs P.A.</td></tr><tr><td>Pune (Hybrid)</td></tr>
 <tr><td>Machine learning, SQL, MLOps</td></tr><tr><td>Today</td></tr><tr><td><a href="{TRACKED_J2}">View job</a></td></tr>
 <tr><td><a href="https://click.naukri.com/c?u=abc123">View job details</a></td></tr>
 <tr><td><a href="https://www.naukri.com/view-all-jobs?alert=1">View all matching jobs</a></td></tr>
 <tr><td>Manage alerts | <a href="https://www.naukri.com/unsubscribe">Unsubscribe</a> | Jane Sample, jane.sample@example.com</td></tr>
</table></body></html>"""


def test_job_links_are_recognised_direct_relative_and_inside_tracking_redirects_but_never_from_look_alikes():
    assert naukri_job_ref(J1) == ("231126915481", "https://www.naukri.com/job-listings-generative-ai-engineer-bizbrolly-noida-pune-4-to-8-years-231126915481")
    assert naukri_job_ref(TRACKED_J2)[0] == "211125099999"
    assert naukri_job_ref("/job-listings-data-scientist-acme-pune-2-to-5-years-211125054321")[0] == "211125054321"
    assert naukri_job_ref("https://naukri.com.evil.example/job-listings-x-211125099999") is None
    assert naukri_job_ref("https://www.naukri.com/mnjuser/profile") is None


def test_alert_email_is_split_into_jobs_with_links_and_details_and_the_footer_is_ignored():
    jobs, hidden = parse_naukri_alert(ALERT)
    assert [j.source_job_id for j in jobs] == ["231126915481", "211125099999"]
    a, b = jobs
    assert (a.title, a.company, a.location_text, a.posted_raw, a.salary_text) == ("Generative AI Engineer", "BizBrolly", ["Noida, Pune"], "1 day ago", None)
    assert a.url.startswith("https://www.naukri.com/job-listings-generative-ai-engineer") and "?" not in a.url      # tracking parameters dropped
    assert a.description.startswith("Experience: 4-8 Yrs") and "LangChain" in a.description
    assert (b.title, b.company, b.location_text, b.salary_text, b.work_mode_text) == ("ML Engineer", "Beta Labs", ["Pune (Hybrid)"], "12-18 Lacs P.A.", "Hybrid")
    assert a.source == "naukri" and a.provider == "gmail_alert"
    assert hidden == 1                                                                                 # "View job details" hid the job: skipped, not followed
    every = json.dumps([j.model_dump() for j in jobs])
    assert "jane.sample" not in every and "Unsubscribe" not in every and "Manage alerts" not in every


def test_alert_jobs_normalise_like_any_other_job():
    jobs, _ = parse_naukri_alert(ALERT)
    rec = normalize(jobs[0], get_catalog(Settings()))
    assert rec.source == "naukri" and rec.source_job_id == "231126915481" and rec.experience_min == 4 and "noida" in rec.location_keys


def test_plain_text_alert_falls_back_to_urls_in_the_text():
    jobs, _ = parse_naukri_alert("", f"Generative AI Engineer\n{J1}\nBizBrolly\n4-8 Yrs\nPune")
    assert len(jobs) == 1 and jobs[0].source_job_id == "231126915481" and jobs[0].company == "BizBrolly"


def test_sender_checks():
    assert sender_domain('"Naukri" <jobalert@naukri.com>') == "naukri.com"
    assert domain_allowed("alerts.naukri.com", ("naukri.com",)) and not domain_allowed("naukri.com.evil.example", ("naukri.com",))


# ------------------------------------------------------------------------------------------------ adapter (Gmail API mocked)
def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def _message(mid: str, sender: str, html: str) -> dict:
    return {"id": mid, "payload": {"mimeType": "multipart/alternative", "headers": [{"name": "From", "value": sender}, {"name": "Subject", "value": "New jobs"}],
                                   "parts": [{"mimeType": "text/plain", "body": {"data": _b64("plain")}}, {"mimeType": "text/html", "body": {"data": _b64(html)}}]}}


class FakeTokens:
    def _access_token(self):
        return "test-access-token"


def _adapter(tmp_path, handler, status="ENABLED", creds=True):
    store = CredentialStore(tmp_path / "alerts.cred", "s" * 32)
    if creds:
        store.save("client-id", "client-secret", "refresh-token")
    pol = Policy(name="email_alerts", status=SourceStatus(status), allowed_hosts=["gmail.googleapis.com"], requests_per_minute=600, max_requests_per_run=41)
    http = PolicyHttp(pol, httpx.Client(transport=httpx.MockTransport(handler)), RateLimiter(600, 41, clock=lambda: 0.0, sleep=lambda s: None), "UA")
    return JobAlertEmailAdapter(pol, http, store, lookback_days=3, tokens=FakeTokens())


def test_adapter_reads_only_alert_senders_through_the_gmail_api_and_never_contacts_naukri(tmp_path):
    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        if req.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "m1aaaaaa"}, {"id": "m2bbbbbb"}]})
        if req.url.path.endswith("/m1aaaaaa"):
            return httpx.Response(200, json=_message("m1aaaaaa", "Naukri <jobalert@naukri.com>", ALERT))
        return httpx.Response(200, json=_message("m2bbbbbb", "Friend <friend@example.com>", "<a href='" + J1 + "'>x</a>"))

    res = _adapter(tmp_path, handler).search(SearchQuery())
    assert [j.source_job_id for j in res.jobs] == ["231126915481", "211125099999"] and res.errors == []
    assert {r.url.host for r in seen} == {"gmail.googleapis.com"}                                   # never naukri.com, never a link from the mail
    assert seen[0].url.params["q"] == "{from:naukri.com} newer_than:3d" and seen[0].headers["Authorization"] == "Bearer test-access-token"
    assert seen[1].url.params["format"] == "full"
    assert any("not from an alert sender" in n for n in res.notes) and any("1 alert e-mail(s) read" in n for n in res.notes)


def test_adapter_without_authorisation_does_nothing_and_says_how_to_set_it_up(tmp_path):
    def handler(req):  # pragma: no cover - must not be called
        raise AssertionError("no request expected")
    res = _adapter(tmp_path, handler, creds=False).search(SearchQuery())
    assert res.jobs == [] and res.notes == [NOT_SET_UP]


def test_adapter_reports_a_revoked_authorisation_and_respects_the_policy(tmp_path):
    res = _adapter(tmp_path, lambda req: httpx.Response(401, json={})).search(SearchQuery())
    assert res.jobs == [] and "re-run scripts/gmail_alerts_setup.py" in res.errors[0]
    with pytest.raises(PolicyViolation):
        _adapter(tmp_path, lambda req: httpx.Response(200, json={}), status="DISABLED").search(SearchQuery())


def test_policy_file_lists_email_alerts_with_only_the_gmail_api_host():
    p = PolicyRegistry.load(Settings()).get("email_alerts")
    assert p.automated_allowed and p.allowed_hosts == ["gmail.googleapis.com"] and not p.auto_apply and not p.manual_allowed
    naukri = PolicyRegistry.load(Settings()).get("naukri")
    assert not naukri.automated_allowed                                                               # naukri.com itself stays closed to automation
