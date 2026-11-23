"""Data access for resumes, candidate profiles and profile versions."""
from pymongo.database import Database

from ai.schemas.candidate_profile import CandidateProfile

RESUMES = "resumes"
PROFILES = "candidate_profiles"
VERSIONS = "profile_versions"
_NO_ID = {"_id": 0}


class ResumesRepository:
    def __init__(self, db: Database):
        self._resumes = db[RESUMES]
        self._profiles = db[PROFILES]
        self._versions = db[VERSIONS]
        self._matches = db["job_matches"]  # derived data: removed together with the resume
        self._documents = db["documents"]
        self._applications = db["applications"]
        self._queue = db["application_queue"]

    # ---- resumes ---------------------------------------------------------
    def create_resume(self, doc: dict) -> dict:
        self._resumes.insert_one(dict(doc))
        return doc

    def get_resume(self, resume_id: str, include_text: bool = False) -> dict | None:
        projection = dict(_NO_ID)
        if not include_text:
            projection["extracted_text"] = 0
        return self._resumes.find_one({"resume_id": resume_id}, projection)

    def get_extracted_text(self, resume_id: str) -> str | None:
        doc = self._resumes.find_one({"resume_id": resume_id}, {"_id": 0, "extracted_text": 1})
        return (doc or {}).get("extracted_text")

    def find_by_sha256(self, sha256: str) -> dict | None:
        return self._resumes.find_one({"sha256": sha256}, {**_NO_ID, "extracted_text": 0})

    def list_resumes(self) -> list[dict]:
        cur = self._resumes.find({}, {**_NO_ID, "extracted_text": 0}).sort("uploaded_at", -1)
        return list(cur)

    def update_resume(self, resume_id: str, fields: dict) -> bool:
        return self._resumes.update_one({"resume_id": resume_id}, {"$set": fields}).matched_count == 1

    def delete_resume(self, resume_id: str) -> bool:
        """Delete the resume and every document derived from it."""
        self._profiles.delete_many({"resume_id": resume_id})
        self._versions.delete_many({"resume_id": resume_id})
        self._matches.delete_many({"resume_id": resume_id})
        self._documents.delete_many({"resume_id": resume_id})  # generated documents are derived from this profile
        # Phase 6: the personal values copied into applications are erased; the history (states, events, references) is kept as an audit trail
        self._applications.update_many({"resume_id": resume_id}, {"$set": {"mapping": [], "uploads": [], "notes": [], "inputs": {}}})
        # Phase 7: open queue tasks for this resume are cancelled (history stays); finished ones are left as they are
        self._queue.update_many({"resume_id": resume_id, "status": {"$in": ["queued", "paused", "processing", "awaiting_approval", "failed", "manual_handoff"]}},
                                {"$set": {"status": "cancelled", "lease_expires_at": None, "worker_id": None}})
        return self._resumes.delete_one({"resume_id": resume_id}).deleted_count == 1

    # ---- profiles --------------------------------------------------------
    def save_profile(self, profile: CandidateProfile) -> None:
        self._profiles.replace_one(
            {"resume_id": profile.resume_id}, profile.model_dump(mode="python"), upsert=True
        )

    def get_profile(self, resume_id: str) -> CandidateProfile | None:
        doc = self._profiles.find_one({"resume_id": resume_id}, _NO_ID)
        return CandidateProfile.model_validate(doc) if doc else None

    def archive_profile_version(self, profile: CandidateProfile) -> None:
        """Snapshot a profile before it is replaced (corrections / re-parse)."""
        self._versions.replace_one(
            {"resume_id": profile.resume_id, "profile_version": profile.profile_version},
            profile.model_dump(mode="python"),
            upsert=True,
        )

    def list_profile_versions(self, resume_id: str) -> list[CandidateProfile]:
        cur = self._versions.find({"resume_id": resume_id}, _NO_ID).sort("profile_version", 1)
        return [CandidateProfile.model_validate(d) for d in cur]
