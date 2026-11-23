"""Claude client: retries, validation, status reporting, privacy-safe logging (all mocked)."""
import json
import logging

import anthropic
import httpx
import pytest
from pydantic import BaseModel

from ai.claude_client import AIResponseError, AIUnavailable, ClaudeClient, ai_status, build_claude_client
from config.settings import get_settings
from tests.fixtures.fake_ai import FakeAnthropic, make_client


class Out(BaseModel):
    value: int


REQ = httpx.Request("POST", "https://api.anthropic.test/v1/messages")


def status_error(cls, code):
    return cls("boom", response=httpx.Response(code, request=REQ), body=None)


def test_ai_status_reasons():
    s = get_settings()
    assert ai_status(s.model_copy(update={"enable_ai_matching": False}))["state"] == "disabled"
    assert ai_status(s.model_copy(update={"anthropic_api_key": ""}))["state"] == "no_api_key"
    assert ai_status(s.model_copy(update={"anthropic_api_key": "k", "ai_consent_external_processing": False}))["state"] == "no_consent"
    st = ai_status(s.model_copy(update={"anthropic_api_key": "k", "ai_consent_external_processing": True}))
    assert st["enabled"] and st["model"] == s.claude_model


def test_client_not_built_without_key_or_consent():
    s = get_settings()
    assert build_claude_client(s.model_copy(update={"anthropic_api_key": ""})) is None
    assert build_claude_client(s.model_copy(update={"anthropic_api_key": "k", "ai_consent_external_processing": False})) is None


def test_success_is_validated():
    ai = make_client(['Sure! {"value": 7}'])
    assert ai.complete_json("sys", "user", Out).value == 7
    call = ai._client.messages.calls[0]
    assert call["model"] == get_settings().claude_model and call["max_tokens"] == get_settings().claude_max_tokens
    assert "JSON schema" in call["messages"][0]["content"]


def test_malformed_then_valid_retries_once():
    ai = make_client(["no json", '{"value": 1}'])
    assert ai.complete_json("s", "u", Out).value == 1
    assert len(ai._client.messages.calls) == 2


def test_malformed_twice_raises():
    ai = make_client(["nope", '{"value": "abc"}'])
    with pytest.raises(AIResponseError):
        ai.complete_json("s", "u", Out)


def test_transient_errors_are_retried_with_backoff():
    sleeps = []
    ai = make_client([status_error(anthropic.RateLimitError, 429), anthropic.APIConnectionError(request=REQ), '{"value": 3}'])
    ai._sleep = sleeps.append
    assert ai.complete_json("s", "u", Out).value == 3
    assert sleeps == [1, 2]


def test_exhausted_retries_raise_unavailable():
    ai = make_client([status_error(anthropic.InternalServerError, 500)])  # script repeats the last item forever
    with pytest.raises(AIUnavailable):
        ai.complete_json("s", "u", Out)
    assert len(ai._client.messages.calls) == get_settings().model_copy(update={"claude_max_retries": 2}).claude_max_retries + 1


def test_client_errors_are_not_retried():
    ai = make_client([status_error(anthropic.AuthenticationError, 401)])
    with pytest.raises(AIUnavailable) as e:
        ai.complete_json("s", "u", Out)
    assert "401" in str(e.value) and len(ai._client.messages.calls) == 1


def test_overloaded_529_is_retried():
    ai = make_client([anthropic.APIStatusError("overloaded", response=httpx.Response(529, request=REQ), body=None), '{"value": 2}'])
    assert ai.complete_json("s", "u", Out).value == 2


def test_logs_contain_metadata_only(caplog):
    secret_prompt = "PRIVATE-RESUME-TEXT-12345 jane@example.com"
    ai = make_client(["garbage", '{"value": 1}'])
    with caplog.at_level(logging.DEBUG):
        ai.complete_json("system", secret_prompt, Out)
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "Claude call ok" in logged and "in_tokens=11" in logged
    assert "PRIVATE-RESUME-TEXT" not in logged and "jane@example.com" not in logged and "test-key" not in logged
