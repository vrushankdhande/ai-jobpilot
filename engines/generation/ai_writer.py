"""Optional Claude wording for summaries, cover letters and screening answers.

Claude receives ONLY the fact pack (no name, email, phone, links or location) and may only rephrase it. Its output is
untrusted: the caller re-validates it with the factuality validator and falls back to the deterministic text if it
contains any unsupported claim. Failures return None (the deterministic path is always available).
"""
import json
import logging

from pydantic import BaseModel, Field

from ai.claude_client import AIResponseError, AIUnavailable, ClaudeClient, load_prompt, new_ai_client
from config.settings import Settings
from engines.generation.fact_pack import FactPack

logger = logging.getLogger(__name__)


class AISummary(BaseModel):
    summary: str = Field(default="", max_length=700)


class AILetter(BaseModel):
    paragraphs: list[str] = Field(default_factory=list, max_length=8)


class AIAnswerItem(BaseModel):
    question_id: str = Field(max_length=60)
    answer: str = Field(max_length=2500)


class AIAnswers(BaseModel):
    answers: list[AIAnswerItem] = Field(default_factory=list, max_length=40)


def document_ai_status(settings: Settings) -> dict:
    """Is Claude allowed to word documents? Needs the feature flag, an API key AND explicit consent for documents."""
    if not settings.enable_ai_matching:
        state, why = "disabled", "ENABLE_AI_MATCHING is false"
    elif not settings.ai_api_key:
        state, why = "no_api_key", ("GEMINI_API_KEY" if settings.ai_provider == "gemini" else "ANTHROPIC_API_KEY") + " is not set"
    elif not settings.ai_documents_consent:
        who = "Google" if settings.ai_provider == "gemini" else "Anthropic"
        state, why = "no_consent", f"AI_DOCUMENTS_CONSENT is not true (profile content - not your name or contact details - would be sent to {who})"
    else:
        state, why = "enabled", f"{settings.ai_provider_name} may word summaries, letters and answers from verified facts"
    return {"state": state, "enabled": state == "enabled", "reason": why, "model": settings.ai_model if state == "enabled" else None}


def build_document_client(settings: Settings) -> ClaudeClient | None:
    return new_ai_client(settings) if document_ai_status(settings)["enabled"] else None


def _facts_block(pack: FactPack) -> str:
    return "<facts>\n" + json.dumps(pack.for_ai(), ensure_ascii=False) + "\n</facts>"


def ai_summary(pack: FactPack, client: ClaudeClient) -> str | None:
    try:
        out = client.complete_json(load_prompt("resume_generation"), _facts_block(pack), AISummary, max_tokens=600)
    except (AIUnavailable, AIResponseError):
        return None
    return out.summary.strip() or None


def ai_letter(pack: FactPack, client: ClaudeClient) -> list[str] | None:
    try:
        out = client.complete_json(load_prompt("cover_letter"), _facts_block(pack), AILetter, max_tokens=1500)
    except (AIUnavailable, AIResponseError):
        return None
    paras = [p.strip() for p in out.paragraphs if p and p.strip()]
    return paras if 2 <= len(paras) <= 6 else None


def ai_answers(pack: FactPack, questions: list[dict], length: str, client: ClaudeClient) -> dict[str, str] | None:
    qs = json.dumps([{"question_id": q["id"], "question": q["text"]} for q in questions], ensure_ascii=False)
    user = f"{_facts_block(pack)}\n<questions length=\"{length}\">\n{qs}\n</questions>"
    try:
        out = client.complete_json(load_prompt("screening_answers"), user, AIAnswers, max_tokens=3000)
    except (AIUnavailable, AIResponseError):
        return None
    return {a.question_id: a.answer.strip() for a in out.answers if a.answer.strip()}
