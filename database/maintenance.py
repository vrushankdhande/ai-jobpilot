"""Database validation, backup and restore (pure Python: no MongoDB command-line tools needed).

* ``validate`` - connectivity, collections/indexes against what ``ensure_indexes`` defines, uniqueness, valid states, cross-collection references and
  application-state consistency. Read-only. Returns a report; nothing is repaired automatically.
* ``backup`` - one JSON-lines file per collection (MongoDB extended JSON, so dates/ObjectIds survive), a manifest with counts and SHA-256, optionally
  Fernet-encrypted with a key derived from SECRET_KEY. Backups contain personal data: store them like the database itself.
* ``restore`` - verifies every checksum BEFORE writing, refuses a non-empty target unless told otherwise, recreates the indexes and checks the counts.
"""
import hashlib
import json
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from bson import json_util
from cryptography.fernet import Fernet, InvalidToken
from pymongo.database import Database

from database.indexes import ensure_indexes
from security.secrets_manager import derive_fernet_key

logger = logging.getLogger(__name__)
MANIFEST = "manifest.json"
BACKUP_FORMAT = 1
TRANSIENT_COLLECTIONS = {"scheduler_locks"}          # locks describe a running process, never data worth restoring


# ------------------------------------------------------------------------------------------------------------ expected schema
class _Recorder:
    """A stand-in Database that records the indexes ``ensure_indexes`` would create."""

    def __init__(self):
        self.specs: dict[str, list[dict]] = {}

    def __getitem__(self, name):
        rec = self

        class _Col:
            def create_index(self, keys, **kw):
                rec.specs.setdefault(name, []).append({"name": kw.get("name"), "unique": bool(kw.get("unique")), "keys": [k for k, _ in keys]})
        return _Col()


def expected_indexes() -> dict[str, list[dict]]:
    r = _Recorder()
    ensure_indexes(r)  # type: ignore[arg-type]
    return r.specs


# ------------------------------------------------------------------------------------------------------------ validation
@dataclass
class Check:
    name: str
    status: str                  # pass | fail | warn
    detail: str = ""


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "", *, warn: bool = False) -> None:
        self.checks.append(Check(name, "pass" if ok else ("warn" if warn else "fail"), "" if ok else detail))

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if c.status == "fail"]

    @property
    def ok(self) -> bool:
        return not self.failed

    def as_dict(self) -> dict:
        return {"ok": self.ok, "passed": sum(c.status == "pass" for c in self.checks), "warnings": sum(c.status == "warn" for c in self.checks), "failed": len(self.failed),
                "checks": [c.__dict__ for c in self.checks]}


def _ids(db: Database, col: str, field_: str) -> set:
    return {d[field_] for d in db[col].find({field_: {"$ne": None}}, {"_id": 0, field_: 1}) if d.get(field_)}


