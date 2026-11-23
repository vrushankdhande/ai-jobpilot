"""MongoDB behaviour of applications: uniqueness, atomic transitions, an append-only event log, and erasure of personal values."""
import threading

import pytest
from pymongo.errors import DuplicateKeyError

from ai.schemas.candidate_profile import new_id
from database.models.application import ApplicationRecord, FieldMapping, idempotency_key
from database.repositories.applications_repository import ApplicationsRepository
from security.audit_logger import AuditLogger

pytestmark = pytest.mark.integration


def rec(job="j" * 32, resume="r" * 32, **kw):
    return ApplicationRecord(idempotency_key=idempotency_key(resume, job), job_id=job, resume_id=resume, profile_id="p" * 32, profile_version=1,
                             source="manual", mapping=[FieldMapping(field_id="0:0", label="Email", value="jane@example.com")], **kw)


def test_one_application_per_resume_and_job(test_db):
    repo = ApplicationsRepository(test_db)
    first = repo.insert(rec())
    with pytest.raises(DuplicateKeyError):
        repo.insert(rec())                                                  # the unique idempotency key is the database-level duplicate guard
    assert repo.insert(rec(job="k" * 32))["application_id"] != first["application_id"]
    assert repo.find_by_key(idempotency_key("r" * 32, "j" * 32))["application_id"] == first["application_id"]


def test_transition_is_an_atomic_compare_and_set(test_db):
    repo = ApplicationsRepository(test_db)
    a = repo.insert(rec(state="APPROVED"))
    wins = []

    def go():
        wins.append(repo.transition(a["application_id"], ["APPROVED"], "SUBMITTING") is not None)
    ts = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert wins.count(True) == 1 and repo.get(a["application_id"])["state"] == "SUBMITTING"
    assert repo.transition(a["application_id"], ["APPROVED"], "SUBMITTING") is None


def test_events_are_appended_in_order_and_unique_per_sequence(test_db):
    repo = ApplicationsRepository(test_db)
    aid = new_id()
    audit = AuditLogger(repo)
    for t in ("created", "prepared", "approved"):
        audit.record(aid, t, actor="candidate")
    evs = repo.list_events(aid)
    assert [e["type"] for e in evs] == ["created", "prepared", "approved"] and [e["seq"] for e in evs] == [1, 2, 3]
    with pytest.raises(DuplicateKeyError):                                   # the (application_id, seq) index refuses an overwrite
        test_db["application_events"].insert_one({"application_id": aid, "seq": 2, "type": "forged"})
    results = []
    ts = [threading.Thread(target=lambda: results.append(repo.add_event(aid, "x")["seq"])) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(results) == [4, 5, 6, 7, 8, 9]                              # concurrent writers never collide or lose an event


def test_the_repository_offers_no_way_to_change_or_delete_events():
    names = {n for n in dir(ApplicationsRepository) if not n.startswith("_")}
    assert not {n for n in names if "event" in n and any(w in n for w in ("update", "delete", "remove", "edit", "set", "clear"))}
    import inspect
    src = inspect.getsource(ApplicationsRepository)
    for forbidden in ("events.update", "events.delete", "events.replace", "events.drop", "events.find_one_and"):
        assert forbidden not in src


def test_audit_events_contain_no_personal_data(test_db):
    repo = ApplicationsRepository(test_db)
    aid = new_id()
    AuditLogger(repo).record(aid, "prepared", email="jane@example.com", full_name="Jane Sample", reason="mail jane@example.com", count=2)
    ev = repo.list_events(aid)[0]
    assert "jane" not in str(ev).lower() and ev["detail"]["count"] == 2


def test_deleting_a_resume_erases_personal_values_but_keeps_the_audit_history(test_db, seeded, tmp_path):
    from backend.services.resume_service import ResumeService
    from config.settings import get_settings
    from database.repositories.resumes_repository import ResumesRepository
    from database.repositories.users_repository import UsersRepository
    repo = ApplicationsRepository(test_db)
    settings = get_settings().model_copy(update={"screenshots_dir": str(tmp_path / "shots")})
    from browser.screenshot_manager import ScreenshotManager
    sid = ScreenshotManager(settings.screenshots_path).save(b"\x89PNG-x", "a" * 32)
    a = repo.insert(rec(resume=seeded.resume_id, screenshots=[sid], state="VERIFIED", inputs={"email": "jane@example.com"}))
    repo.add_event(a["application_id"], "verified")
    # the resume record itself must exist for the service call
    ResumesRepository(test_db).create_resume({"resume_id": seeded.resume_id, "stored_filename": "x.pdf", "original_filename": "x.pdf", "uploaded_at": None})
    svc = ResumeService(ResumesRepository(test_db), UsersRepository(test_db), settings, storage_dir=tmp_path / "store")
    svc.delete(seeded.resume_id)
    after = repo.get(a["application_id"])
    assert after["mapping"] == [] and after["inputs"] == {} and after["screenshots"] == [] and after["state"] == "VERIFIED"
    assert not (tmp_path / "shots" / sid).exists() and len(repo.list_events(a["application_id"])) == 1
