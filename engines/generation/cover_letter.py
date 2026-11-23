"""Cover letter generation from the verified fact pack.

Deterministic template (always available). Sentences are assembled from the candidate's own verbatim facts and the job's
title/company/duties; the letter makes no claim that is not in the profile. Missing details (company, hiring manager,
your name) are left neutral or marked as needing input - never guessed.
"""
import re

from ai.schemas.document_schema import PLACEHOLDER, CoverLetterContent, LetterParagraph
from engines.generation.fact_pack import FactPack

_IRREGULAR_PAST = {"built", "led", "ran", "wrote", "won", "drove", "grew", "made", "took", "taught", "began", "chose", "set", "put", "cut", "got"}


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def first_person(text: str) -> str:
    """'Built a RAG pipeline.' -> 'I built a RAG pipeline'; anything else is quoted as 'my work included: ...'."""
    t = re.sub(r"\s+", " ", text).strip().rstrip(".")
    first = t.split(" ", 1)[0]
    if first.lower() in _IRREGULAR_PAST or (first.lower().endswith("ed") and len(first) > 3 and first[0].isupper()):
        return "I " + first.lower() + (" " + t.split(" ", 1)[1] if " " in t else "")
    return f"my work included: {t[0].lower() + t[1:] if t and t[1:2].islower() else t}"


def cap_first(s: str) -> str:
    """Upper-case the first character only (never lower-case the rest: acronyms such as RAG must survive)."""
    return s[:1].upper() + s[1:]


def _sentence(s: str) -> str:
    s = s.strip()
    return s[0].upper() + s[1:] + ("" if s.endswith((".", "!", "?")) else ".") if s else s


def compose_cover_letter(p: FactPack) -> CoverLetterContent:
    missing: list[str] = []
    paragraphs: list[LetterParagraph] = []

    # 1. opening
    if p.job_title and p.company:
        target = f"the {p.job_title} position at {p.company}"
    elif p.job_title:
        target = f"the {p.job_title} position"
    elif p.company:
        target = f"a role at {p.company}"
    else:
        target = "the open position"
    open_ = f"I am writing to apply for {target}."
    src = []
    if p.title and p.years and p.years >= 1:
        open_ += f" As a {p.title} with {int(p.years)}+ years of experience"
        src += ["summary.current_job_title", "summary.total_experience_years"]
        open_ += f", I work with {_join(p.matched_skills[:4])}, which this role calls for." if p.matched_skills else "."
    elif p.title:
        open_ += f" I currently work as a {p.title}" + (f" and work with {_join(p.matched_skills[:4])}, which this role calls for." if p.matched_skills else ".")
        src.append("summary.current_job_title")
    elif p.matched_skills:
        open_ += f" My background includes {_join(p.matched_skills[:4])}, which this role calls for."
    src += [f"technical_skills ({s})" for s in p.matched_skills[:4]]
    paragraphs.append(LetterParagraph(text=open_, sources=src))

    # 2. most relevant role
    roles = [r for r in p.roles if r.bullets and r.relevance > 0] or [r for r in p.roles if r.bullets]
    if roles:
        r = roles[0]
        lead = f"In my {'current ' if r.is_current else ''}role as {r.job_title or 'team member'}" + (f" at {r.company_name}" if r.company_name else "")
        lead += f" ({r.period})," if r.period else ","
        body = f"{lead} {first_person(r.bullets[0][0])}."
        srcs = [r.bullets[0][1]]
        if len(r.bullets) > 1:
            body += f" {_sentence(first_person(r.bullets[1][0]).replace('my work included:', 'My work also included:', 1))}"
            srcs.append(r.bullets[1][1])
        if p.best_responsibility:
            body += f' The role lists the duty "{p.best_responsibility}", which is close to this work.'
        paragraphs.append(LetterParagraph(text=body, sources=srcs + [f"work_experience[{r.index}]"]))
        # 3. earlier role / project / measurable result
        extra = []
        srcs = []
        if len(roles) > 1:
            r2 = roles[1]
            extra.append(f"Previously, as {r2.job_title or 'a team member'}" + (f" at {r2.company_name}" if r2.company_name else "") + f", {first_person(r2.bullets[0][0])}.")
            srcs += [r2.bullets[0][1], f"work_experience[{r2.index}]"]
        if p.projects and p.projects[0].bullets:
            pr = p.projects[0]
            extra.append(f"I also built {pr.name or 'a project'}" + (f" using {_join(pr.technologies[:4])}" if pr.technologies else "") +
                         f", where {first_person(pr.bullets[0][0])}.")
            srcs.append(pr.bullets[0][1])
        shown = {t for r_ in roles[:2] for t, _ in r_.bullets[:2]}
        ach = next(((t, s) for t, s in p.achievements if t not in shown), None)
        if ach and not extra:
            extra.append(f"A result I am proud of: {ach[0].rstrip('.')}.")
            srcs.append(ach[1])
        if extra:
            paragraphs.append(LetterParagraph(text=" ".join(extra), sources=srcs))
    elif not p.projects:
        missing.append("No work experience or projects were available in the verified profile to describe.")

    # 4. toolkit + education (facts only)
    tail = []
    srcs = []
    more = [s for s in p.matched_skills[4:8]] or [s for s in p.top_skills if s not in p.matched_skills][:3]
    if more:
        tail.append(f"My toolkit also includes {_join(more)}.")
        srcs += [f"technical_skills ({s})" for s in more]
    if p.education:
        tail.append(f"I hold a {p.education}.")
        srcs.append(p.education_source or "education")
    if tail:
        paragraphs.append(LetterParagraph(text=" ".join(tail), sources=srcs))

    # 5. closing
    who = p.company or "your team"
    paragraphs.append(LetterParagraph(
        text=f"I would welcome the opportunity to discuss how my background could contribute to {who}. Thank you for your time and consideration."))
    if not p.company:
        missing.append("Company name is unknown: the letter addresses 'your team'.")
    signature = p.name
    if not signature:
        signature = f"{PLACEHOLDER}: your name]"
        missing.append("Your name is not in the profile.")
    return CoverLetterContent(paragraphs=paragraphs, signature=signature, company_name=p.company, job_title=p.job_title, missing=missing)
