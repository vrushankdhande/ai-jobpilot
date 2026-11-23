"""Upload validation: never trust the filename or MIME type alone."""
import hashlib
import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass

ALLOWED_EXTENSIONS = {".pdf": "pdf", ".docx": "docx"}
_MAX_UNCOMPRESSED_DOCX = 25 * 1024 * 1024   # zip-bomb guard: real resumes are far below this
_MAX_DOCX_RATIO = 200                          # compressed -> uncompressed expansion above this is not a document


class ResumeValidationError(Exception):
    """Raised for unacceptable uploads. ``status_code`` maps to HTTP."""

    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class ValidatedUpload:
    file_type: str  # "pdf" | "docx"
    display_name: str  # sanitized, for display only - never used as a path
    size_bytes: int
    sha256: str


def sanitize_display_name(filename: str | None) -> str:
    """Strip any directory part and control characters from an uploaded name."""
    name = (filename or "").replace("\\", "/").split("/")[-1]
    name = unicodedata.normalize("NFKC", name)
    name = re.sub(r"[\x00-\x1f\x7f<>:\"|?*]", "", name).strip(" .")
    dot = name.rfind(".")
    stem, ext = (name[:dot], name[dot:]) if dot > 0 else (name, "")
    ext = ext[:10]
    return (stem[: 120 - len(ext)] + ext) or "resume"


def _extension(display_name: str) -> str:
    dot = display_name.rfind(".")
    return display_name[dot:].lower() if dot != -1 else ""


def validate_upload(filename: str | None, data: bytes, max_bytes: int) -> ValidatedUpload:
    display_name = sanitize_display_name(filename)
    ext = _extension(display_name)
    if ext not in ALLOWED_EXTENSIONS:
        raise ResumeValidationError(
            "unsupported_type", "Only .pdf and .docx files are supported.", 415
        )
    if len(data) == 0:
        raise ResumeValidationError("empty_file", "The uploaded file is empty.", 400)
    if len(data) > max_bytes:
        raise ResumeValidationError(
            "file_too_large", f"File exceeds the {max_bytes // (1024 * 1024)} MB limit.", 413
        )

    file_type = ALLOWED_EXTENSIONS[ext]
    if file_type == "pdf":
        if b"%PDF-" not in data[:1024]:
            raise ResumeValidationError(
                "content_mismatch", "File content is not a valid PDF.", 415
            )
    else:
        _check_docx(data)

    return ValidatedUpload(
        file_type=file_type,
        display_name=display_name,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def _check_docx(data: bytes) -> None:
    mismatch = ResumeValidationError(
        "content_mismatch", "File content is not a valid DOCX document.", 415
    )
    if not data.startswith(b"PK"):
        raise mismatch
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = set(zf.namelist())
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise mismatch
            total = sum(i.file_size for i in zf.infolist())
            if total > _MAX_UNCOMPRESSED_DOCX or (total > 1024 * 1024 and total > _MAX_DOCX_RATIO * max(1, len(data))):
                raise ResumeValidationError(
                    "file_too_large", "Document expands to an unreasonable size.", 413
                )
    except zipfile.BadZipFile:
        raise mismatch from None
