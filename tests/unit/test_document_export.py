"""DOCX and PDF export: real files are generated, re-opened and inspected (text, links, layout, page breaks, hostile text)."""
import copy
import io
import zipfile

import pymupdf
import pytest
from docx import Document

from ai.schemas.document_schema import (
    Bullet, CertificationItem, CoverLetterContent, EducationItem, ExperienceItem, LetterParagraph, ProjectItem, ResumeContent, ResumeHeader,
    ScreeningAnswer, ScreeningContent, SkillGroup,
)
from ai.schemas.job_schema import JobInput
from engines.generation.cover_letter import compose_cover_letter
from engines.generation.exporters import CONTENT_TYPES, download_name, export, safe_part
from engines.generation.fact_pack import build_fact_pack
from engines.generation.render import format_period, month_name, render_lines
from engines.generation.resume_customizer import build_job_context
from engines.generation.screening_answers import compose_screening
from engines.matching.jd_parser import parse_jd
from engines.resume.resume_builder import build_resume
from config.scoring_config import Preferences
from tests.fixtures.candidate import synthetic_profile
from tests.fixtures.jd_dataset import JDS

JD = JDS[0]


@pytest.fixture
def parts(catalog):
    p = synthetic_profile()
    p.contact.linkedin_url, p.contact.github_url = "https://linkedin.com/in/janesample", "https://github.com/janesample"
    a = parse_jd(JobInput(job_description=JD["text"], job_title=JD["title"], company_name=JD["company"]), catalog)
    ctx = build_job_context(a, catalog, company_name=JD["company"], job_title=JD["title"])
    resume = build_resume(p, ctx, catalog)
    pack = build_fact_pack(p, ctx, catalog)
    return {"resume": resume.model_dump(mode="json"), "cover_letter": compose_cover_letter(pack).model_dump(mode="json"),
            "screening_answers": compose_screening(pack, Preferences(notice_period="30 days")).model_dump(mode="json"), "header": resume.header.model_dump()}


def pdf_text(data: bytes) -> tuple[str, pymupdf.Document]:
    doc = pymupdf.open(stream=data, filetype="pdf")
    return "\n".join(page.get_text() for page in doc), doc


def docx_text(data: bytes) -> str:
    return "\n".join(p.text for p in Document(io.BytesIO(data)).paragraphs)


def blank_resume(**kw) -> dict:
    return ResumeContent(header=ResumeHeader(full_name="Jane Sample"), **kw).model_dump(mode="json")


# ============================================================ DOCX
def test_resume_docx_contains_all_sections_in_order(parts):
    r = export("resume", parts["resume"], "docx", parts["header"], approved=True)
    text = docx_text(r.data)
    for needle in ("Jane Sample", "SUMMARY", "SKILLS", "EXPERIENCE", "PROJECTS", "EDUCATION", "CERTIFICATIONS", "Acme Analytics", "Beta Labs",
                   "Jan 2022 - Present", "Jun 2019 - Dec 2021", "Built a RAG pipeline", "Bachelor of Technology", "Azure AI Engineer Associate"):
        assert needle in text, needle
    assert text.index("SUMMARY") < text.index("SKILLS") < text.index("EXPERIENCE") < text.index("PROJECTS") < text.index("EDUCATION")
    assert zipfile.is_zipfile(io.BytesIO(r.data)) and r.sha256 and r.fmt == "docx" and CONTENT_TYPES["docx"].endswith("wordprocessingml.document")


def test_docx_has_clickable_hyperlinks_and_metadata(parts):
    data = export("resume", parts["resume"], "docx", parts["header"], approved=True, title="Resume - Jane").data
    doc = Document(io.BytesIO(data))
    targets = {rel.target_ref for rel in doc.part.rels.values() if rel.is_external}
    assert {"mailto:jane.sample@example.com", "https://linkedin.com/in/janesample", "https://github.com/janesample"} <= targets
    assert "w:hyperlink" in doc.element.xml and doc.core_properties.author == "Jane Sample" and doc.core_properties.title == "Resume - Jane"


def test_docx_draft_marker_only_when_not_approved(parts):
    draft = Document(io.BytesIO(export("resume", parts["resume"], "docx", parts["header"], approved=False).data))
    final = Document(io.BytesIO(export("resume", parts["resume"], "docx", parts["header"], approved=True).data))
    assert "DRAFT" in draft.sections[0].footer.paragraphs[0].text and "DRAFT" not in final.sections[0].footer.paragraphs[0].text


def test_docx_page_size_and_fonts(parts):
    a4 = Document(io.BytesIO(export("resume", parts["resume"], "docx", parts["header"], True, page="A4").data)).sections[0]
    us = Document(io.BytesIO(export("resume", parts["resume"], "docx", parts["header"], True, page="LETTER").data)).sections[0]
    assert round(a4.page_width.inches, 2) == 8.27 and round(us.page_width.inches, 2) == 8.5
    doc = Document(io.BytesIO(export("resume", parts["resume"], "docx", parts["header"], True).data))
    assert {r.font.name for p in doc.paragraphs for r in p.runs if r.font.name} == {"Calibri"}  # consistent font


