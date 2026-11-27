"""Rejection tracking on a throw-away database with a FAKE Gmail reader (made-up e-mails; the real mailbox and credentials are never used)."""
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.api.routes.applications import get_rejection_service
from backend.main import app
from backend.services.rejection_service import RejectionService
from config.settings import get_settings
from database.models.application import ApplicationRecord, idempotency_key
from database.repositories.applications_repository import ApplicationsRepository
from integrations.email.gmail_service import MailMessage

pytestmark = pytest.mark.integration
AT = datetime(2026, 11, 24, 9, 30, tzinfo=timezone.utc)


class FakeReader:
    def __init__(self, mails: dict[str, tuple]):
        self.mails, self.reads, self.queries = mails, [], []

    def search_query(self, query, limit=25):
        self.queries.append(query)
        return list(self.mails)[:limit]

    def read_message(self, mid):
        self.reads.append(mid)
        subject, text, domain, name = self.mails[mid]
        return MailMessage(mid, domain, name, subject, "", text, AT)


def _app(db, company, title, state="SUBMITTED"):
    job = uuid4().hex
    rec = ApplicationRecord(idempotency_key=idempotency_key("r" * 32, job), job_id=job, resume_id="r" * 32, profile_id="p" * 32, profile_version=1,
                            source="linkedin", state=state, job_title=title, company_name=company)
    ApplicationsRepository(db).insert(rec)
    return rec.application_id


MAILS = {
    "m00000001": ("Your application to Machine Learning Engineer at Acme Analytics", "Unfortunately, we will not be moving forward with your application.", "linkedin.com", "LinkedIn"),
    "m00000002": ("Update on your application", "Thank you for your interest in Beta Labs. We regret to inform you that we will not proceed with your application.", "greenhouse.io", "Beta Labs Recruiting"),
    "m00000003": ("Your application to Python Developer at Nowhere Corp", "Unfortunately we are not moving forward with your application.", "nowhere.com", "Nowhere HR"),
    "m00000004": ("Team offsite", "Unfortunately the bus is late.", "friends.example", "Pat"),
}


@pytest.fixture
def env(test_db):
    a1 = _app(test_db, "Acme Analytics Pvt Ltd", "Machine Learning Engineer")
    b1 = _app(test_db, "Beta Labs", "Data Scientist", "VERIFIED")
    b2 = _app(test_db, "Beta Labs", "ML Ops Engineer", "VERIFIED")
    reader = FakeReader(MAILS)
    svc = RejectionService(test_db, get_settings(), reader=reader)
    return svc, reader, {"a1": a1, "b1": b1, "b2": b2}, test_db


def test_scan_marks_sure_matches_and_queues_the_rest(env):
    svc, reader, ids, db = env
    s = svc.scan()
    assert (s["read"], s["auto_marked"], s["needs_confirmation"], s["unmatched"], s["not_rejection"]) == (4, 1, 1, 1, 1)
    a1 = ApplicationsRepository(db).get(ids["a1"])
    assert a1["employer_response"]["outcome"] == "rejected" and a1["employer_response"]["source"] == "email"
    assert a1["employer_response"]["company"] == "Acme Analytics" and a1["state"] == "SUBMITTED"        # sending state untouched
    assert ApplicationsRepository(db).get(ids["b1"]).get("employer_response") is None                  # not sure -> not changed
    ov = svc.overview()
    assert len(ov["needs_confirmation"]) == 1 and {c["application_id"] for c in ov["needs_confirmation"][0]["candidates"]} == {ids["b1"], ids["b2"]}
    assert ov["unmatched"][0]["company"] == "Nowhere Corp" and ov["rejected_total"] == 1 and ov["last_check"]["auto_marked"] == 1
    events = [e["type"] for e in ApplicationsRepository(db).list_events(ids["a1"])]
    assert "employer_rejected" in events


def test_nothing_from_the_mail_body_is_stored(env):
    svc, reader, ids, db = env
    svc.scan()
    for row in db["rejection_emails"].find({}, {"_id": 0}):
        assert "message_id" not in row and not any(isinstance(v, str) and "bus is late" in v for v in row.values())
        assert set(row) <= {"signal_id", "message_key", "created_at", "status", "received_at", "company", "role", "phrase", "sender_domain", "candidates",
                            "reason", "application_id", "decided_at", "decided_by"}


