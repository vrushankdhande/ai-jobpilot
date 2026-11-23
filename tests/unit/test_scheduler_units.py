"""Phase 7 unit tests (no MongoDB, no browser, no network): time zones and slots, limits, backoff, error classes, Gmail transport (mocked HTTP)."""
import base64
import email
import json
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest

from config.automation_config import ADJUSTABLE, AutomationLimits, effective, in_window, load_limits
from config.settings import Settings, get_settings
from notifications.email_notifications import (
    CredentialStore, EmailNotifier, GmailAuthError, GmailTemporaryError, GmailTransport, mask_email,
)
from notifications.notification_templates import daily_report, discovery_summary
from scheduler.application_queue import priority_for
from scheduler.application_worker import backoff_seconds, classify
from scheduler.scheduler import JobSpec, Scheduler, get_zone, local_day, next_slot, slot_at_or_before

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc


def utc(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


# ============================================================================================ time zones and slots
def test_default_timezone_is_asia_kolkata_and_configurable():
    assert get_settings().scheduler_timezone == "Asia/Kolkata" and load_limits(get_settings()).timezone == "Asia/Kolkata"
    assert get_zone("Asia/Kolkata").key == "Asia/Kolkata" and get_zone("America/New_York").key == "America/New_York"
    assert get_zone("Not/AZone").key == "Asia/Kolkata" and get_zone(None).key == "Asia/Kolkata"
    assert load_limits(get_settings().model_copy(update={"scheduler_timezone": "Europe/London"})).timezone == "Europe/London"


def test_slot_is_computed_in_local_time_not_utc():
    now = utc(2026, 11, 13, 1, 45)                                    # 07:15 IST
    slot = slot_at_or_before("07:00", now, IST)
    assert slot == utc(2026, 11, 13, 1, 30) and slot.astimezone(IST).hour == 7                 # 07:00 IST = 01:30 UTC
    before = utc(2026, 11, 13, 1, 0)                                  # 06:30 IST: today's slot is still ahead -> yesterday's is the latest
    assert slot_at_or_before("07:00", before, IST) == utc(2026, 11, 12, 1, 30)
    assert next_slot("07:00", before, IST) == utc(2026, 11, 13, 1, 30) and next_slot("07:00", now, IST) == utc(2026, 11, 14, 1, 30)
    assert slot_at_or_before("07:00", now, ZoneInfo("America/New_York")) != slot                 # another zone, another instant


def test_local_day_follows_the_zone_across_midnight():
    assert local_day(utc(2026, 11, 13, 18, 29), IST) == "2026-11-13" and local_day(utc(2026, 11, 13, 18, 31), IST) == "2026-11-14"   # IST midnight = 18:30 UTC


class _Repo:   # is_due/next_run never touch the database
    pass


def sched(grace=240, **kw):
    return Scheduler(_Repo(), IST, [], catchup_grace_minutes=grace, **kw)


def test_daily_job_is_due_once_per_slot():
    s, spec = sched(), JobSpec("d", lambda t: {}, at="07:00")
    t0 = utc(2026, 11, 13, 1, 31)                                     # one minute after the slot
    assert s.is_due(spec, None, t0) is True
    assert s.is_due(spec, t0, t0 + timedelta(hours=1)) is False        # already ran for today's slot
    assert s.is_due(spec, utc(2026, 11, 12, 1, 31), t0) is True        # last ran yesterday
    assert s.is_due(spec, utc(2026, 11, 12, 1, 31), utc(2026, 11, 13, 1, 29)) is False   # slot not reached yet
    assert s.next_run(spec, t0, t0 + timedelta(hours=1)) == utc(2026, 11, 14, 1, 30)


def test_missed_slot_is_made_up_only_within_the_grace_period():
    s, spec = sched(grace=240), JobSpec("d", lambda t: {}, at="07:00")
    slot = utc(2026, 11, 13, 1, 30)
    assert s.is_due(spec, None, slot + timedelta(minutes=239)) is True          # restarted 3h59m late: catch up once
    assert s.is_due(spec, None, slot + timedelta(minutes=241)) is False         # too stale: skip, wait for tomorrow
    assert s.next_run(spec, None, slot + timedelta(hours=5)) == utc(2026, 11, 14, 1, 30)
    assert sched(grace=0).is_due(spec, None, slot + timedelta(minutes=1)) is False


def test_interval_job_due_logic_and_naive_datetimes_are_treated_as_utc():
    s, spec = sched(), JobSpec("w", lambda t: {}, every_seconds=60)
    now = utc(2026, 11, 13, 5, 0)
    assert s.is_due(spec, None, now) and not s.is_due(spec, now - timedelta(seconds=59), now) and s.is_due(spec, now - timedelta(seconds=60), now)
    assert s.is_due(spec, (now - timedelta(seconds=90)).replace(tzinfo=None), now)
    assert s.next_run(spec, now, now) == now + timedelta(seconds=60) and sched().next_run(JobSpec("x", lambda t: {}), None, now) is None


# ============================================================================================ limits
def test_windows_including_overnight():
    assert in_window(("09:00", "21:00"), time(9, 0)) and in_window(("09:00", "21:00"), time(21, 0)) and not in_window(("09:00", "21:00"), time(8, 59))
    assert in_window(("22:00", "06:00"), time(23, 30)) and in_window(("22:00", "06:00"), time(5, 0)) and not in_window(("22:00", "06:00"), time(12, 0))


def test_yaml_limits_load_with_the_documented_defaults():
    lim = load_limits(get_settings())
    assert (lim.max_applications_per_day, lim.max_applications_per_platform_per_day) == (20, 10)                       # Phase 6 keys untouched
    assert 20 <= lim.daily_application_target <= 30 and lim.max_jobs_discovered_per_day > 0 and lim.max_jobs_processed_per_day > 0
    assert lim.max_retries_per_application >= 0 and lim.max_concurrent_workers >= 1 and 0 <= lim.min_match_score <= 100
    assert lim.discovery_window and lim.application_window and lim.discovery_time == "07:00" and lim.report_time and lim.timezone == "Asia/Kolkata"


def test_invalid_or_missing_limits_file_falls_back_safely(tmp_path):
    bad = tmp_path / "l.yaml"
    bad.write_text("max_applications_per_day: [not, a, number]\n", encoding="utf-8")
    s = get_settings().model_copy(update={"automation_limits_file": str(bad)})
    assert load_limits(s) == AutomationLimits(timezone=load_limits(s).timezone) or load_limits(s).max_applications_per_day == 20
    missing = get_settings().model_copy(update={"automation_limits_file": str(tmp_path / "nope.yaml")})
    assert load_limits(missing).max_applications_per_day == 20
    bad.write_text("discovery_window: ['25:99', '10:00']\n", encoding="utf-8")
    assert load_limits(s).discovery_window == AutomationLimits().discovery_window


def test_dashboard_overrides_can_only_lower_the_caps():
    caps = AutomationLimits(max_applications_per_day=20, max_jobs_processed_per_day=60, max_concurrent_workers=2, min_match_score=75.0, max_retries_per_application=3)
    eff = effective(caps, {"max_applications_per_day": 5, "max_jobs_processed_per_day": 500, "max_concurrent_workers": 0, "min_match_score": 60, "max_retries_per_application": 1})
    assert eff.max_applications_per_day == 5                       # lowered
    assert eff.max_jobs_processed_per_day == 60                    # cannot exceed the YAML cap
    assert eff.max_concurrent_workers == 1                         # never below 1
    assert eff.min_match_score == 75.0                             # the threshold can only be raised, not relaxed below the YAML value
    assert effective(caps, {"min_match_score": 90}).min_match_score == 90 and eff.max_retries_per_application == 1
    junk = effective(caps, {"unknown": 1, "max_applications_per_day": True, "max_jobs_processed_per_day": "9", "min_match_score": None})
    assert junk == caps and effective(caps, None) == caps
    assert isinstance(effective(caps, {"max_applications_per_day": 7.9}).max_applications_per_day, int) and set(ADJUSTABLE) >= {"min_match_score"}


# ============================================================================================ retry maths, classification, priority
def test_backoff_doubles_and_is_capped():
    assert [backoff_seconds(60, n) for n in (1, 2, 3, 4)] == [60, 120, 240, 480] and backoff_seconds(60, 20) == 3600 and backoff_seconds(0, 3) == 0


def test_failure_classification():
    from backend.services.application_service import ActionNotAllowed, DuplicateApplication, NotEligible
    from backend.services.matching_service import UnreviewedProfile
    from browser.browser_manager import BrowserError
    from integrations.policy import PolicyViolation
    for exc in (NotEligible("x"), ActionNotAllowed("x"), DuplicateApplication("x"), PolicyViolation("x"), UnreviewedProfile("x"), ValueError("x"), BrowserError("x")):
        assert classify(exc) == "permanent", exc
    for exc in (httpx.ConnectError("x"), TimeoutError(), ConnectionResetError(), BrowserError("slow", transient=True)):
        assert classify(exc) == "transient", exc
    assert classify(RuntimeError("boom")) == "recoverable" and classify(KeyError("k")) == "recoverable"


def test_priority_prefers_score_then_confirmed_eligibility():
    assert priority_for(90, "UNKNOWN") < priority_for(90, "PASS") and priority_for(95, "UNKNOWN") > priority_for(80, "UNKNOWN")
    assert priority_for(80, "PASS") > priority_for(85, "UNKNOWN")


# ============================================================================================ gmail pieces
def test_mask_email():
    assert mask_email("jane.sample@example.com") == "j***@example.com" and mask_email("") == "***" and mask_email("nonsense") == "***"


def settings_with(tmp_path, **kw):
    return get_settings().model_copy(update={"secret_key": "k" * 32, "notification_credentials_file": str(tmp_path / "gmail.cred"), **kw})


def test_credentials_are_encrypted_at_rest_and_need_the_secret(tmp_path):
    s = settings_with(tmp_path)
    store = CredentialStore(s.notification_credentials_path, s.secret_key)
    store.save("client-id-123", "client-secret-XYZ", "refresh-token-ABC")
    raw = s.notification_credentials_path.read_bytes()
    assert all(x.encode() not in raw for x in ("client-id-123", "client-secret-XYZ", "refresh-token-ABC", "client_id", "refresh_token"))
    assert store.load() == {"client_id": "client-id-123", "client_secret": "client-secret-XYZ", "refresh_token": "refresh-token-ABC"}
    assert CredentialStore(s.notification_credentials_path, "z" * 32).load() is None and CredentialStore(s.notification_credentials_path, "").load() is None
    with pytest.raises(ValueError):
        store.save("", "x", "y")
    assert store.delete() and store.load() is None and not store.exists()


def gmail_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def stored(tmp_path):
    s = settings_with(tmp_path)
    st = CredentialStore(s.notification_credentials_path, s.secret_key)
    st.save("cid", "csecret", "rtoken")
    return st


def test_transport_refreshes_the_token_then_sends_and_caches_the_token(tmp_path):
    calls = []

    def handler(req: httpx.Request):
        calls.append((req.url.host, req.url.path))
        if req.url.host == "oauth2.googleapis.com":
            body = req.content.decode()
            assert "grant_type=refresh_token" in body and "refresh_token=rtoken" in body
            return httpx.Response(200, json={"access_token": "ACCESS", "expires_in": 3600})
        assert req.headers["authorization"] == "Bearer ACCESS" and req.url.path == "/gmail/v1/users/me/messages/send"
        raw = base64.urlsafe_b64decode(json.loads(req.content)["raw"])
        assert b"Subject: Hello" in raw
        return httpx.Response(200, json={"id": "msg-1"})
    t = GmailTransport(stored(tmp_path), gmail_client(handler))
    assert t.send(b"To: a@b.com\r\nSubject: Hello\r\n\r\nbody") == "msg-1" and t.send(b"To: a@b.com\r\nSubject: Hello\r\n\r\nbody") == "msg-1"
    assert [h for h, _ in calls].count("oauth2.googleapis.com") == 1 and {h for h, _ in calls} == {"oauth2.googleapis.com", "gmail.googleapis.com"}   # only Google hosts


def test_transport_refreshes_once_when_the_access_token_expired_and_retries_the_same_send(tmp_path):
    state = {"tokens": 0, "sends": 0}

    def handler(req):
        if req.url.host == "oauth2.googleapis.com":
            state["tokens"] += 1
            return httpx.Response(200, json={"access_token": f"T{state['tokens']}", "expires_in": 3600})
        state["sends"] += 1
        return httpx.Response(401) if req.headers["authorization"] == "Bearer T1" else httpx.Response(200, json={"id": "ok"})
    assert GmailTransport(stored(tmp_path), gmail_client(handler)).send(b"x") == "ok" and state == {"tokens": 2, "sends": 2}


@pytest.mark.parametrize("token_status,send_status,exc", [(400, None, GmailAuthError), (401, None, GmailAuthError), (500, None, GmailTemporaryError), (429, None, GmailTemporaryError),
                                                          (200, 500, GmailTemporaryError), (200, 429, GmailTemporaryError), (200, 403, GmailAuthError), (200, 400, GmailAuthError)])
def test_transport_error_classes(tmp_path, token_status, send_status, exc):
    def handler(req):
        if req.url.host == "oauth2.googleapis.com":
            return httpx.Response(token_status, json={"access_token": "A", "expires_in": 3600} if token_status == 200 else {"error": "x"})
        return httpx.Response(send_status, json={"error": "x"})
    with pytest.raises(exc):
        GmailTransport(stored(tmp_path), gmail_client(handler)).send(b"x")


def test_network_errors_are_temporary_and_missing_credentials_are_auth_errors(tmp_path):
    def boom(req):
        raise httpx.ConnectError("down")
    with pytest.raises(GmailTemporaryError):
        GmailTransport(stored(tmp_path), gmail_client(boom)).send(b"x")
    with pytest.raises(GmailAuthError):
        GmailTransport(CredentialStore(tmp_path / "none.cred", "k" * 32), gmail_client(lambda r: httpx.Response(200))).send(b"x")


def test_error_messages_never_contain_credentials(tmp_path):
    def handler(req):
        return httpx.Response(401, text="refresh-token rtoken csecret")
    try:
        GmailTransport(stored(tmp_path), gmail_client(handler)).send(b"x")
    except GmailAuthError as exc:
        assert "rtoken" not in str(exc) and "csecret" not in str(exc)


def test_message_building_and_status_never_expose_secrets(tmp_path):
    s = settings_with(tmp_path, notifications_enabled=True, notification_recipient="me@example.com")
    n = EmailNotifier(s, repo=None, transport=object())
    msg = email.message_from_bytes(n._message("Subject\nwith newline", "body text"))
    assert msg["To"] == "me@example.com" and "\n" not in msg["Subject"] and msg.get_payload().strip() == "body text"
    st = n.status()
    assert st == {"enabled": True, "recipient_set": True, "recipient": "m***@example.com", "credentials_stored": False, "configured": True}
    assert not EmailNotifier(settings_with(tmp_path, notifications_enabled=False, notification_recipient="me@example.com"), None, object()).is_configured()
    assert not EmailNotifier(settings_with(tmp_path, notifications_enabled=True, notification_recipient="not-an-email"), None, object()).is_configured()
    assert not EmailNotifier(settings_with(tmp_path, notifications_enabled=True, notification_recipient="me@example.com"), None, None).is_configured()   # no credentials, no transport


def test_email_templates_contain_counts_not_personal_data():
    rep = {"date": "2026-11-13", "timezone": "Asia/Kolkata", "discovery": {"discovered": 12, "duplicates_removed": 3, "by_source": {"career_pages": 12}},
           "matching": {"matched": 12, "excellent": 1, "strong": 2, "good": 3, "eligible": 4}, "applications": {"awaiting_approval": 2, "submitted": 1, "verified": 1, "failed": 0,
                                                                                                              "manual_handoff": 1, "pending_verification": 0},
           "limits": {"jobs_discovered": {"used": 12, "limit": 300}}}
    subject, body = daily_report(rep)
    assert "1 submitted" in subject and "only confirmed submissions" in body and "12 / 300" in body and "@" not in body
    s2, b2 = discovery_summary({"totals": {"new": 5, "duplicates": 2, "fetched": 9}, "queued": 3, "matched": 5})
    assert "5 new jobs" in s2 and "Nothing is applied to automatically" in b2