def validate(db: Database) -> Report:
    from engines.application.state_machine import SUBMISSION_MAY_EXIST, AppState
    from database.repositories.queue_repository import ALL_STATUSES
    rep = Report()
    try:
        db.client.admin.command("ping")
        rep.add("connectivity", True)
    except Exception as exc:  # noqa: BLE001
        rep.add("connectivity", False, f"MongoDB did not answer ({type(exc).__name__})")
        return rep
    # ---- schema: indexes (names + uniqueness)
    names = set(db.list_collection_names())
    for col, specs in expected_indexes().items():
        have = {i["name"]: i for i in db[col].list_indexes()} if col in names else {}
        missing = [s["name"] for s in specs if s["name"] not in have]
        rep.add(f"indexes:{col}", not missing, f"missing: {', '.join(missing)}" + ("" if col in names else " (collection does not exist yet - run the API once)"), warn=col not in names)
        not_unique = [s["name"] for s in specs if s["unique"] and s["name"] in have and not have[s["name"]].get("unique")]
        rep.add(f"unique:{col}", not not_unique, f"not unique: {', '.join(not_unique)}")
    # ---- duplicates that the unique indexes should make impossible (detects an index that was dropped)
    for col, key in (("applications", "idempotency_key"), ("application_queue", "idempotency_key"), ("jobs", "content_hash"), ("interviews", "dup_key"), ("documents", "document_id"),
                     ("candidate_profiles", "resume_id")):
        dup = list(db[col].aggregate([{"$match": {key: {"$ne": None}}}, {"$group": {"_id": f"${key}", "n": {"$sum": 1}}}, {"$match": {"n": {"$gt": 1}}}, {"$limit": 3}]))
        rep.add(f"no-duplicates:{col}.{key}", not dup, f"{len(dup)}+ duplicated values")
    dup_pair = list(db["documents"].aggregate([{"$group": {"_id": {"g": "$group_id", "v": "$version"}, "n": {"$sum": 1}}}, {"$match": {"n": {"$gt": 1}}}, {"$limit": 3}]))
    rep.add("no-duplicates:documents.(group_id,version)", not dup_pair, "duplicate document versions")
    # ---- valid values
    valid_states = {s.value for s in AppState}
    bad = db["applications"].count_documents({"state": {"$nin": list(valid_states)}})
    rep.add("applications.state is a known state", bad == 0, f"{bad} application(s) with an unknown state")
    bad = db["application_queue"].count_documents({"status": {"$nin": list(ALL_STATUSES)}})
    rep.add("queue.status is a known status", bad == 0, f"{bad} task(s) with an unknown status")
    bad = db["interviews"].count_documents({"status": {"$nin": ["scheduled", "completed", "cancelled", "no_show"]}})
    rep.add("interviews.status is valid", bad == 0, f"{bad} interview(s) with an invalid status")
    # ---- application consistency (the evidence rules of Phase 6)
    submitted_no_evidence = db["applications"].count_documents({"state": {"$in": ["SUBMITTED", "VERIFIED"]}, "$or": [{"submitted_at": None}, {"submission.outcome": {"$nin": ["confirmed", "user_reported"]}}]})
    rep.add("SUBMITTED/VERIFIED applications have evidence", submitted_no_evidence == 0, f"{submitted_no_evidence} without a confirmed/user-reported outcome or submitted_at")
    verified_no_ref = db["applications"].count_documents({"state": "VERIFIED", "verified_at": None})
    rep.add("VERIFIED applications have verified_at", verified_no_ref == 0, f"{verified_no_ref} without verified_at")
    approved_no_approval = db["applications"].count_documents({"state": "APPROVED", "approval": None})
    rep.add("APPROVED applications have a recorded approval", approved_no_approval == 0, f"{approved_no_approval} APPROVED without approval")
    stuck = db["applications"].count_documents({"state": "SUBMITTING"})
    rep.add("no application is stuck in SUBMITTING", stuck == 0, f"{stuck} in SUBMITTING (outcome unknown: verify manually)", warn=True)
    # ---- event log consistency: contiguous sequence; last event agrees with the state
    seq_bad, state_bad = 0, 0
    for app_doc in db["applications"].find({}, {"_id": 0, "application_id": 1, "state": 1}):
        evs = list(db["application_events"].find({"application_id": app_doc["application_id"]}, {"_id": 0, "seq": 1, "to_state": 1}).sort("seq", 1))
        if not evs:
            continue
        if [e["seq"] for e in evs] != list(range(1, len(evs) + 1)):
            seq_bad += 1
        last_state = next((e["to_state"] for e in reversed(evs) if e.get("to_state")), None)
        if last_state and last_state != app_doc["state"]:
            state_bad += 1
    rep.add("application events are contiguous", seq_bad == 0, f"{seq_bad} application(s) with gaps in the event log")
    rep.add("last event matches the application state", state_bad == 0, f"{state_bad} application(s) whose state differs from their last event", warn=True)
    # ---- references between collections (orphans)
    resumes, jobs, apps = _ids(db, "resumes", "resume_id"), _ids(db, "jobs", "job_id"), _ids(db, "applications", "application_id")
    for col, key, valid, label in (("candidate_profiles", "resume_id", resumes, "resume"), ("documents", "resume_id", resumes, "resume"), ("documents", "job_id", jobs, "job"),
                                   ("job_matches", "job_id", jobs, "job"), ("job_matches", "resume_id", resumes, "resume"), ("applications", "job_id", jobs, "job"),
                                   ("applications", "resume_id", resumes, "resume"), ("application_queue", "job_id", jobs, "job"), ("interviews", "application_id", apps, "application")):
        orphans = len(_ids(db, col, key) - valid)
        rep.add(f"references:{col}.{key} -> {label}", orphans == 0, f"{orphans} value(s) point to a {label} that does not exist", warn=True)
    q_apps = len(_ids(db, "application_queue", "application_id") - apps)
    rep.add("references:application_queue.application_id -> application", q_apps == 0, f"{q_apps} task(s) point to a missing application", warn=True)
    return rep


# ------------------------------------------------------------------------------------------------------------ backup / restore
class BackupError(Exception):
    pass


