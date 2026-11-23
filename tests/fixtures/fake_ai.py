"""A scripted stand-in for the Anthropic SDK client - tests never touch the network."""
from types import SimpleNamespace

from ai.claude_client import ClaudeClient
from config.settings import get_settings


class FakeMessages:
    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=item)],
            usage=SimpleNamespace(input_tokens=11, output_tokens=7),
        )


class FakeAnthropic:
    def __init__(self, script):
        self.messages = FakeMessages(script)


def make_client(script, **settings_overrides) -> ClaudeClient:
    settings = get_settings().model_copy(update={
        "anthropic_api_key": "test-key", "ai_consent_external_processing": True, "enable_ai_matching": True,
        "claude_max_retries": 2, **settings_overrides})
    client = ClaudeClient(settings, client=FakeAnthropic(script), sleep=lambda s: None)
    return client
