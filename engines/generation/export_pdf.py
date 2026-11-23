"""PDF export (reportlab platypus): same content and structure as the DOCX, with wrapping, page breaks and clickable links.

Fonts: a system Unicode TrueType family (Arial / DejaVu) is used when available; otherwise built-in Helvetica. Characters
the chosen font cannot draw are replaced by '?' and reported in the export warnings - never silently dropped.
"""
import io
import logging
import os
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import HRFlowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from ai.schemas.document_schema import CoverLetterContent, ResumeContent, ScreeningContent, parse_content
from engines.generation.render import format_period, month_name

logger = logging.getLogger(__name__)
_FONT_SETS = [
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/ariali.ttf", "C:/Windows/Fonts/arialbi.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf"),
]
_family: str | None = None
_ttf: TTFont | None = None


def _fonts() -> tuple[str, str, str, str]:
    """(regular, bold, italic, bold-italic) font names; registers a Unicode family once if one exists."""
    global _family, _ttf
    if _family is None:
        _family = "Helvetica"
        for paths in _FONT_SETS:
            if all(os.path.exists(p) for p in paths):
                try:
                    names = ("JPSans", "JPSans-Bold", "JPSans-Italic", "JPSans-BoldItalic")
                    for n, p in zip(names, paths):
                        pdfmetrics.registerFont(TTFont(n, p))
                    pdfmetrics.registerFontFamily("JPSans", normal=names[0], bold=names[1], italic=names[2], boldItalic=names[3])
                    _ttf, _family = pdfmetrics.getFont("JPSans"), "JPSans"
                    break
                except Exception:  # unusable font file -> keep Helvetica
                    logger.warning("Could not register font family; using Helvetica")
    if _family == "JPSans":
        return "JPSans", "JPSans-Bold", "JPSans-Italic", "JPSans-BoldItalic"
    return "Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Helvetica-BoldOblique"


def _can_draw(ch: str) -> bool:
    if ch in "\n\t" or ord(ch) < 32:
        return False
    if _ttf is not None:
        return ord(ch) in _ttf.face.charToGlyph
    try:
        ch.encode("cp1252")
        return True
    except UnicodeEncodeError:
        return False


class _Text:
    """Escapes text for reportlab markup and records characters the font cannot draw."""

    def __init__(self):
        self.replaced: set[str] = set()

    def __call__(self, text) -> str:
        out = []
        for ch in str(text or ""):
            if ch in "\n\t":
                out.append(" ")
            elif _can_draw(ch):
                out.append(ch)
            elif ord(ch) >= 32:
                self.replaced.add(ch)
                out.append("?")
        return escape("".join(out))


def _styles(font):
    r, b, i, bi = font
    base = dict(fontName=r, fontSize=10, leading=13, textColor=colors.HexColor("#1a1a1a"), splitLongWords=1)
    return {
        "name": ParagraphStyle("name", **{**base, "fontName": b, "fontSize": 19, "leading": 22, "alignment": TA_CENTER}),
        "headline": ParagraphStyle("headline", **{**base, "fontSize": 11, "textColor": colors.HexColor("#555555"), "alignment": TA_CENTER}),
        "contact": ParagraphStyle("contact", **{**base, "alignment": TA_CENTER, "spaceAfter": 4}),
        "h": ParagraphStyle("h", **{**base, "fontName": b, "fontSize": 10.5, "textColor": colors.HexColor("#1F2D3D"), "spaceBefore": 8, "spaceAfter": 1, "keepWithNext": 1}),
        "body": ParagraphStyle("body", **{**base, "spaceAfter": 2}),
        "bullet": ParagraphStyle("bullet", **{**base, "leftIndent": 12, "bulletIndent": 2, "spaceAfter": 1.2}),
        "left": ParagraphStyle("left", **base),
        "right": ParagraphStyle("right", **{**base, "alignment": 2, "textColor": colors.HexColor("#555555")}),
        "letter": ParagraphStyle("letter", **{**base, "fontSize": 10.5, "leading": 14.5, "spaceAfter": 8}),
        "q": ParagraphStyle("q", **{**base, "fontName": b, "fontSize": 10.5, "spaceBefore": 6, "spaceAfter": 2, "keepWithNext": 1}),
        "title": ParagraphStyle("title", **{**base, "fontName": b, "fontSize": 15, "leading": 19, "spaceAfter": 4}),
        "bold": b, "italic": i,
    }


def _link(url: str, label: str, esc) -> str:
    safe = escape(url, {'"': "&quot;"})
    return f'<link href="{safe}" color="#0563C1"><u>{esc(label)}</u></link>'


def _contact(h: dict, esc) -> str:
    parts = []
    if h.get("email"):
        parts.append(_link(f"mailto:{h['email']}", h["email"], esc))
    for key in ("phone", "location"):
        if h.get(key):
            parts.append(esc(h[key]))
    for label, key in (("LinkedIn", "linkedin_url"), ("GitHub", "github_url"), ("Portfolio", "portfolio_url")):
        if h.get(key):
            parts.append(_link(h[key], label, esc))
    return "  |  ".join(parts)


def _row(left: str, right: str, st, width):
    t = Table([[Paragraph(left, st["left"]), Paragraph(right, st["right"])]], colWidths=[width * 0.74, width * 0.26])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                           ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
    return t