def test_docx_survives_control_characters_and_xml_specials(parts):
    r = copy.deepcopy(parts["resume"])
    r["summary"] = "Built <b>fast</b> & reliable \x00systems\x07 with \"quotes\" and 'apostrophes' > 5 < 10"
    r["experience"][0]["bullets"][0]["text"] = "R&D lead: reduced cost by 20% – “smart quotes” •"
    text = docx_text(export("resume", r, "docx", parts["header"], True).data)
    assert "<b>fast</b> & reliable systems" in text and "R&D lead" in text and "\x00" not in text and "“smart quotes”" in text


def test_cover_letter_and_screening_docx(parts):
    cl = docx_text(export("cover_letter", parts["cover_letter"], "docx", parts["header"], True).data)
    assert cl.startswith("Jane Sample") and "Dear Hiring Manager," in cl and "Sincerely," in cl and cl.rstrip().endswith("Jane Sample")
    sc = docx_text(export("screening_answers", parts["screening_answers"], "docx", parts["header"], True, title="Screening answers - X").data)
    assert "Tell us about yourself." in sc and "My notice period is 30 days." in sc and sc.startswith("Screening answers - X")


def test_docx_is_valid_with_empty_sections_and_missing_name():
    for content, header in ((blank_resume(), {}), (ResumeContent().model_dump(mode="json"), {})):
        data = export("resume", content, "docx", header, False).data
        assert Document(io.BytesIO(data)).paragraphs is not None
    letter = CoverLetterContent(paragraphs=[LetterParagraph(text="Hello.")], signature=None).model_dump(mode="json")
    assert zipfile.is_zipfile(io.BytesIO(export("cover_letter", letter, "docx", {}, True).data))


# ============================================================ PDF
def test_resume_pdf_text_layout_and_links(parts):
    r = export("resume", parts["resume"], "pdf", parts["header"], approved=True, title="Resume - Jane")
    text, doc = pdf_text(r.data)
    for needle in ("Jane Sample", "SUMMARY", "EXPERIENCE", "Acme Analytics", "Jan 2022 - Present", "Built a RAG pipeline using LangChain",
                   "Bachelor of Technology, Computer Science", "Azure AI Engineer Associate"):
        assert needle in text, needle
    assert r.data.startswith(b"%PDF-") and r.fmt == "pdf" and r.warnings == []
    uris = {l.get("uri") for page in doc for l in page.get_links()}
    assert {"mailto:jane.sample@example.com", "https://linkedin.com/in/janesample", "https://github.com/janesample"} <= uris
    assert doc.metadata["title"] == "Resume - Jane" and doc.metadata["author"] == "Jane Sample"


def test_pdf_content_stays_inside_the_page(parts):
    _, doc = pdf_text(export("resume", parts["resume"], "pdf", parts["header"], True).data)
    for page in doc:
        w, h = page.rect.width, page.rect.height
        for x0, y0, x1, y1, *_ in page.get_text("blocks"):
            assert x0 >= 30 and x1 <= w - 30 and y0 >= 15 and y1 <= h - 15, (x0, y0, x1, y1)


def test_long_content_flows_across_pages_without_clipping(parts):
    big = copy.deepcopy(parts["resume"])
    big["experience"] = big["experience"] * 8
    for e in big["experience"]:
        e["bullets"] = [{"text": f"Delivered milestone number {i} with measurable improvements across several teams and systems in production.", "source": None}
                        for i in range(9)]
    r = export("resume", big, "pdf", parts["header"], True)
    text, doc = pdf_text(r.data)
    assert len(doc) >= 3
    assert text.count("Delivered milestone number 8") == 16 and "Page 3" in text            # nothing lost at page breaks
    w = doc[0].rect.width
    assert all(b[2] <= w - 30 for page in doc for b in page.get_text("blocks"))


def test_very_long_unbroken_strings_do_not_overflow(parts):
    r = copy.deepcopy(parts["resume"])
    r["experience"][0]["bullets"][0]["text"] = "https://example.com/" + "a" * 300 + " end"
    r["summary"] = "x" * 400
    text, doc = pdf_text(export("resume", r, "pdf", parts["header"], True).data)
    w = doc[0].rect.width
    assert all(b[2] <= w - 20 for page in doc for b in page.get_text("blocks")) and "end" in text.replace("\n", "")


def test_pdf_escapes_markup_in_text(parts):
    r = copy.deepcopy(parts["resume"])
    r["summary"] = "Tom & Jerry <script>alert(1)</script> use <b>bold</b> & 5 < 6 > 4"
    r["experience"][0]["company_name"] = "R&D <Labs>"
    text, _ = pdf_text(export("resume", r, "pdf", parts["header"], True).data)
    assert "Tom & Jerry <script>alert(1)</script>" in text and "<b>bold</b>" in text and "R&D <Labs>" in text and "5 < 6 > 4" in text


