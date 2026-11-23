"""Chooses the documents for an application and uploads them. APPROVED, validated documents only - never anything else.

* ``select_documents`` finds, per document type, the version the candidate APPROVED (not a later draft), re-validates it against the
  current profile, makes sure an export from the approved content exists, and checks the stored file's SHA-256.
* Files are copied to a private temp folder under a clean download name (the employer sees ``Jane_Sample_Resume_Acme.pdf``, not an internal id);
  the generated documents themselves are only read, never modified or deleted.
* ``upload_file`` sets the file on the form's <input type=file> and then asks the browser what is attached (name + size) before reporting success.
"""
import hashlib
import logging
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from database.models.application import UploadRecord
from engines.generation.render import render_lines

logger = logging.getLogger(__name__)
FORMAT_PREFERENCE = {"resume": ("pdf", "docx"), "cover_letter": ("pdf", "docx")}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


@dataclass
class SelectedFile:
    role: str
    document_id: str
    version: int
    path: Path
    name: str
    sha256: str
    size: int
    format: str
    alt: dict = field(default_factory=dict)       # other formats: {"docx": SelectedFile}


@dataclass
class Selection:
    resume: SelectedFile | None = None
    cover_letter: SelectedFile | None = None
    screening: dict | None = None                 # the approved screening document (content dict)
    cover_letter_text: str | None = None
    problems: list[str] = field(default_factory=list)
    document_ids: dict = field(default_factory=dict)
    document_versions: dict = field(default_factory=dict)
    _tmp: Path | None = None

    def cleanup(self) -> None:
        if self._tmp:
            shutil.rmtree(self._tmp, ignore_errors=True)
            self._tmp = None

    @property
    def answers(self) -> dict[str, dict]:
        """Approved screening answers that actually contain text (needs_input placeholders are excluded)."""
        out: dict[str, dict] = {}
        for a in (self.screening or {}).get("answers", []):
            if a.get("status") in ("answered", "draft") and (a.get("answer") or "").strip():
                out[a["question_id"]] = {"question": a["question"], "answer": a["answer"], "status": a["status"]}
        return out


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:120] or "document"


def _approved_version(documents, resume_id: str, job_id: str, doc_type: str) -> dict | None:
    gid = documents.group_id(resume_id, job_id, doc_type)
    approved = [d for d in documents.docs.group(gid, include_content=True) if d.get("review_status") == "approved"]
    return max(approved, key=lambda d: d["version"]) if approved else None


def select_documents(documents, resume_id: str, job_id: str, tmp_root: Path | None = None) -> Selection:
    """``documents`` is a DocumentService. Returns what may be uploaded; ``problems`` explains what is missing/unusable."""
    sel = Selection()
    tmp = Path(tempfile.mkdtemp(prefix="jp_upload_", dir=str(tmp_root) if tmp_root else None))
    sel._tmp = tmp
    for role, doc_type in (("resume", "resume"), ("cover_letter", "cover_letter"), ("screening", "screening_answers")):
        doc = _approved_version(documents, resume_id, job_id, doc_type)
        label = doc_type.replace("_", " ")
        if doc is None:
            if role != "screening":
                sel.problems.append(f"No approved {label} for this job. Generate and approve one in Document Generation.")
            continue
        try:
            doc = documents.validate(doc["document_id"])          # re-check against the CURRENT profile; never trust an old result
        except Exception as exc:  # noqa: BLE001
            sel.problems.append(f"The approved {label} could not be re-validated ({type(exc).__name__}).")
            continue
        if doc["validation"]["status"] == "failed" or doc["review_status"] != "approved":
            sel.problems.append(f"The approved {label} no longer passes the fact check. Fix and approve it again.")
            continue
        sel.document_ids[role], sel.document_versions[role] = doc["document_id"], doc["version"]
        if role == "screening":
            sel.screening = doc["content"]
            continue
        if role == "cover_letter":
            sel.cover_letter_text = "\n".join(render_lines("cover_letter", doc["content"]))
        # make sure there is an export of the approved content, then fetch it through the integrity-checked download
        formats = FORMAT_PREFERENCE[role]
        if not any(e["format"] in formats and e.get("from_approved") for e in doc.get("exports", [])):
            try:
                doc = documents.export(doc["document_id"], list(formats))
            except Exception as exc:  # noqa: BLE001
                sel.problems.append(f"The approved {label} could not be exported ({type(exc).__name__}).")
                continue
        files: dict[str, SelectedFile] = {}
        for fmt in formats:
            try:
                data, name, _ctype = documents.download(doc["document_id"], fmt)
            except Exception:  # noqa: BLE001 - missing / modified file
                continue
            if not data or len(data) > MAX_UPLOAD_BYTES:
                continue
            path = tmp / role / _safe_name(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            files[fmt] = SelectedFile(role, doc["document_id"], doc["version"], path, path.name, hashlib.sha256(data).hexdigest(), len(data), fmt)
        if not files:
            sel.problems.append(f"No intact exported file was found for the approved {label}.")
            continue
        first = files[next(f for f in formats if f in files)]
        first.alt = {k: v for k, v in files.items() if v is not first}
        setattr(sel, role, first)
    return sel


def choose_format(sf: SelectedFile, accept: str) -> SelectedFile | None:
    """The file whose format the input accepts (accept="" = anything)."""
    if not accept.strip():
        return sf
    allowed = {a.strip().lower().lstrip(".") for a in accept.split(",")}
    allowed_mime = {"application/pdf": "pdf", "application/msword": "doc",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx"}
    allowed |= {allowed_mime[a] for a in allowed if a in allowed_mime}
    for cand in [sf, *sf.alt.values()]:
        if cand.format in allowed:
            return cand
    return None


def upload_file(locator, sf: SelectedFile) -> UploadRecord:
    """Attach ``sf`` to a file input and verify the browser really holds it. ``verified`` is False if it does not."""
    rec = UploadRecord(role=sf.role, document_id=sf.document_id, document_version=sf.version, file_name=sf.name, sha256=sf.sha256,
                       size_bytes=sf.size, format=sf.format)
    try:
        locator.set_input_files(str(sf.path))
        attached = locator.evaluate("el => Array.from(el.files || []).map(f => [f.name, f.size])")
    except Exception as exc:  # noqa: BLE001
        rec.detail = f"upload failed ({type(exc).__name__})"
        return rec
    if len(attached) == 1 and attached[0][0] == sf.name and attached[0][1] == sf.size:
        rec.verified = True
    else:
        rec.detail = "the browser does not show the expected file as attached"
    return rec
