"""Generation pipeline: verified profile + job -> validated document content (deterministic, optionally Claude-worded).

If Claude is used, its text is only accepted when the factuality validator finds no error in it. Otherwise the
deterministic text is used and the reason is recorded in ``notes`` - an AI failure never blocks generation and an
unsupported AI claim never reaches a document.
"""
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from ai.claude_client import ClaudeClient
from ai.schemas.document_schema import PLACEHOLDER, CoverLetterContent, DocumentValidation, LetterParagraph, ScreeningAnswer
from config.scoring_config import Preferences
from engines.generation import ai_writer
from engines.generation.cover_letter import compose_cover_letter
from engines.generation.fact_pack import FactPack
from engines.generation.resume_customizer import JobContext
from engines.generation.screening_answers import DEFAULT_QUESTIONS, compose_screening, effective_inputs
from engines.matching.skill_extractor import SkillCatalog
from engines.resume.resume_builder import ResumeOptions, build_resume, compose_summary

logger = logging.getLogger(__name__)
Validate = Callable[[str, dict], DocumentValidation]


@dataclass
class Generated:
    content: dict
    generator: str = "deterministic"
    ai_model: str | None = None
    notes: list[str] = field(default_factory=list)


def _errors(v: DocumentValidation) -> list[str]:
    return [f"{i.field}: {i.message}" for i in v.issues if i.severity == "error"]


def generate_resume(profile, ctx: JobContext, pack: FactPack, catalog: SkillCatalog, validate: Validate,
                    options: ResumeOptions | None = None, ai: ClaudeClient | None = None) -> Generated:
    resume = build_resume(profile, ctx, catalog, options)
    gen = Generated(resume.model_dump(mode="json"), notes=list(resume.notes))
    if ai is not None:
        text = ai_writer.ai_summary(pack, ai)
        if text is None:
            gen.notes.append("Claude summary unavailable; the deterministic summary was used.")
        else:
            cand = build_resume(profile, ctx, catalog, options, summary=text, summary_sources=["ai"])
            errs = _errors(validate("resume", cand.model_dump(mode="json")))
            if errs:
                gen.notes.append("Claude summary rejected (unsupported claim): " + "; ".join(errs[:2]))
            else:
                gen.content, gen.generator, gen.ai_model = cand.model_dump(mode="json"), "claude+deterministic", ai.model
    return gen


def generate_cover_letter(pack: FactPack, validate: Validate, ai: ClaudeClient | None = None) -> Generated:
    letter = compose_cover_letter(pack)
    gen = Generated(letter.model_dump(mode="json"), notes=list(letter.missing))
    if ai is not None:
        paras = ai_writer.ai_letter(pack, ai)
        if paras is None:
            gen.notes.append("Claude letter unavailable; the deterministic letter was used.")
        else:
            cand = CoverLetterContent(paragraphs=[LetterParagraph(text=t, sources=["ai"]) for t in paras], signature=letter.signature,
                                      company_name=letter.company_name, job_title=letter.job_title, missing=letter.missing)
            errs = _errors(validate("cover_letter", cand.model_dump(mode="json")))
            if errs:
                gen.notes.append("Claude letter rejected (unsupported claim): " + "; ".join(errs[:2]))
            else:
                gen.content, gen.generator, gen.ai_model = cand.model_dump(mode="json"), "claude+deterministic", ai.model
    return gen


def generate_screening(pack: FactPack, prefs: Preferences, validate: Validate, questions: list[dict] | None = None, length: str = "medium",
                       candidate_inputs: dict | None = None, ai: ClaudeClient | None = None) -> Generated:
    content = compose_screening(pack, prefs, questions, length, candidate_inputs)
    gen = Generated(content.model_dump(mode="json"))
    if ai is None:
        return gen
    chosen = [{"id": a.question_id, "text": a.question} for a in content.answers]
    drafted = ai_writer.ai_answers(pack, chosen, length, ai)
    if drafted is None:
        gen.notes.append("Claude answers unavailable; deterministic answers were used.")
        return gen
    used = 0
    answers = []
    for a in content.answers:
        text = drafted.get(a.question_id)
        # personal details are never AI-written, and answers the deterministic engine could not give stay needs_input
        if not text or "NEEDS_INPUT" in text.upper() or a.question_id in ("notice_period", "salary_expectation", "relocation", "work_authorization") \
                or (a.status == "needs_input" and not a.question_id.startswith("custom_")):
            answers.append(a)
            continue
        cand = ScreeningAnswer(question_id=a.question_id, question=a.question, answer=text, status="draft", length=a.length,
                               sources=["ai"], note="Worded by Claude from your verified facts - review before approving.")
        trial = content.model_copy(update={"answers": [cand]})
        if _errors(validate("screening_answers", trial.model_dump(mode="json"))):
            gen.notes.append(f"Claude's answer to '{a.question[:40]}' rejected (unsupported claim); the deterministic answer was kept.")
            answers.append(a)
        else:
            answers.append(cand)
            used += 1
    if used:
        gen.content = content.model_copy(update={"answers": answers}).model_dump(mode="json")
        gen.generator, gen.ai_model = "claude+deterministic", ai.model
    return gen
