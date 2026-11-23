"""Phase 9 database validation: structure, indexes, unique constraints, consistency checks, legacy documents, backup and restore.

Everything runs on throw-away databases; the restore target is a SECOND throw-away database. The live database is never read or written.
"""
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from pymongo.errors import DuplicateKeyError

from database.indexes import ensure_indexes
from database.maintenance import BackupError, backup, expected_indexes, restore, validate, verify_backup
from database.models.application import ApplicationRecord, SubmissionResult, idempotency_key
from database.repositories.applications_repository import ApplicationsRepository
from tests.integration.test_phase8_backend import add_job, add_match, app_doc

pytestmark = pytest.mark.integration
UTC = timezone.utc


@pytest.fixture
def second_db(test_db):
    name = f"ai_jobpilot_test_{uuid4().hex[:8]}"
    db = test_db.client[name]
    yield db
    test_db.client.drop_database(name)


def seed(db):
    """A small, internally consistent data set touching every collection that matters."""
    now = datetime.now(UTC)
    db["resumes"].insert_one({"resume_id": "r" * 32, "original_filename": "r.pdf", "uploaded_at": now})
    db["candidate_profiles"].insert_one({"profile_id": "p" * 32, "resume_id": "r" * 32, "profile_version": 1})
    for i in range(6):
        add_job(db, i)
        add_match(db, f"{i:032x}", 70 + i, resume="r" * 32)
    apps = ApplicationsRepository(db)
    out = []
    for i, (state, outcome, sub) in enumerate([("VERIFIED", "confirmed", now), ("SUBMITTED", "user_reported", now), ("READY_FOR_REVIEW", "not_attempted", None), ("MANUAL_HANDOFF_REQUIRED", "not_attempted", None)]):
        a = apps.insert(ApplicationRecord(idempotency_key=idempotency_key("r" * 32, f"{i:032x}"), job_id=f"{i:032x}", resume_id="r" * 32, profile_id="p" * 32, profile_version=1, source="manual",
                                          state=state, submission=SubmissionResult(outcome=outcome, verified_by="user" if outcome == "user_reported" else ("page" if outcome == "confirmed" else None)),
                                          submitted_at=sub, verified_at=now if state == "VERIFIED" else None))
        apps.add_event(a["application_id"], "created", to_state="DISCOVERED")
        apps.add_event(a["application_id"], "prepared", to_state=state)
        out.append(a)
    db["interviews"].insert_one({"interview_id": "i" * 32, "dup_key": "k1", "application_id": out[0]["application_id"], "status": "scheduled", "scheduled_at": now + timedelta(days=1)})
    db["application_queue"].insert_one({"task_id": "t" * 32, "idempotency_key": "tk", "status": "queued", "job_id": "0" * 32, "resume_id": "r" * 32})
    db["automation_counters"].insert_one({"day": "2026-11-13", "metric": "jobs_processed", "count": 3})
    db["scheduler_locks"].insert_one({"name": "job:x", "owner": "o", "expires_at": now + timedelta(minutes=5)})
    return out


# ============================================================================================ structure + indexes
def test_expected_schema_is_defined_and_ensure_indexes_is_idempotent(test_db):
    spec = expected_indexes()
    assert {"jobs", "applications", "application_events", "application_queue", "interviews", "documents", "scheduler_locks", "automation_counters", "notifications", "daily_reports"} <= set(spec)
    assert any(i["unique"] for i in spec["applications"])
    ensure_indexes(test_db)
    before = {c: sorted(i["name"] for i in test_db[c].list_indexes()) for c in spec}
    ensure_indexes(test_db)
    ensure_indexes(test_db)
    assert before == {c: sorted(i["name"] for i in test_db[c].list_indexes()) for c in spec}


