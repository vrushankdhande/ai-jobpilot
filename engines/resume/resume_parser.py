"""Resume parsing pipeline.

file -> text extraction (PDF/DOCX) -> cleaning -> section detection and
extraction (profile_extractor) -> optional AI fill -> factuality validation.
"""
import logging
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from ai.schemas.candidate_profile import CandidateProfile
from ai.validators.factuality_validator import validate_profile
from engines.resume.ai_extractor import AIExtractor, merge_ai_content
from engines.resume.profile_extractor import extract_profile

logger = logging.getLogger(__name__)

MAX_PDF_PAGES = 30
OcrHandler = Callable[[Path], str]

# Extension point: assign a callable (path -> text) to enable OCR for scanned PDFs,
# e.g. one built on pytesseract. Nothing is bundled, so scans are reported honestly
# as "needs_ocr" rather than "parsed".
OCR_HANDLER: OcrHandler | None = None


class ResumeExtractionError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ParseOutcome:
    text: str
    profile: CandidateProfile


# ---- text extraction -----------------------------------------------------
def extract_pdf_text(path: Path) -> str:
    try:
        doc = pymupdf.open(path)
    except Exception:
        raise ResumeExtractionError("corrupted", "The PDF could not be opened (corrupted file).") from None
    with doc:
        if doc.needs_pass or doc.is_encrypted:
            raise ResumeExtractionError("encrypted", "The PDF is password protected. Remove the password and re-upload.")
        try:
            pages = [p.get_text("text", sort=True) for i, p in enumerate(doc) if i < MAX_PDF_PAGES]
        except Exception:
            raise ResumeExtractionError("corrupted", "Text could not be read from the PDF.") from None
    return "\n".join(pages)


def extract_docx_text(path: Path) -> str:
    try:
        doc = Document(str(path))
    except Exception:
        raise ResumeExtractionError("corrupted", "The DOCX could not be opened (corrupted file).") from None
    lines: list[str] = []
    seen_headers: set[str] = set()
    for sec in doc.sections:
        for p in sec.header.paragraphs:
            t = p.text.strip()
            if t and t not in seen_headers:
                seen_headers.add(t)
                lines.append(t)
    for child in doc.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            para = Paragraph(child, doc)
            t = para.text.strip()
            if t:
                style = (para.style.name if para.style is not None else "") or ""
                lines.append(("• " + t) if style.startswith("List") else t)
        elif tag == "tbl":
            for row in Table(child, doc).rows:
                seen, cells = set(), []
                for cell in row.cells:
                    if id(cell._tc) in seen:
                        continue
                    seen.add(id(cell._tc))
                    if cell.text.strip():
                        cells.append(cell.text.strip().replace("\n", " "))
                if cells:
                    lines.append(" | ".join(cells))
    return "\n".join(lines)


def extract_text(path: Path, file_type: str) -> str:
    if file_type == "pdf":
        return extract_pdf_text(path)
    if file_type == "docx":
        return extract_docx_text(path)
    raise ResumeExtractionError("unsupported_type", "Unsupported file type.")


# ---- cleaning ------------------------------------------------------------
_BULLET_GLYPHS = "•●▪◦■➢➤○◆►·∙"  # only treated as bullets at the START of a line


def clean_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.replace(" ", " ").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​﻿]", "", text)
    out = []
    for line in text.split("\n"):
        line = line.rstrip()
        stripped = line.lstrip()
        if stripped and (stripped[0] in _BULLET_GLYPHS or 0xE000 <= ord(stripped[0]) <= 0xF8FF):
            rest = stripped[1:].strip()
            line = f"• {rest}" if rest else ""
        line = re.sub(r"[ \t]{2,}", " ", line)
        out.append(line)
    text = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ---- pipeline ------------------------------------------------------------
def parse_resume_file(
    path: Path,
    file_type: str,
    resume_id: str,
    *,
    min_chars: int = 100,
    ai_extractor: AIExtractor | None = None,
    ocr: OcrHandler | None = None,
) -> ParseOutcome:
    cleaned = clean_text(extract_text(path, file_type))
    if len(cleaned) < min_chars:
        ocr = ocr or OCR_HANDLER
        if file_type == "pdf" and ocr is not None:
            cleaned = clean_text(ocr(path))
        if len(cleaned) < min_chars:
            if file_type == "pdf":
                raise ResumeExtractionError(
                    "needs_ocr",
                    "Almost no text could be extracted. This looks like a scanned PDF; "
                    "OCR is not configured. Upload a text-based PDF or DOCX.",
                )
            raise ResumeExtractionError("empty_document", "The document contains too little text to parse.")
    return ParseOutcome(text=cleaned, profile=build_profile(cleaned, resume_id, ai_extractor))


def build_profile(text: str, resume_id: str, ai_extractor: AIExtractor | None = None) -> CandidateProfile:
    profile = extract_profile(text, resume_id)
    if ai_extractor is not None:
        ai_content = ai_extractor.extract(text)
        if ai_content is not None:
            profile = merge_ai_content(profile, ai_content, text)
            from engines.resume.profile_extractor import compute_missing_fields

            profile.missing_fields = compute_missing_fields(profile)
    profile.validation_warnings = validate_profile(profile, text)
    if any(i.severity != "info" for i in profile.validation_warnings) or profile.review_flags:
        profile.parsing_status = "completed_with_warnings"
    return profile
