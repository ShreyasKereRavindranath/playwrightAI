"""
Unit tests for the provider-neutral LLM abstraction (llm/*).

These are pure unit tests — no browser, no network, no provider SDKs. A fake
in-memory provider is registered and driven end-to-end through LLMService, so we
exercise the registry, factory, capability negotiation, JSON parsing, retry
policy, and the backward-compat shim without touching a real provider.
"""

import os

import pytest

from llm import registry
from llm.config import ProviderConfig
from llm.core.capabilities import Capability, ProviderCapabilities
from llm.core.errors import RetryableError
from llm.core.interfaces import LLMProvider
from llm.core.metadata import Availability, HealthResult, ProviderMetadata, ValidationResult
from llm.core.models import LLMRequest, LLMResponse, Message, Usage
from llm.policies.retry import with_retry
from llm.service import LLMService, _parse_json

pytestmark = pytest.mark.unit


# ── A fake provider that records what it received ────────────────────────────

@registry.register_provider("fake")
class _FakeProvider(LLMProvider):
    last_request: LLMRequest = None  # type: ignore
    reply: str = '{"ok": true}'

    def __init__(self, config: ProviderConfig):
        self._cfg = config

    def metadata(self):
        return ProviderMetadata("fake", "Fake", "cloud", requires_api_key=False,
                                default_model="fake-1")

    def capabilities(self):
        # No TEMPERATURE, no VISION → negotiation must strip those.
        return ProviderCapabilities.of(
            Capability.CHAT, Capability.SYSTEM_PROMPT, Capability.JSON_MODE, Capability.MAX_TOKENS)

    def validate_config(self):
        return ValidationResult(True, "fake ok")

    def health_check(self):
        return HealthResult(Availability.AVAILABLE, "fake")

    def complete(self, request: LLMRequest) -> LLMResponse:
        _FakeProvider.last_request = request
        return LLMResponse(text=_FakeProvider.reply, model="fake-1", provider="fake",
                           usage=Usage(3, 5))


@pytest.fixture
def fake_service(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "fake")
    _FakeProvider.last_request = None
    _FakeProvider.reply = '{"ok": true}'
    return LLMService()


# ── Registry / discovery ─────────────────────────────────────────────────────

def test_all_real_providers_registered():
    names = set(registry.registered())
    assert {"openai", "anthropic", "gemini", "ollama", "lmstudio", "openai_compatible"} <= names


# ── Capability negotiation ───────────────────────────────────────────────────

def test_temperature_stripped_for_provider_without_capability(fake_service):
    fake_service.complete_text("hi", temperature=0.9)
    assert _FakeProvider.last_request.temperature is None  # stripped


def test_images_stripped_without_vision(fake_service):
    req = LLMRequest(messages=[Message("user", "look", images=["data:image/png;base64,xxx"])])
    fake_service.complete(req)
    assert _FakeProvider.last_request.messages[0].images == []


def test_temperature_preserved_when_supported():
    # OpenAI advertises TEMPERATURE — negotiation must keep it.
    from llm.providers.openai_provider import OpenAIProvider
    svc = LLMService()
    negotiated = svc._negotiate(OpenAIProvider(ProviderConfig("openai", api_key="k")),
                                LLMRequest.simple("hi", temperature=0.7))
    assert negotiated.temperature == 0.7


# ── JSON handling ────────────────────────────────────────────────────────────

def test_complete_json_parses_object(fake_service):
    _FakeProvider.reply = '{"a": 1, "b": [2, 3]}'
    assert fake_service.complete_json("x") == {"a": 1, "b": [2, 3]}


def test_complete_json_extracts_from_noise(fake_service):
    _FakeProvider.reply = "Here you go:\n```json\n{\"a\": 1}\n```"
    assert fake_service.complete_json("x") == {"a": 1}


@pytest.mark.parametrize("raw,expected", [
    ('{"x":1}', {"x": 1}),
    ("garbage", {}),
    ("", {}),
    ('prefix {"y": 2} suffix', {"y": 2}),
])
def test_parse_json_helper(raw, expected):
    assert _parse_json(raw) == expected


# ── Retry policy ─────────────────────────────────────────────────────────────

def test_retry_recovers_after_transient(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RetryableError("boom")
        return "ok"

    assert with_retry(flaky, max_retries=3) == "ok"
    assert calls["n"] == 3


def test_retry_gives_up_and_raises(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)

    def always():
        raise RetryableError("nope")

    with pytest.raises(RetryableError):
        with_retry(always, max_retries=2)


# ── Selection is remembered ──────────────────────────────────────────────────

def test_select_provider_persists(monkeypatch, tmp_path):
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.setattr("llm.config._SELECTION_FILE", tmp_path / "sel.json")
    svc = LLMService()
    svc.select_provider("fake")
    assert svc.current_provider_name() == "fake"


# ── Backward-compat shim ─────────────────────────────────────────────────────

def test_legacy_shim_delegates(fake_service, monkeypatch):
    from utils.llm_client import LLMClient
    client = LLMClient(service=fake_service)
    assert client.available is True
    _FakeProvider.reply = "plain text"
    assert client.complete("hi") == "plain text"
    _FakeProvider.reply = '{"k": "v"}'
    assert client.complete_json("hi") == {"k": "v"}