def test_a_database_with_the_application_schema_validates_clean(test_db):
    seed(test_db)
    rep = validate(test_db)
    bad = [(c.name, c.detail) for c in rep.checks if c.status == "fail"]
    assert rep.ok and not bad, bad
    names = {c.name for c in rep.checks}
    assert {"connectivity", "indexes:applications", "unique:applications", "no-duplicates:applications.idempotency_key", "applications.state is a known state",
            "SUBMITTED/VERIFIED applications have evidence", "application events are contiguous"} <= names


@pytest.mark.parametrize("col,doc,dup", [
    ("jobs", {"job_id": "x1", "content_hash": "same-hash"}, {"job_id": "x2", "content_hash": "same-hash"}),
    ("applications", {"application_id": "a1", "idempotency_key": "same"}, {"application_id": "a2", "idempotency_key": "same"}),
    ("application_queue", {"task_id": "t1", "idempotency_key": "same"}, {"task_id": "t2", "idempotency_key": "same"}),
    ("interviews", {"interview_id": "i1", "dup_key": "same"}, {"interview_id": "i2", "dup_key": "same"}),
    ("documents", {"document_id": "d1", "group_id": "g", "version": 1}, {"document_id": "d2", "group_id": "g", "version": 1}),
    ("automation_counters", {"day": "d", "metric": "m"}, {"day": "d", "metric": "m"}),
    ("scheduler_locks", {"name": "lock"}, {"name": "lock"}),
    ("candidate_profiles", {"profile_id": "p1", "resume_id": "r1"}, {"profile_id": "p2", "resume_id": "r1"}),
    ("application_events", {"application_id": "a", "seq": 1}, {"application_id": "a", "seq": 1}),
])
def test_unique_constraints_are_enforced_by_the_database(test_db, col, doc, dup):
    test_db[col].insert_one(dict(doc))
    with pytest.raises(DuplicateKeyError):
        test_db[col].insert_one(dict(dup))


# ============================================================================================ consistency checks catch real corruption
def names(rep, status="fail"):
    return {c.name for c in rep.checks if c.status == status}


def test_validation_detects_corruption(test_db):
    apps = seed(test_db)
    col = test_db["applications"]
    col.update_one({"application_id": apps[2]["application_id"]}, {"$set": {"state": "TELEPORTED"}})
    col.update_one({"application_id": apps[1]["application_id"]}, {"$set": {"submission.outcome": "not_attempted"}})          # SUBMITTED without evidence
    col.update_one({"application_id": apps[0]["application_id"]}, {"$set": {"verified_at": None}})                             # VERIFIED without verified_at
    col.update_one({"application_id": apps[3]["application_id"]}, {"$set": {"state": "APPROVED", "approval": None}})
    test_db["application_events"].delete_one({"application_id": apps[0]["application_id"], "seq": 1})                           # a gap in the audit log
    test_db["application_queue"].update_one({}, {"$set": {"status": "weird"}})
    rep = validate(test_db)
    f = names(rep)
    assert {"applications.state is a known state", "SUBMITTED/VERIFIED applications have evidence", "VERIFIED applications have verified_at",
            "APPROVED applications have a recorded approval", "application events are contiguous", "queue.status is a known status"} <= f
    assert not rep.ok


def test_validation_warns_about_orphans_and_stuck_submissions_without_failing(test_db):
    apps = seed(test_db)
    test_db["jobs"].delete_many({"job_id": f"{3:032x}"})
    test_db["interviews"].update_one({}, {"$set": {"application_id": "nonexistent"}})
    test_db["applications"].update_one({"application_id": apps[2]["application_id"]}, {"$set": {"state": "SUBMITTING"}})
    rep = validate(test_db)
    w = names(rep, "warn")
    assert any(n.startswith("references:applications.job_id") for n in w) and any(n.startswith("references:interviews.application_id") for n in w)
    assert "no application is stuck in SUBMITTING" in w and rep.ok


