"""Document workflow: generate -> validate -> edit (new version) -> approve -> export.

Rules enforced here:
* the verified candidate profile is the only source (unreviewed profiles need explicit confirmation, as in Phase 3);
* every generation, edit or regeneration creates a NEW version; nothing is overwritten;
* a document is only "approved" by an explicit call, only if validation has no errors for the CURRENT content, and warnings
  must be acknowledged;
* exports of unapproved documents carry a DRAFT marker; exports of documents with validation errors are refused;
* the master resume and the stored resume files are never written to.
"""
import difflib
import hashlib
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ai.claude_client import ClaudeClient
from ai.schemas.candidate_profile import CandidateProfile, new_id
from ai.schemas.document_schema import (
    DOC_TYPES, DocumentValidation, ExportRef, GeneratedDocument, content_hash, parse_content,
)
from ai.validators.document_validator import JobFacts, _nums, validate_document
from backend.services.matching_service import JobNotFound, MatchingService, MatchNotFound
from config.scoring_config import Preferences, ScoringConfig
from config.settings import Settings
from database.repositories.documents_repository import DocumentsRepository
from engines.generation import document_engine
from engines.generation.exporters import CONTENT_TYPES, download_name, export
from engines.generation.fact_pack import build_fact_pack
from engines.generation.render import render_lines
from engines.generation.resume_customizer import build_job_context
from engines.generation.screening_answers import effective_inputs
from engines.matching.skill_extractor import SkillCatalog
from engines.resume.resume_builder import ResumeOptions

logger = logging.getLogger(__name__)
_FILE_ID = re.compile(r"^[A-Za-z0-9_\-]{8,120}\.(docx|pdf)$")


class DocumentNotFound(Exception):
    pass


class NotApprovable(Exception):
    def __init__(self, message: str, issues: list | None = None):
        super().__init__(message)
        self.issues = issues or []


class ExportRefused(Exception):
    pass


