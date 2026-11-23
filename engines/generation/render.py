"""Plain-text views of documents (used for version comparison and previews) + shared date formatting."""
from ai.schemas.document_schema import CoverLetterContent, ResumeContent, ScreeningContent, parse_content

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def month_name(value: str | None) -> str | None:
    """'2022-01' -> 'Jan 2022'; '2019' -> '2019'. Never invents a month."""
    if not value:
        return None
    if len(value) >= 7 and value[4] == "-" and value[5:7].isdigit() and 1 <= int(value[5:7]) <= 12:
        return f"{_MONTHS[int(value[5:7]) - 1]} {value[:4]}"
    return value


def format_period(start: str | None, end: str | None, current: bool) -> str:
    s, e = month_name(start), ("Present" if current else month_name(end))
    if s and e:
        return f"{s} - {e}"
    return s or e or ""


def resume_lines(c: ResumeContent) -> list[str]:
    h = c.header
    lines = [f"# {h.full_name or ''}".strip(), h.headline or "", " | ".join(x for x in [h.email, h.phone, h.location, h.linkedin_url, h.github_url, h.portfolio_url] if x)]
    if c.summary:
        lines += ["## Summary", c.summary]
    if c.skills:
        lines.append("## Skills")
        lines += [f"{g.label}: {', '.join(g.items)}" for g in c.skills]
    if c.experience:
        lines.append("## Experience")
        for e in c.experience:
            lines.append(f"{e.job_title or ''} - {e.company_name or ''} ({format_period(e.start_date, e.end_date, e.is_current)})")
            lines += [f"- {b.text}" for b in e.bullets]
    if c.projects:
        lines.append("## Projects")
        for p in c.projects:
            lines.append(f"{p.project_name or ''} [{', '.join(p.technologies)}]")
            lines += [f"- {b.text}" for b in p.bullets]
    if c.education:
        lines.append("## Education")
        lines += [" - ".join(x for x in [e.degree, e.specialization, e.institution, str(e.graduation_year or ""), e.grade] if x) for e in c.education]
    if c.certifications:
        lines.append("## Certifications")
        lines += [" - ".join(x for x in [x.certification_name, x.issuing_organization, month_name(x.issue_date)] if x) for x in c.certifications]
    if c.achievements:
        lines.append("## Achievements")
        lines += [f"- {a.text}" for a in c.achievements]
    return [l for l in lines if l is not None]


def cover_letter_lines(c: CoverLetterContent) -> list[str]:
    return [c.salutation, "", *[p.text for p in c.paragraphs], "", c.closing, c.signature or ""]


def screening_lines(c: ScreeningContent) -> list[str]:
    out = []
    for a in c.answers:
        out += [f"Q: {a.question}", f"A [{a.status}]: {a.answer}", ""]
    return out


def render_lines(doc_type: str, content: dict) -> list[str]:
    parsed = parse_content(doc_type, content)
    return {"resume": resume_lines, "cover_letter": cover_letter_lines, "screening_answers": screening_lines}[doc_type](parsed)
