"""Gemini provider: same contract as the Claude client, tested with a mocked HTTP transport (no network)."""
import json
import logging

import httpx
import pytest
from pydantic import BaseModel

from ai.claude_client import AIResponseError, AIUnavailable, ClaudeClient, GeminiClient, ai_status, build_claude_client, new_ai_client
from config.settings import Settings
from engines.generation.ai_writer import build_document_client, document_ai_status
from engines.resume.ai_extractor import GeminiExtractor, build_ai_extractor

KEY = "AQ.test-gemini-key-not-real-0123456789"
pytestmark = pytest.mark.usefixtures("no_network")


class Reply(BaseModel):
    answer: str


def settings(**kw) -> Settings:
    base = dict(_env_file=None, ai_provider="gemini", gemini_api_key=KEY, enable_ai_matching=True, ai_consent_external_processing=True,
                claude_max_retries=2)
    return Settings(**{**base, **kw})


def ok(text: str) -> httpx.Response:
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}],
                                     "usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 7}})


def client_for(*responses, s=None):
    seen: list[httpx.Request] = []
    queue = list(responses)

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item

    return GeminiClient(s or settings(), client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _s: None), seen


def test_status_is_provider_aware_and_never_mentions_the_other_providers_key():
    assert ai_status(settings())["enabled"] and ai_status(settings())["model"] == "gemini-3.1-flash-lite" and ai_status(settings())["provider"] == "gemini"
    assert ai_status(settings(gemini_api_key=""))["state"] == "no_api_key" and "GEMINI_API_KEY" in ai_status(settings(gemini_api_key=""))["reason"]
    assert ai_status(settings(anthropic_api_key="sk-other"))["enabled"]                              # an Anthropic key does not matter when gemini is selected
    assert ai_status(settings(gemini_api_key="", anthropic_api_key="sk-other"))["state"] == "no_api_key"
    no_consent = ai_status(settings(ai_consent_external_processing=False))
    assert no_consent["state"] == "no_consent" and "Google" in no_consent["reason"] and not no_consent["enabled"]
    assert ai_status(settings(enable_ai_matching=False))["state"] == "disabled"


def test_builders_pick_the_selected_provider_and_return_nothing_without_consent():
    assert isinstance(build_claude_client(settings()), GeminiClient)
    assert build_claude_client(settings(ai_consent_external_processing=False)) is None
    assert type(new_ai_client(settings(ai_provider="anthropic", anthropic_api_key="k"), client=object())) is ClaudeClient
    assert build_document_client(settings()) is None                                                   # document consent is separate and off
    assert isinstance(build_document_client(settings(ai_documents_consent=True)), GeminiClient)
    assert document_ai_status(settings(ai_documents_consent=True))["model"] == "gemini-3.1-flash-lite"
    assert build_ai_extractor(settings()) is None                                                      # extraction needs its own switch
    assert isinstance(build_ai_extractor(settings(ai_extraction_enabled=True)), GeminiExtractor)


def test_request_shape_key_in_header_only_and_json_mode():
    c, seen = client_for(ok('{"answer": "hi"}'))
    assert c.complete_json("be brief", "question", Reply).answer == "hi"
    req = seen[0]
    assert req.url.host == "generativelanguage.googleapis.com" and req.url.path.endswith("/models/gemini-3.1-flash-lite:generateContent")
    assert req.headers["x-goog-api-key"] == KEY and KEY not in str(req.url) and not req.url.query
    body = json.loads(req.content)
    assert body["systemInstruction"]["parts"][0]["text"] == "be brief" and body["generationConfig"]["responseMimeType"] == "application/json"
    assert "question" in body["contents"][0]["parts"][0]["text"] and KEY not in req.content.decode()


def test_retries_rate_limits_and_server_errors_then_succeeds():
    c, seen = client_for(httpx.Response(429), httpx.Response(503), ok('{"answer": "later"}'))
    assert c.complete_json("s", "u", Reply).answer == "later" and len(seen) == 3


def test_gives_up_after_the_retry_budget_with_a_safe_message():
    c, seen = client_for(httpx.Response(503), s=settings(claude_max_retries=1))
    with pytest.raises(AIUnavailable, match="temporarily unavailable"):
        c.complete_json("s", "u", Reply)
    assert len(seen) == 2


def test_timeouts_are_retried_then_reported_without_details():
    c, seen = client_for(httpx.ReadTimeout("boom"), s=settings(claude_max_retries=1))
    with pytest.raises(AIUnavailable, match="temporarily unavailable"):
        c.complete_json("s", "u", Reply)
    assert len(seen) == 2


def test_a_rejected_request_is_not_retried_and_does_not_leak_the_key():
    c, seen = client_for(httpx.Response(403, json={"error": {"message": f"bad key {KEY}"}}))
    with pytest.raises(AIUnavailable) as exc:
        c.complete_json("s", "u", Reply)
    assert KEY not in str(exc.value) and "403" in str(exc.value) and len(seen) == 1


def test_malformed_replies_raise_response_errors():
    for response in (httpx.Response(200, json={"candidates": []}), httpx.Response(200, text="not json")):
        c, _ = client_for(response)
        with pytest.raises(AIResponseError):
            c.complete_json("s", "u", Reply)
    c, seen = client_for(ok("sorry, no json"))                                                         # valid HTTP, wrong content: one repair attempt, then error
    with pytest.raises(AIResponseError):
        c.complete_json("s", "u", Reply)
    assert len(seen) == 2


def test_logs_contain_metadata_only(caplog):
    c, _ = client_for(ok('{"answer": "SECRET-ANSWER-TEXT"}'))
    with caplog.at_level(logging.DEBUG):
        c.complete_json("SECRET-SYSTEM", "SECRET-USER-PROMPT", Reply)
    text = caplog.text
    assert "Gemini call ok" in text and KEY not in text and "SECRET-ANSWER-TEXT" not in text and "SECRET-USER-PROMPT" not in text and "SECRET-SYSTEM" not in text


def test_gemini_extractor_validates_and_falls_back_silently():
    good = '{"contact": {}, "summary": {"current_job_title": "Engineer"}}'
    ex = GeminiExtractor(settings(ai_extraction_enabled=True), client=httpx.Client(transport=httpx.MockTransport(lambda r: ok(good))))
    assert ex.extract("resume text").summary.current_job_title == "Engineer"
    bad = GeminiExtractor(settings(), client=httpx.Client(transport=httpx.MockTransport(lambda r: ok("no json here"))))
    assert bad.extract("resume text") is None
    down = GeminiExtractor(settings(claude_max_retries=0), client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    assert down.extract("resume text") is None


def test_the_key_is_never_shown_in_settings_output():
    s = settings()
    assert KEY not in repr(s) and KEY not in str(s) and s.ai_api_key == KEY and s.ai_provider_name == "Google Gemini"
