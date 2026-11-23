"""Anthropic Claude client for JSON extraction tasks.

* Used only when ENABLE_AI_MATCHING, an API key and explicit consent are all present.
* The application never depends on it: callers fall back to the deterministic engine.
* Retries transient failures (rate limit, timeout, connection, 5xx) with exponential backoff.
* Validates every response with Pydantic. Logs metadata only - never prompts, resume/JD text or keys.
"""
import json
import logging
import re
import time
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from config.settings import Settings

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)
PROMPT_DIR = Path(__file__).parent / "prompts"


class AIUnavailable(Exception):
    """Claude is not configured, not allowed, or the request cannot succeed."""


class AIResponseError(Exception):
    """Claude answered, but not with valid JSON for the expected schema."""


def load_prompt(name: str) -> str:
    return (PROMPT_DIR / f"{name}.txt").read_text(encoding="utf-8")


def ai_status(settings: Settings) -> dict:
    """Why AI is (not) used - shown in the UI so a deterministic result is never mistaken for AI."""
    gemini = settings.ai_provider == "gemini"
    key_name, who = ("GEMINI_API_KEY", "Google") if gemini else ("ANTHROPIC_API_KEY", "Anthropic")
    if not settings.enable_ai_matching:
        state, why = "disabled", "ENABLE_AI_MATCHING is false"
    elif not settings.ai_api_key:
        state, why = "no_api_key", f"{key_name} is not set"
    elif not settings.ai_consent_external_processing:
        state, why = "no_consent", f"AI_CONSENT_EXTERNAL_PROCESSING is not true (job text would be sent to {who})"
    else:
        state, why = "enabled", f"{settings.ai_provider_name} is configured"
    return {"state": state, "enabled": state == "enabled", "reason": why,
            "model": settings.ai_model if state == "enabled" else None, "provider": settings.ai_provider}


def _transient_types():
    import anthropic

    return (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.APITimeoutError,
            anthropic.InternalServerError)


class ClaudeClient:
    def __init__(self, settings: Settings, client=None, sleep=time.sleep):
        self.settings = settings
        self.model = settings.claude_model
        self._sleep = sleep
        if client is None:
            import anthropic

            # max_retries=0: retrying is handled here so it is logged and bounded.
            client = anthropic.Anthropic(
                api_key=settings.anthropic_api_key, timeout=settings.claude_timeout, max_retries=0
            )
        self._client = client

    def complete_json(self, system: str, user: str, model_cls: type[T], max_tokens: int | None = None) -> T:
        schema = json.dumps(model_cls.model_json_schema())
        prompt = f"{user}\n\nReturn ONLY one JSON object that conforms to this JSON schema:\n{schema}"
        last_error: Exception | None = None
        for attempt in range(2):  # second attempt only for malformed output
            text = self._call(system, prompt, max_tokens or self.settings.claude_max_tokens)
            try:
                match = re.search(r"\{.*\}", text, re.DOTALL)
                if not match:
                    raise ValueError("no JSON object in response")
                return model_cls.model_validate(json.loads(match.group(0)))
            except (ValueError, ValidationError) as exc:
                last_error = exc
                logger.warning("Claude returned invalid JSON for %s (attempt %d): %s",
                               model_cls.__name__, attempt + 1, type(exc).__name__)
                prompt += "\n\nYour previous reply was not valid JSON for the schema. Reply with the JSON object only."
        raise AIResponseError(f"Claude response was not valid for {model_cls.__name__}") from last_error

    def _call(self, system: str, prompt: str, max_tokens: int) -> str:
        import anthropic

        transient = _transient_types()
        attempts = self.settings.claude_max_retries + 1
        for attempt in range(1, attempts + 1):
            started = time.monotonic()
            try:
                msg = self._client.messages.create(
                    model=self.model, max_tokens=max_tokens, system=system,
                    messages=[{"role": "user", "content": prompt}],
                )
            except transient as exc:
                logger.warning("Claude transient error %s (attempt %d/%d)", type(exc).__name__, attempt, attempts)
                if attempt == attempts:
                    raise AIUnavailable("Claude is temporarily unavailable") from None
                self._sleep(min(2 ** (attempt - 1), 16))
                continue
            except anthropic.APIStatusError as exc:
                if exc.status_code >= 500:  # e.g. 529 overloaded
                    if attempt == attempts:
                        raise AIUnavailable("Claude is temporarily unavailable") from None
                    self._sleep(min(2 ** (attempt - 1), 16))
                    continue
                logger.error("Claude request rejected: status=%s", exc.status_code)
                raise AIUnavailable(f"Claude request was rejected (HTTP {exc.status_code})") from None
            except Exception as exc:
                logger.error("Claude call failed: %s", type(exc).__name__)
                raise AIUnavailable("Claude request failed") from None
            usage = getattr(msg, "usage", None)
            logger.info(
                "Claude call ok: model=%s attempt=%d latency_ms=%d in_tokens=%s out_tokens=%s",
                self.model, attempt, int((time.monotonic() - started) * 1000),
                getattr(usage, "input_tokens", "?"), getattr(usage, "output_tokens", "?"),
            )
            return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        raise AIUnavailable("Claude request failed")  # pragma: no cover


GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class GeminiClient(ClaudeClient):
    """Google Gemini through its public REST API (same ``complete_json`` contract, same fallbacks and validation).

    The key travels in the ``x-goog-api-key`` header, never in a URL, and is never logged. Only the allow-listed
    Google host is contacted.
    """

    def __init__(self, settings: Settings, client=None, sleep=time.sleep):
        self.settings = settings
        self.model = settings.gemini_model
        self._sleep = sleep
        if client is None:
            import httpx

            client = httpx.Client(timeout=settings.claude_timeout)
        self._client = client

    def _call(self, system: str, prompt: str, max_tokens: int) -> str:
        import httpx

        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            # reasoning tokens count against the limit on current Gemini models, so leave head-room for the answer
            "generationConfig": {"maxOutputTokens": min(max_tokens * 2, 65536), "responseMimeType": "application/json"},
        }
        headers = {"x-goog-api-key": self.settings.gemini_api_key, "Content-Type": "application/json"}
        attempts = self.settings.claude_max_retries + 1
        for attempt in range(1, attempts + 1):
            started = time.monotonic()
            try:
                resp = self._client.post(GEMINI_URL.format(model=self.model), json=body, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                logger.warning("Gemini transient error %s (attempt %d/%d)", type(exc).__name__, attempt, attempts)
                if attempt == attempts:
                    raise AIUnavailable("Gemini is temporarily unavailable") from None
                self._sleep(min(2 ** (attempt - 1), 16))
                continue
            except Exception as exc:  # noqa: BLE001
                logger.error("Gemini call failed: %s", type(exc).__name__)
                raise AIUnavailable("Gemini request failed") from None
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == attempts:
                    raise AIUnavailable("Gemini is temporarily unavailable")
                self._sleep(min(2 ** (attempt - 1), 16))
                continue
            if resp.status_code != 200:
                logger.error("Gemini request rejected: status=%s", resp.status_code)
                raise AIUnavailable(f"Gemini request was rejected (HTTP {resp.status_code})")
            try:
                data = resp.json()
                parts = data["candidates"][0]["content"]["parts"]
                text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
            except (ValueError, KeyError, IndexError, TypeError):
                raise AIResponseError("Gemini response had no text") from None
            usage = data.get("usageMetadata") or {}
            logger.info("Gemini call ok: model=%s attempt=%d latency_ms=%d in_tokens=%s out_tokens=%s", self.model, attempt,
                        int((time.monotonic() - started) * 1000), usage.get("promptTokenCount", "?"), usage.get("candidatesTokenCount", "?"))
            return text
        raise AIUnavailable("Gemini request failed")  # pragma: no cover


def new_ai_client(settings: Settings, client=None, sleep=time.sleep) -> ClaudeClient:
    """The client of the selected provider. Callers must already have checked the status/consent."""
    cls = GeminiClient if settings.ai_provider == "gemini" else ClaudeClient
    return cls(settings, client=client, sleep=sleep)


def build_claude_client(settings: Settings) -> ClaudeClient | None:
    return new_ai_client(settings) if ai_status(settings)["enabled"] else None
