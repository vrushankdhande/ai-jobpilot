"""Resume workflow: validate -> store -> parse -> persist -> correct -> version."""
import logging
from pathlib import Path

from ai.schemas.candidate_profile import (
    CandidateProfile,
    ProfileContent,
    Provenance,
    new_id,
    utcnow,
)
from ai.validators.factuality_validator import validate_profile
from config.settings import Settings
from database.repositories.resumes_repository import ResumesRepository
from database.repositories.users_repository import UsersRepository
from engines.resume.ai_extractor import AIExtractor
from engines.resume.document_validator import validate_upload
from engines.resume.profile_extractor import compute_missing_fields
from engines.resume.resume_parser import ResumeExtractionError, parse_resume_file

logger = logging.getLogger(__name__)

_CONTENT_SECTIONS = [
    "contact", "summary", "technical_skills", "work_experience",
    "projects", "education", "certifications", "achievements",
]


class ResumeNotFound(Exception):
    pass


class ProfileNotFound(Exception):
    pass


class ResumeConflict(Exception):
    pass


class ResumeService:
    def __init__(
        self,
        repo: ResumesRepository,
        users: UsersRepository,
        settings: Settings,
        storage_dir: Path | None = None,
        ai_extractor: AIExtractor | None = None,
    ):
        self.repo = repo
        self.users = users
        self.settings = settings
        self.storage_dir = Path(storage_dir) if storage_dir else settings.resume_storage_path
        self.ai_extractor = ai_extractor

    # ---- helpers ---------------------------------------------------------
    def _require(self, resume_id: str) -> dict:
        doc = self.repo.get_resume(resume_id)
        if doc is None:
            raise ResumeNotFound(resume_id)
        return doc

    def _path(self, doc: dict) -> Path:
        base = self.storage_dir.resolve()
        path = (base / doc["stored_filename"]).resolve()
        if not path.is_relative_to(base):  # defence in depth against path traversal
            raise ResumeNotFound(doc["resume_id"])
        return path

    def active_resume_id(self) -> str | None:
        return self.users.get_active_resume_id()

    # ---- upload ----------------------------------------------------------
    def upload(self, filename: str | None, data: bytes) -> tuple[dict, bool]:
        """Store a new resume and parse it. Returns (resume_doc, is_duplicate)."""
        validated = validate_upload(filename, data, self.settings.max_upload_mb * 1024 * 1024)
        existing = self.repo.find_by_sha256(validated.sha256)
        if existing:
            return existing, True

        resume_id = new_id()
        stored = f"{resume_id}.{validated.file_type}"
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        with open(self.storage_dir / stored, "xb") as fh:  # "x": never overwrite
            fh.write(data)

        doc = {
            "resume_id": resume_id,
            "original_filename": validated.display_name,
            "stored_filename": stored,  # internal only, never returned by the API
            "file_type": validated.file_type,
            "size_bytes": validated.size_bytes,
            "sha256": validated.sha256,
            "uploaded_at": utcnow(),
            "parsing_status": "pending",
            "parsing_error": None,
            "review_status": None,
            "profile_version": None,
        }
        self.repo.create_resume(doc)
        if self.users.get_active_resume_id() is None:
            self.users.set_active_resume(resume_id)
        logger.info("Resume uploaded: id=%s type=%s size=%d", resume_id, validated.file_type, validated.size_bytes)
        return self.parse(resume_id, force=True), False

    # ---- parse -----------------------------------------------------------
    def parse(self, resume_id: str, force: bool = False) -> dict:
        doc = self._require(resume_id)
        existing = self.repo.get_profile(resume_id)
        if existing and not force:
            raise ResumeConflict("A profile already exists. Re-parse with force=true (the old one is kept as a version).")

        self.repo.update_resume(resume_id, {"parsing_status": "parsing", "parsing_error": None})
        try:
            outcome = parse_resume_file(
                self._path(doc), doc["file_type"], resume_id,
                min_chars=self.settings.min_resume_text_chars, ai_extractor=self.ai_extractor,
            )
        except ResumeExtractionError as exc:
            status = "needs_ocr" if exc.code == "needs_ocr" else "failed"
            self.repo.update_resume(resume_id, {
                "parsing_status": status, "parsing_error": exc.message, "parsing_error_code": exc.code,
            })
            logger.warning("Parsing %s for resume %s", exc.code, resume_id)
            return self._require(resume_id)
        except Exception:
            logger.exception("Unexpected parsing failure for resume %s", resume_id)
            self.repo.update_resume(resume_id, {
                "parsing_status": "failed", "parsing_error": "Unexpected error while parsing the resume.",
                "parsing_error_code": "internal_error",
            })
            return self._require(resume_id)

        profile = outcome.profile
        if existing:
            self.repo.archive_profile_version(existing)
            profile.profile_version = existing.profile_version + 1
        self.repo.save_profile(profile)
        self.repo.update_resume(resume_id, {
            "parsing_status": "completed", "parsing_error": None, "parsing_error_code": None,
            "review_status": profile.review_status, "profile_version": profile.profile_version,
            **({"extracted_text": outcome.text} if self.settings.store_resume_text else {}),
        })
        return self._require(resume_id)

    # ---- read ------------------------------------------------------------
    def get_resume(self, resume_id: str) -> dict:
        return self._require(resume_id)

    def list_resumes(self) -> list[dict]:
        return self.repo.list_resumes()

    def get_profile(self, resume_id: str) -> CandidateProfile:
        self._require(resume_id)
        profile = self.repo.get_profile(resume_id)
        if profile is None:
            raise ProfileNotFound(resume_id)
        return profile

    def list_versions(self, resume_id: str) -> list[CandidateProfile]:
        self._require(resume_id)
        return self.repo.list_profile_versions(resume_id)

    # ---- corrections -----------------------------------------------------
    def update_profile(self, resume_id: str, content: ProfileContent, review_status: str | None = None) -> CandidateProfile:
        current = self.get_profile(resume_id)
        old, new = current.content().model_dump(mode="json"), content.model_dump(mode="json")

        merged = current.model_dump()
        merged.update(content.model_dump())
        updated = CandidateProfile.model_validate(merged)
        updated.profile_version = current.profile_version + 1
        updated.updated_at = utcnow()
        updated.review_status = review_status or "pending_review"
        for section in _CONTENT_SECTIONS:
            if old[section] != new[section]:
                updated.provenance[section] = Provenance(method="manual", kind="edited", source_section=section)
        updated.missing_fields = compute_missing_fields(updated)

        source = self.repo.get_extracted_text(resume_id)
        if source:
            updated.validation_warnings = validate_profile(updated, source)

        self.repo.archive_profile_version(current)
        self.repo.save_profile(updated)
        self.repo.update_resume(resume_id, {
            "review_status": updated.review_status, "profile_version": updated.profile_version,
        })
        return updated

    # ---- versions / lifecycle ---------------------------------------------
    def activate(self, resume_id: str) -> dict:
        doc = self._require(resume_id)
        self.users.set_active_resume(resume_id)
        return doc

    def _remove_generated_files(self, resume_id: str) -> None:
        """Delete exported DOCX/PDF files of documents generated from this resume (their records go with the resume)."""
        base = self.settings.generated_storage_path.resolve()
        for d in self.repo._documents.find({"resume_id": resume_id}, {"_id": 0, "exports": 1}):
            for e in d.get("exports", []):
                try:
                    p = (base / e["file_id"]).resolve()
                    if p.is_relative_to(base):
                        p.unlink(missing_ok=True)
                except (OSError, KeyError):
                    logger.warning("Could not remove an exported file for resume %s", resume_id)

    def _remove_application_screenshots(self, resume_id: str) -> None:
        """Screenshots of filled application forms contain personal data: delete them with the resume."""
        from browser.screenshot_manager import ScreenshotManager
        ids = [s for d in self.repo._applications.find({"resume_id": resume_id}, {"_id": 0, "screenshots": 1}) for s in d.get("screenshots", [])]
        ScreenshotManager(self.settings.screenshots_path).delete(ids)
        if ids:
            self.repo._applications.update_many({"resume_id": resume_id}, {"$set": {"screenshots": []}})

    def delete(self, resume_id: str) -> None:
        doc = self._require(resume_id)
        try:
            self._path(doc).unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove stored file for resume %s", resume_id)
        self._remove_generated_files(resume_id)
        self._remove_application_screenshots(resume_id)
        self.repo.delete_resume(resume_id)
        self.users.clear_active_if(resume_id)
        logger.info("Resume deleted: id=%s", resume_id)