def test_validation_detects_a_dropped_unique_index_and_the_duplicates_it_allowed(test_db):
    ensure_indexes(test_db)
    test_db["interviews"].drop_index("uq_interview_dup")
    test_db["interviews"].insert_many([{"interview_id": "a", "dup_key": "k"}, {"interview_id": "b", "dup_key": "k"}])
    rep = validate(test_db)
    assert "indexes:interviews" in names(rep) and "no-duplicates:interviews.dup_key" in names(rep)


def test_validation_reports_connectivity_failure_instead_of_raising():
    from pymongo import MongoClient
    c = MongoClient("mongodb://127.0.0.1:1/", serverSelectionTimeoutMS=300)
    rep = validate(c["x"])
    assert not rep.ok and [x.name for x in rep.failed] == ["connectivity"] and "mongodb://" not in json.dumps(rep.as_dict())


# ============================================================================================ legacy documents (migration tolerance)
def test_documents_written_before_phase_6_to_8_still_load_and_are_served(test_db):
    legacy = {"application_id": "l" * 32, "idempotency_key": "legacy", "job_id": "j" * 32, "resume_id": "r" * 32, "profile_id": "p" * 32, "profile_version": 1, "source": "manual", "state": "READY_FOR_REVIEW",
              "created_at": datetime.now(UTC), "updated_at": datetime.now(UTC)}                      # no user_notes / dismissed_at / allow_unreviewed / eligibility ...
    test_db["applications"].insert_one(dict(legacy))
    rec = ApplicationRecord.model_validate({k: v for k, v in legacy.items()})
    assert rec.user_notes is None and rec.dismissed_at is None and rec.state == "READY_FOR_REVIEW"
    ensure_indexes(test_db)                                                                              # upgrading the indexes on an existing database works
    from backend.api.routes.applications import get_application_service
    from backend.main import app
    from fastapi.testclient import TestClient
    from tests.integration.test_phase8_backend import FakeManager
    from database.connection import get_database_manager
    app.dependency_overrides[get_database_manager] = lambda: FakeManager(test_db)
    try:
        r = TestClient(app).get("/api/applications/page")
        assert r.status_code == 200 and r.json()["total"] == 1 and r.json()["items"][0]["user_notes"] is None
        assert TestClient(app).get("/api/dashboard/summary").status_code == 200
    finally:
        app.dependency_overrides.clear()


# ============================================================================================ backup + restore
def docs_of(db):
    return {c: sorted(json.dumps(d, sort_keys=True, default=str) for d in db[c].find({})) for c in db.list_collection_names() if not c.startswith("system.")}


@pytest.mark.parametrize("encrypted", [False, True])
def test_backup_and_restore_round_trip_into_a_separate_database(test_db, second_db, tmp_path, encrypted):
    seed(test_db)
    m = backup(test_db, tmp_path / "bk", secret_key="k" * 32, encrypt=encrypted)
    assert m["encrypted"] is encrypted and "scheduler_locks" not in m["collections"] and m["collections"]["applications"]["documents"] == 4
    files = [p.name for p in (tmp_path / "bk").iterdir()]
    assert "manifest.json" in files and all(f.endswith((".jsonl.enc" if encrypted else ".jsonl")) for f in files if f != "manifest.json")
    if encrypted:
        assert b"idempotency_key" not in (tmp_path / "bk" / "applications.jsonl.enc").read_bytes()
    assert verify_backup(tmp_path / "bk", secret_key="k" * 32)["database"] == test_db.name
    out = restore(second_db, tmp_path / "bk", secret_key="k" * 32)
    assert out["restored"]["applications"] == 4 and out["database"] == second_db.name
    src, dst = docs_of(test_db), docs_of(second_db)
    src.pop("scheduler_locks", None)
    assert dst.pop("scheduler_locks") == []                                                              # recreated EMPTY by the index setup: lock rows are never restored
    assert dst == src                                                                                    # every document identical (including _id, dates, nested values)
    rep = validate(second_db)
    assert rep.ok, [(c.name, c.detail) for c in rep.failed]                                              # the restored database is itself valid ...
    with pytest.raises(DuplicateKeyError):                                                               # ... and the unique indexes were recreated
        second_db["applications"].insert_one({"application_id": "zz", "idempotency_key": test_db["applications"].find_one({})["idempotency_key"]})


