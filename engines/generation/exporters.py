"""Export facade: DOCX and PDF with unique, safe file names. The master resume is never touched."""
import hashlib
import re
from dataclasses import dataclass, field

from engines.generation.export_docx import export_docx
from engines.generation.export_pdf import export_pdf

CONTENT_TYPES = {"docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "pdf": "application/pdf"}
_LABEL = {"resume": "Resume", "cover_letter": "CoverLetter", "screening_answers": "ScreeningAnswers"}


@dataclass
class ExportResult:
    data: bytes
    fmt: str
    sha256: str
    warnings: list[str] = field(default_factory=list)


def safe_part(text: str | None, default: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", (text or "").encode("ascii", "ignore").decode()).strip("_")
    return s[:40] or default


def download_name(doc_type: str, fmt: str, name: str | None, company: str | None, approved: bool) -> str:
    parts = ["DRAFT"] if not approved else []
    parts += [safe_part(name, "Candidate"), _LABEL[doc_type]] + ([safe_part(company, "")] if company else [])
    return "_".join(p for p in parts if p) + f".{fmt}"


def export(doc_type: str, content: dict, fmt: str, header: dict, approved: bool, page: str = "A4", title: str = "") -> ExportResult:
    if fmt == "docx":
        data, warnings = export_docx(doc_type, content, header, title, approved, page), []
    elif fmt == "pdf":
        data, warnings = export_pdf(doc_type, content, header, title, approved, page)
    else:
        raise ValueError("format must be 'docx' or 'pdf'")
    return ExportResult(data=data, fmt=fmt, sha256=hashlib.sha256(data).hexdigest(), warnings=warnings)