class _Ctx:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class DocumentService:
    def __init__(self, docs: DocumentsRepository, matching: MatchingService, settings: Settings, catalog: SkillCatalog,
                 prefs: Preferences, cfg: ScoringConfig, ai: ClaudeClient | None = None):
        self.docs, self.matching, self.settings, self.catalog, self.prefs, self.cfg, self.ai = docs, matching, settings, catalog, prefs, cfg, ai
        self.storage = settings.generated_storage_path

    # ------------------------------------------------------------------------------------ context
    def _job_facts(self, job_doc: dict, analysis) -> JobFacts:
        text = (job_doc.get("job_description") or job_doc.get("jd_text") or "")[:20000]
        return JobFacts(company=job_doc.get("company_name"), title=job_doc.get("job_title"),
                        skills={s.name for s in analysis.skills}, numbers=_nums(text))

    def _validator(self, profile: CandidateProfile, jf: JobFacts, inputs: dict | None = None):
        def validate(doc_type: str, content: dict) -> DocumentValidation:
            return validate_document(doc_type, content, profile, self.catalog, job=jf, inputs=inputs, bands=self.cfg.seniority.bands)
        return validate

    def _prepare(self, job_id: str, resume_id: str | None, match_id: str | None, allow_unreviewed: bool) -> _Ctx:
        profile = self.matching.profile_for_matching(resume_id, allow_unreviewed)
        job_doc, analysis = self.matching.analysis_for_job(job_id, use_ai=False)  # deterministic parse: no extra external call
        if match_id:
            report = self.matching.matches.get(match_id)
            if report is None:
                raise MatchNotFound(match_id)
            if report.job_id != job_id or report.resume_id != profile.resume_id:
                raise ValueError("That match report belongs to a different job or resume.")
        else:
            report, _ = self.matching.score(job_id=job_id, resume_id=profile.resume_id, allow_unreviewed=allow_unreviewed, use_ai=False)
        ctx = build_job_context(analysis, self.catalog, matched_skills=report.matched_skills,
                                company_name=job_doc.get("company_name"), job_title=job_doc.get("job_title"))
        return _Ctx(profile=profile, job_doc=job_doc, analysis=analysis, report=report, ctx=ctx,
                    pack=build_fact_pack(profile, ctx, self.catalog), jf=self._job_facts(job_doc, analysis))

    # ------------------------------------------------------------------------------------ storage
    @staticmethod
    def group_id(resume_id: str, job_id: str, doc_type: str) -> str:
        return hashlib.sha256(f"{resume_id}|{job_id}|{doc_type}".encode()).hexdigest()[:32]

    def _store(self, doc_type: str, c: _Ctx, gen: document_engine.Generated, validate) -> dict:
        gid = self.group_id(c.profile.resume_id, c.job_doc["job_id"], doc_type)
        prev = self.docs.latest_version(gid)
        validation = validate(doc_type, gen.content)
        doc = GeneratedDocument(
            group_id=gid, document_type=doc_type, version=(prev["version"] + 1) if prev else 1, parent_version_id=prev["document_id"] if prev else None,
            profile_id=c.profile.profile_id, profile_version=c.profile.profile_version, resume_id=c.profile.resume_id,
            job_id=c.job_doc["job_id"], match_id=c.report.match_id, job_title=c.job_doc.get("job_title"), company_name=c.job_doc.get("company_name"),
            generator=gen.generator, ai_model=gen.ai_model, notes=gen.notes, content=gen.content, validation=validation)
        out = self.docs.insert(doc)
        logger.info("Document generated: id=%s type=%s v%d validation=%s generator=%s", doc.document_id, doc_type, doc.version, validation.status, gen.generator)
        return out

    def _ai(self, use_ai: bool | None) -> ClaudeClient | None:
        return None if use_ai is False else self.ai

    # ------------------------------------------------------------------------------------ generate
    def generate_resume(self, job_id, *, resume_id=None, match_id=None, allow_unreviewed=False, use_ai=None, options: dict | None = None) -> dict:
        c = self._prepare(job_id, resume_id, match_id, allow_unreviewed)
        validate = self._validator(c.profile, c.jf)
        gen = document_engine.generate_resume(c.profile, c.ctx, c.pack, self.catalog, validate, ResumeOptions(**(options or {})), self._ai(use_ai))
        return self._store("resume", c, gen, validate)

    def generate_cover_letter(self, job_id, *, resume_id=None, match_id=None, allow_unreviewed=False, use_ai=None) -> dict:
        c = self._prepare(job_id, resume_id, match_id, allow_unreviewed)
        validate = self._validator(c.profile, c.jf)
        gen = document_engine.generate_cover_letter(c.pack, validate, self._ai(use_ai))
        return self._store("cover_letter", c, gen, validate)

    def generate_screening(self, job_id, *, resume_id=None, match_id=None, allow_unreviewed=False, use_ai=None, questions: list[dict] | None = None,
                           length: str = "medium", candidate_inputs: dict | None = None) -> dict:
        c = self._prepare(job_id, resume_id, match_id, allow_unreviewed)
        inputs = effective_inputs(self.prefs, candidate_inputs)
        validate = self._validator(c.profile, c.jf, inputs)
        gen = document_engine.generate_screening(c.pack, self.prefs, validate, questions, length, candidate_inputs, self._ai(use_ai))
        return self._store("screening_answers", c, gen, validate)

    # ------------------------------------------------------------------------------------ read
    def get(self, document_id: str) -> dict:
        d = self.docs.get(document_id)
        if d is None:
            raise DocumentNotFound(document_id)
        return d

    def _current_profile(self, doc: dict) -> CandidateProfile | None:
        return self.matching.resumes.get_profile(doc["resume_id"])

    def profile_stale(self, doc: dict) -> bool:
        p = self._current_profile(doc)
        return p is not None and p.profile_version != doc["profile_version"]

    def versions(self, document_id: str) -> list[dict]:
        return self.docs.group(self.get(document_id)["group_id"])

    def list_documents(self, **filters) -> list[dict]:
        return self.docs.find_all(**filters)

    # ------------------------------------------------------------------------------------ validate / edit / approve
    def _validate_doc(self, doc: dict) -> DocumentValidation:
        profile = self._current_profile(doc)
        if profile is None:
            raise NotApprovable("The candidate profile for this document no longer exists.")
        job_doc, analysis = self.matching.analysis_for_job(doc["job_id"], use_ai=False)
        inputs = doc["content"].get("candidate_inputs") if doc["document_type"] == "screening_answers" else None
        return self._validator(profile, self._job_facts(job_doc, analysis), inputs)(doc["document_type"], doc["content"])

    def validate(self, document_id: str) -> dict:
        doc = self.get(document_id)
        v = self._validate_doc(doc)
        self.docs.update(document_id, {"validation": v.model_dump(mode="python")})
        return self.get(document_id)

    def update_content(self, document_id: str, content: dict) -> dict:
        """The candidate edits a document: saved as a NEW version (the old one is kept), re-validated, back to draft."""
        doc = self.get(document_id)
        if len(str(content)) > self.settings.max_document_chars:
            raise ValueError("The document is too large.")
        parsed = parse_content(doc["document_type"], content).model_dump(mode="json")  # ValidationError -> 422 in the route
        if content_hash(parsed) == content_hash(doc["content"]):
            return doc
        gid = doc["group_id"]
        latest = self.docs.latest_version(gid)
        new = GeneratedDocument(
            group_id=gid, document_type=doc["document_type"], version=latest["version"] + 1, parent_version_id=document_id,
            profile_id=doc["profile_id"], profile_version=doc["profile_version"], resume_id=doc["resume_id"], job_id=doc["job_id"],
            match_id=doc.get("match_id"), job_title=doc.get("job_title"), company_name=doc.get("company_name"), generator="manual_edit",
            ai_model=None, notes=["Edited by the candidate."], content=parsed, edited=True)
        probe = self.docs.insert(new)
        v = self._validate_doc(probe)
        self.docs.update(probe["document_id"], {"validation": v.model_dump(mode="python")})
        return self.get(probe["document_id"])

    def approve(self, document_id: str, acknowledge_warnings: bool = False) -> dict:
        doc = self.get(document_id)
        v = self._validate_doc(doc)  # always against the current profile and the exact current content
        self.docs.update(document_id, {"validation": v.model_dump(mode="python")})
        if v.status == "failed":
            raise NotApprovable("The document contains claims that are not supported by your verified profile (or still needs your input). "
                                "Correct them before approving.", [i.model_dump() for i in v.issues if i.severity == "error"])
        if v.status == "passed_with_warnings" and not acknowledge_warnings:
            raise NotApprovable("The document has warnings. Review them and approve again with acknowledge_warnings=true.",
                                [i.model_dump() for i in v.issues if i.severity == "warning"])
        self.docs.update(document_id, {"review_status": "approved", "approved_at": datetime.now(timezone.utc)})
        self.docs.supersede_others(doc["group_id"], document_id)
        logger.info("Document approved: id=%s type=%s v%s", document_id, doc["document_type"], doc["version"])
        return self.get(document_id)

    # ------------------------------------------------------------------------------------ export / download
    def export(self, document_id: str, formats: list[str]) -> dict:
        doc = self.get(document_id)
        v = self._validate_doc(doc)
        self.docs.update(document_id, {"validation": v.model_dump(mode="python")})
        if v.status == "failed":
            raise ExportRefused("Export is blocked while the document has validation errors. Correct them first.")
        profile = self._current_profile(doc)
        header = profile.contact.model_dump() if profile else {}
        approved = doc["review_status"] == "approved"
        title = {"resume": "Resume", "cover_letter": "Cover letter", "screening_answers": "Screening answers"}[doc["document_type"]]
        title = f"{title} - {doc.get('job_title') or ''} {('at ' + doc['company_name']) if doc.get('company_name') else ''}".strip()
        self.storage.mkdir(parents=True, exist_ok=True)
        for fmt in dict.fromkeys(formats):
            res = export(doc["document_type"], doc["content"], fmt, header, approved, self.settings.pdf_page_size, title)
            file_id = f"{document_id[:12]}_v{doc['version']}_{doc['document_type']}_{uuid.uuid4().hex[:10]}.{fmt}"
            with open(self.storage / file_id, "xb") as fh:  # "x": never overwrite an existing file
                fh.write(res.data)
            ref = ExportRef(format=fmt, file_id=file_id, sha256=res.sha256, size_bytes=len(res.data), from_approved=approved, warnings=res.warnings,
                            download_name=download_name(doc["document_type"], fmt, profile.contact.full_name if profile else None, doc.get("company_name"), approved))
            self.docs.push_export(document_id, ref.model_dump(mode="python"))
        return self.get(document_id)

    def download(self, document_id: str, fmt: str) -> tuple[bytes, str, str]:
        doc = self.get(document_id)
        ref = next((e for e in reversed(doc.get("exports", [])) if e["format"] == fmt), None)
        if ref is None or not _FILE_ID.match(ref["file_id"]):
            raise DocumentNotFound("no export")
        base = self.storage.resolve()
        path = (base / ref["file_id"]).resolve()
        if not path.is_relative_to(base) or not path.is_file():
            raise DocumentNotFound("file missing")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != ref["sha256"]:
            raise DocumentNotFound("file changed on disk")  # never serve a modified file
        return data, ref["download_name"], CONTENT_TYPES[fmt]

    # ------------------------------------------------------------------------------------ compare
    def compare(self, document_id: str, other_id: str) -> dict:
        a, b = self.get(document_id), self.get(other_id)
        if a["group_id"] != b["group_id"]:
            raise ValueError("Only versions of the same document can be compared.")
        old, new = sorted([a, b], key=lambda d: d["version"])
        diff = list(difflib.unified_diff(render_lines(old["document_type"], old["content"]), render_lines(new["document_type"], new["content"]),
                                         fromfile=f"v{old['version']}", tofile=f"v{new['version']}", lineterm="", n=1))
        return {"from_version": old["version"], "to_version": new["version"], "from_id": old["document_id"], "to_id": new["document_id"],
                "changed": bool(diff), "diff": diff}