def _fernet(secret: str | None) -> Fernet:
    return Fernet(derive_fernet_key(secret, "backup"))


def backup(db: Database, dest: Path, *, secret_key: str | None = None, encrypt: bool = False, exclude: set[str] | None = None, now: datetime | None = None) -> dict:
    """Write a backup into the NEW directory ``dest`` (refuses to overwrite). Returns the manifest."""
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        raise BackupError("The backup folder already exists and is not empty (backups are never overwritten).")
    dest.mkdir(parents=True, exist_ok=True)
    f = _fernet(secret_key) if encrypt else None
    skip = TRANSIENT_COLLECTIONS | (exclude or set())
    manifest = {"format": BACKUP_FORMAT, "created_at": (now or datetime.now(timezone.utc)).isoformat(), "database": db.name, "encrypted": encrypt, "collections": {}}
    try:
        for name in sorted(db.list_collection_names()):
            if name in skip or name.startswith("system."):
                continue
            lines = (json_util.dumps(d, sort_keys=True) for d in db[name].find({}))
            body = ("\n".join(lines) + "\n").encode()
            count = body.count(b"\n") if body.strip() else 0
            blob = f.encrypt(body) if f else body
            fname = f"{name}.jsonl" + (".enc" if f else "")
            (dest / fname).write_bytes(blob)
            manifest["collections"][name] = {"file": fname, "documents": count, "sha256": hashlib.sha256(blob).hexdigest(),
                                             "indexes": [{"name": i["name"], "unique": bool(i.get("unique"))} for i in db[name].list_indexes()]}
        (dest / MANIFEST).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)         # never leave a half-written backup that looks complete
        raise
    logger.info("Backup written: collections=%d", len(manifest["collections"]))
    return manifest


def verify_backup(src: Path, *, secret_key: str | None = None) -> dict:
    """Check every file against the manifest (and that it can be decrypted/parsed). Raises BackupError."""
    src = Path(src)
    try:
        manifest = json.loads((src / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise BackupError("manifest.json is missing or unreadable.") from None
    if manifest.get("format") != BACKUP_FORMAT:
        raise BackupError(f"Unsupported backup format {manifest.get('format')}.")
    for name, info in manifest["collections"].items():
        path = src / info["file"]
        try:
            blob = path.read_bytes()
        except OSError:
            raise BackupError(f"{info['file']} is missing.") from None
        if hashlib.sha256(blob).hexdigest() != info["sha256"]:
            raise BackupError(f"{info['file']} does not match its checksum (corrupt or modified).")
        if manifest.get("encrypted"):
            try:
                blob = _fernet(secret_key).decrypt(blob)
            except (InvalidToken, ValueError, Exception) as exc:  # noqa: BLE001
                raise BackupError("The backup could not be decrypted (wrong SECRET_KEY?).") from None
        if blob.count(b"\n") != info["documents"] and info["documents"]:
            raise BackupError(f"{info['file']} has an unexpected number of documents.")
    return manifest


def restore(db: Database, src: Path, *, secret_key: str | None = None, allow_non_empty: bool = False) -> dict:
    """Restore into ``db``. Everything is verified first; a non-empty target is refused unless ``allow_non_empty`` (existing documents with the same _id are replaced)."""
    manifest = verify_backup(src, secret_key=secret_key)
    existing = [n for n in db.list_collection_names() if not n.startswith("system.") and db[n].estimated_document_count() > 0]
    if existing and not allow_non_empty:
        raise BackupError("The target database is not empty. Restore into a new database, or pass the explicit override.")
    f = _fernet(secret_key) if manifest.get("encrypted") else None
    restored = {}
    for name, info in manifest["collections"].items():
        blob = (Path(src) / info["file"]).read_bytes()
        body = f.decrypt(blob) if f else blob
        docs = [json_util.loads(line) for line in body.decode().splitlines() if line.strip()]
        if docs:
            col = db[name]
            col.delete_many({"_id": {"$in": [d["_id"] for d in docs]}}) if allow_non_empty else None
            for i in range(0, len(docs), 500):
                col.insert_many(docs[i:i + 500])
        restored[name] = len(docs)
    ensure_indexes(db)                                   # (re)creates every index the application defines, including unique ones
    for name, n in restored.items():
        if db[name].count_documents({}) < n:
            raise BackupError(f"Verification failed for {name}: fewer documents than restored.")
    return {"restored": restored, "database": db.name, "from": manifest["database"]}