def _heading(text, st, esc, width):
    return [Paragraph(esc(text.upper()), st["h"]), HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#7F8C8D"), spaceAfter=3)]


def _resume_story(c: ResumeContent, st, esc, width):
    h = c.header.model_dump()
    story = [Paragraph(esc(h.get("full_name") or ""), st["name"])]
    if h.get("headline"):
        story.append(Paragraph(esc(h["headline"]), st["headline"]))
    story.append(Paragraph(_contact(h, esc), st["contact"]))
    if c.summary:
        story += _heading("Summary", st, esc, width) + [Paragraph(esc(c.summary), st["body"])]
    if c.skills:
        story += _heading("Skills", st, esc, width)
        story += [Paragraph(f'<font name="{st["bold"]}">{esc(g.label)}:</font> {esc(", ".join(g.items))}', st["body"]) for g in c.skills]
    if c.experience:
        story += _heading("Experience", st, esc, width)
        for e in c.experience:
            left = f'<font name="{st["bold"]}">{esc(e.company_name)}</font>' + (f' | <font name="{st["italic"]}">{esc(e.job_title)}</font>' if e.job_title else "")
            block = [_row(left, esc(format_period(e.start_date, e.end_date, e.is_current)), st, width)]
            if e.location:
                block.append(Paragraph(f'<font name="{st["italic"]}">{esc(e.location)}</font>', st["body"]))
            bullets = [Paragraph(esc(b.text), st["bullet"], bulletText="\u2022" if _can_draw("\u2022") else "-") for b in e.bullets]
            story.append(KeepTogether(block + bullets[:1]))
            story += bullets[1:]
    if c.projects:
        story += _heading("Projects", st, esc, width)
        for p in c.projects:
            line = f'<font name="{st["bold"]}">{esc(p.project_name)}</font>'
            if p.technologies:
                line += f' | <font name="{st["italic"]}">{esc(", ".join(p.technologies))}</font>'
            if p.project_url:
                line += " | " + _link(p.project_url, "Link", esc)
            bullets = [Paragraph(esc(b.text), st["bullet"], bulletText="\u2022" if _can_draw("\u2022") else "-") for b in p.bullets]
            story.append(KeepTogether([Paragraph(line, st["body"])] + bullets[:1]))
            story += bullets[1:]
    if c.education:
        story += _heading("Education", st, esc, width)
        for e in c.education:
            title = ", ".join(x for x in [e.degree, e.specialization] if x)
            left = f'<font name="{st["bold"]}">{esc(title)}</font>' + (f' | <font name="{st["italic"]}">{esc(e.institution)}</font>' if e.institution else "")
            story.append(KeepTogether([_row(left, esc(" - ".join(str(y) for y in (e.start_year, e.graduation_year) if y)), st, width)]
                                      + ([Paragraph(esc(e.grade), st["body"])] if e.grade else [])))
    if c.certifications:
        story += _heading("Certifications", st, esc, width)
        for x in c.certifications:
            left = f'<font name="{st["bold"]}">{esc(x.certification_name)}</font>' + (f' | <font name="{st["italic"]}">{esc(x.issuing_organization)}</font>' if x.issuing_organization else "")
            story.append(_row(left, esc(month_name(x.issue_date) or ""), st, width))
    if c.achievements:
        story += _heading("Achievements", st, esc, width)
        story += [Paragraph(esc(a.text), st["bullet"], bulletText="\u2022" if _can_draw("\u2022") else "-") for a in c.achievements]
    return story


def _letter_story(c: CoverLetterContent, h: dict, st, esc, width):
    story = [Paragraph(esc(h.get("full_name") or c.signature or ""), st["title"]), Paragraph(_contact(h, esc), st["left"]), Spacer(1, 14),
             Paragraph(esc(c.salutation), st["letter"])]
    story += [Paragraph(esc(p.text), st["letter"]) for p in c.paragraphs]
    story += [Spacer(1, 4), Paragraph(esc(c.closing), st["letter"]), Spacer(1, 14),
              Paragraph(f'<font name="{st["bold"]}">{esc(c.signature)}</font>', st["left"])]
    return story


def _screening_story(c: ScreeningContent, h: dict, title: str, st, esc, width):
    story = [Paragraph(esc(title), st["title"]), Paragraph(esc(h.get("full_name") or ""), st["body"]), Spacer(1, 6)]
    for a in c.answers:
        story += [Paragraph(esc(a.question), st["q"]), Paragraph(esc(a.answer), st["letter"])]
    return story


def export_pdf(doc_type: str, content: dict, header: dict, title: str, approved: bool, page: str = "A4") -> tuple[bytes, list[str]]:
    """-> (pdf bytes, warnings)."""
    parsed = parse_content(doc_type, content)
    font = _fonts()
    st, esc = _styles(font), _Text()
    size = letter if page.upper() == "LETTER" else A4
    margin = 17 * mm
    width = size[0] - 2 * margin - 12  # the default frame has 6pt padding per side; tables must fit inside it

    story = (_resume_story(parsed, st, esc, width) if doc_type == "resume"
             else _letter_story(parsed, header, st, esc, width) if doc_type == "cover_letter"
             else _screening_story(parsed, header, title, st, esc, width))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(font[0], 8)
        canvas.setFillColor(colors.HexColor("#777777"))
        note = "" if approved else "DRAFT - not approved for submission    "
        canvas.drawCentredString(size[0] / 2, 9 * mm, f"{note}Page {doc.page}")
        canvas.restoreState()

    out = io.BytesIO()
    SimpleDocTemplate(out, pagesize=size, leftMargin=margin, rightMargin=margin, topMargin=15 * mm, bottomMargin=17 * mm,
                      title=esc(title)[:200], author=esc(header.get("full_name") or "")[:100], subject="Generated by AI JobPilot").build(
        story, onFirstPage=footer, onLaterPages=footer)
    warnings = []
    if esc.replaced:
        warnings.append("These characters cannot be drawn by the PDF font and were replaced with '?': " + " ".join(sorted(esc.replaced))[:80]
                        + ". Use the DOCX export for full Unicode support.")
    return out.getvalue(), warnings