def test_restore_refuses_a_non_empty_target_and_never_touches_it(test_db, second_db, tmp_path):
    seed(test_db)
    backup(test_db, tmp_path / "bk")
    second_db["jobs"].insert_one({"job_id": "keep", "content_hash": "keep"})
    with pytest.raises(BackupError, match="not empty"):
        restore(second_db, tmp_path / "bk")
    assert second_db["jobs"].count_documents({}) == 1 and "applications" not in second_db.list_collection_names()


def test_a_corrupted_backup_is_detected_before_anything_is_written(test_db, second_db, tmp_path):
    seed(test_db)
    backup(test_db, tmp_path / "bk")
    f = tmp_path / "bk" / "jobs.jsonl"
    f.write_bytes(f.read_bytes().replace(b"Co1", b"Co9", 1) if b"Co1" in f.read_bytes() else f.read_bytes() + b"\n")
    with pytest.raises(BackupError, match="checksum"):
        restore(second_db, tmp_path / "bk")
    assert second_db.list_collection_names() == []                                                       # nothing was restored
    (tmp_path / "bk" / "manifest.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(BackupError, match="manifest"):
        verify_backup(tmp_path / "bk")
    shutil.rmtree(tmp_path / "bk2", ignore_errors=True)
    backup(test_db, tmp_path / "bk2")
    (tmp_path / "bk2" / "applications.jsonl").unlink()
    with pytest.raises(BackupError, match="missing"):
        verify_backup(tmp_path / "bk2")


def test_an_encrypted_backup_needs_the_right_key(test_db, second_db, tmp_path):
    seed(test_db)
    backup(test_db, tmp_path / "bk", secret_key="right-key-0123456789", encrypt=True)
    with pytest.raises(BackupError, match="decrypt"):
        restore(second_db, tmp_path / "bk", secret_key="wrong-key-0123456789")
    assert second_db.list_collection_names() == []


def test_backups_are_never_overwritten_and_a_failed_backup_leaves_nothing(test_db, tmp_path):
    seed(test_db)
    backup(test_db, tmp_path / "bk")
    with pytest.raises(BackupError, match="never overwritten"):
        backup(test_db, tmp_path / "bk")
    with pytest.raises(Exception):
        backup(test_db, tmp_path / "enc", encrypt=True, secret_key="")                                   # encryption requested without a key
    assert not (tmp_path / "enc").exists() or not list((tmp_path / "enc").iterdir())


def test_restore_over_an_existing_database_only_with_the_explicit_override(test_db, second_db, tmp_path):
    seed(test_db)
    backup(test_db, tmp_path / "bk")
    restore(second_db, tmp_path / "bk")
    second_db["jobs"].update_one({}, {"$set": {"job_title": "TAMPERED"}})
    restore(second_db, tmp_path / "bk", allow_non_empty=True)
    assert second_db["jobs"].count_documents({"job_title": "TAMPERED"}) == 0 and second_db["jobs"].count_documents({}) == 6   # replaced, not duplicated


def test_the_cli_refuses_to_restore_into_the_live_database(test_db, tmp_path, capsys):
    from config.settings import get_settings
    import importlib.util
    spec = importlib.util.spec_from_file_location("db_tools", Path(__file__).resolve().parents[2] / "scripts" / "db_tools.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    seed(test_db)
    backup(test_db, tmp_path / "bk")
    assert mod.main(["restore", "--from", str(tmp_path / "bk"), "--target-db", get_settings().mongodb_database]) == 2
    assert "Refusing" in capsys.readouterr().out