def test_pdf_draft_marker_page_numbers_and_page_size(parts):
    draft_text, draft = pdf_text(export("resume", parts["resume"], "pdf", parts["header"], False).data)
    final_text, final = pdf_text(export("resume", parts["resume"], "pdf", parts["header"], True, page="LETTER").data)
    assert "DRAFT - not approved for submission" in draft_text and "DRAFT" not in final_text and "Page 1" in final_text
    assert round(draft[0].rect.width) == 595 and round(final[0].rect.width) == 612


def test_pdf_reports_characters_it_cannot_draw(parts):
    r = copy.deepcopy(parts["resume"])
    r["summary"] = "Name: अनिल 张伟 ✓ done"
    res = export("resume", r, "pdf", parts["header"], True)
    text, _ = pdf_text(res.data)
    assert res.data.startswith(b"%PDF-") and "done" in text
    assert res.warnings and "cannot be drawn" in res.warnings[0] and "DOCX" in res.warnings[0]   # never silently dropped
    docx = docx_text(export("resume", r, "docx", parts["header"], True).data)
    assert "अनिल" in docx                                                       # DOCX keeps full Unicode


def test_latin_accents_and_symbols_render(parts):
    r = copy.deepcopy(parts["resume"])
    r["summary"] = "José Müller – café “quotes” • ₹ — done"
    res = export("resume", r, "pdf", parts["header"], True)
    text, _ = pdf_text(res.data)
    assert "José Müller" in text and "café" in text


def test_cover_letter_and_screening_pdf(parts):
    cl, _ = pdf_text(export("cover_letter", parts["cover_letter"], "pdf", parts["header"], True).data)
    assert "Dear Hiring Manager," in cl and "Sincerely," in cl and "Northwind AI" in cl
    sc, doc = pdf_text(export("screening_answers", parts["screening_answers"], "pdf", parts["header"], True, title="Screening answers").data)
    assert "Tell us about yourself." in sc and "What is your notice period?" in sc and len(doc) >= 1


def test_pdf_handles_empty_and_minimal_documents():
    for content in (blank_resume(), ResumeContent().model_dump(mode="json")):
        data = export("resume", content, "pdf", {}, False).data
        assert data.startswith(b"%PDF-") and len(pymupdf.open(stream=data, filetype="pdf")) == 1
    s = ScreeningContent(answers=[ScreeningAnswer(question_id="q", question="Q?", answer="A.")]).model_dump(mode="json")
    assert export("screening_answers", s, "pdf", {}, True).data.startswith(b"%PDF-")


def test_exports_are_deterministic_in_content(parts):
    a = pdf_text(export("resume", parts["resume"], "pdf", parts["header"], True).data)[0]
    b = pdf_text(export("resume", parts["resume"], "pdf", parts["header"], True).data)[0]
    assert a == b


def test_unknown_format_is_rejected(parts):
    with pytest.raises(ValueError):
        export("resume", parts["resume"], "exe", parts["header"], True)


# ============================================================ names + render helpers
@pytest.mark.parametrize("name,company,approved,expected", [
    ("Jane Sample", "Northwind AI", True, "Jane_Sample_Resume_Northwind_AI.docx"),
    ("Jane Sample", "Northwind AI", False, "DRAFT_Jane_Sample_Resume_Northwind_AI.docx"),
    ("José 张 O'Neil", None, True, "Jos_O_Neil_Resume.docx"),
    ("../../etc/passwd", "a/b\\c", True, "etc_passwd_Resume_a_b_c.docx"),
    (None, None, True, "Candidate_Resume.docx"),
])
def test_download_names_are_safe(name, company, approved, expected):
    assert download_name("resume", "docx", name, company, approved) == expected


def test_safe_part_limits_length():
    assert len(safe_part("x" * 200, "d")) == 40 and safe_part("!!!", "fallback") == "fallback"


def test_date_helpers_never_invent_months():
    assert month_name("2022-01") == "Jan 2022" and month_name("2019") == "2019" and month_name(None) is None and month_name("2022-13") == "2022-13"
    assert format_period("2022-01", None, True) == "Jan 2022 - Present" and format_period("2019", "2021", False) == "2019 - 2021"
    assert format_period(None, None, False) == "" and format_period("2022-01", None, False) == "Jan 2022"


def test_render_lines_for_comparison(parts):
    lines = render_lines("resume", parts["resume"])
    assert lines[0] == "# Jane Sample" and "## Experience" in lines and any(l.startswith("- Built a RAG pipeline") for l in lines)
    assert render_lines("cover_letter", parts["cover_letter"])[0] == "Dear Hiring Manager,"
    assert any(l.startswith("A [answered]") for l in render_lines("screening_answers", parts["screening_answers"]))