def test_each_mail_is_read_once(env):
    svc, reader, ids, db = env
    svc.scan()
    s = svc.scan()
    assert s["read"] == 0 and s["already_seen"] == 4 and len(reader.reads) == 4


def test_confirm_dismiss_and_undo(env):
    svc, reader, ids, db = env
    svc.scan()
    ov = svc.overview()
    sig = ov["needs_confirmation"][0]
    svc.confirm(sig["signal_id"], ids["b1"])
    assert ApplicationsRepository(db).get(ids["b1"])["employer_response"]["outcome"] == "rejected"
    with pytest.raises(Exception):
        svc.confirm(sig["signal_id"], ids["b2"])                    # already handled
    svc.dismiss(ov["unmatched"][0]["signal_id"])
    assert svc.overview()["unmatched"] == []
    svc.undo(ids["a1"])
    assert ApplicationsRepository(db).get(ids["a1"]).get("employer_response") is None
    assert svc.scan()["auto_marked"] == 0                           # an undone e-mail is not applied again


def test_manual_mark_and_page_filter(env):
    svc, reader, ids, db = env
    svc.mark_rejected(ids["b2"])
    repo = ApplicationsRepository(db)
    assert [r["application_id"] for r in repo.find_all(employer_outcome="rejected")] == [ids["b2"]]
    assert repo.count(employer_outcome="none") == 2


def test_not_connected_does_nothing(test_db):
    s = RejectionService(test_db, get_settings()).scan()
    assert s["status"] == "not_set_up" and s["read"] == 0


def test_api(env):
    svc, reader, ids, db = env
    app.dependency_overrides[get_rejection_service] = lambda: svc
    try:
        c = TestClient(app)
        svc.scan()
        ov = c.get("/api/applications/rejections").json()
        sig = ov["needs_confirmation"][0]["signal_id"]
        assert c.post(f"/api/applications/rejections/{sig}/confirm", json={"application_id": ids["b2"]}).status_code == 200
        assert c.post(f"/api/applications/rejections/{sig}/dismiss").status_code == 409
        assert c.post(f"/api/applications/{ids['b1']}/employer-rejected").json()["employer_response"]["source"] == "candidate"
        assert c.post(f"/api/applications/{ids['b1']}/employer-rejected/undo").status_code == 200
        assert c.post(f"/api/applications/{ids['b1']}/employer-rejected/undo").status_code == 409
    finally:
        app.dependency_overrides.pop(get_rejection_service, None)


def test_add_an_outside_application_as_applied_and_rejected(env):
    svc, reader, ids, db = env
    svc.scan()
    sig = svc.overview()["unmatched"][0]                                   # "Python Developer at Nowhere Corp" - applied outside JobPilot
    calls = []

    def import_job(fields, source):
        calls.append((fields, source))
        return {"job_id": "j" * 32}

    def record_applied(job_id, notes):
        new = _app(db, "Nowhere Corp", "Python Developer")
        return {"application_id": new}

    out = svc.add_as_applied(sig["signal_id"], import_job=import_job, record_applied=record_applied)
    assert calls == [({"job_title": "Python Developer", "company_name": "Nowhere Corp"}, None)]   # only what the e-mail names; nothing fetched
    app_rec = ApplicationsRepository(db).get(out["application_id"])
    assert app_rec["employer_response"]["outcome"] == "rejected" and app_rec["employer_response"]["company"] == "Nowhere Corp"
    assert svc.overview()["unmatched"] == []
    with pytest.raises(Exception):
        svc.add_as_applied(sig["signal_id"], import_job=import_job, record_applied=record_applied)   # already handled: never added twice


def test_add_as_applied_needs_company_and_role(env):
    svc, reader, ids, db = env
    sid = svc.rej.insert("m-no-role", {"status": "unmatched", "company": "Somewhere", "role": None, "sender_domain": "somewhere.com"})["signal_id"]
    with pytest.raises(Exception, match="company and the role"):
        svc.add_as_applied(sid, import_job=lambda f, s: {"job_id": "x"}, record_applied=lambda j, n: {"application_id": "y"})
