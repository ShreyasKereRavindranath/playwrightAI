"""
Unit tests for LLM model routing & fallback (llm/service.py).

Registers fake providers and drives LLMService end-to-end with routing on/off.
No network, no real provider SDKs.
"""

import pytest

from config.config import Config
from llm import registry
from llm.config import ProviderConfig
from llm.core.capabilities import Capability, ProviderCapabilities
from llm.core.interfaces import LLMProvider
from llm.core.metadata import Availability, HealthResult, ProviderMetadata, ValidationResult
from llm.core.models import LLMResponse, Usage
from llm.policies.metrics import metrics
from llm.service import LLMService
from utils.llm_observability import LLMBudgetExceeded

pytestmark = pytest.mark.unit


def _md(name, default_model):
    return ProviderMetadata(name, name, "cloud", requires_api_key=False, default_model=default_model)


def _caps():
    return ProviderCapabilities.of(Capability.CHAT, Capability.SYSTEM_PROMPT,
                                   Capability.MAX_TOKENS, Capability.JSON_MODE)


@registry.register_provider("rp_esc")
class _EscProvider(LLMProvider):
    """Cheap model returns empty (weak); the strong model returns real output."""

    def __init__(self, config: ProviderConfig): self._cfg = config
    def metadata(self): return _md("rp_esc", "cheap")
    def capabilities(self): return _caps()
    def validate_config(self): return ValidationResult(True, "ok")
    def health_check(self): return HealthResult(Availability.AVAILABLE, "ok")

    def complete(self, request):
        model = request.model or "cheap"
        text = "STRONG-OK" if model == "strong" else ""     # cheap → weak/empty
        return LLMResponse(text=text, model=model, provider="rp_esc", usage=Usage(2, 2))


@registry.register_provider("rp_boom")
class _BoomProvider(LLMProvider):
    def __init__(self, config: ProviderConfig): self._cfg = config
    def metadata(self): return _md("rp_boom", "b1")
    def capabilities(self): return _caps()
    def validate_config(self): return ValidationResult(True, "ok")
    def health_check(self): return HealthResult(Availability.AVAILABLE, "ok")
    def complete(self, request): raise RuntimeError("provider exploded")


@registry.register_provider("rp_ok")
class _OkProvider(LLMProvider):
    def __init__(self, config: ProviderConfig): self._cfg = config
    def metadata(self): return _md("rp_ok", "o1")
    def capabilities(self): return _caps()
    def validate_config(self): return ValidationResult(True, "ok")
    def health_check(self): return HealthResult(Availability.AVAILABLE, "ok")

    def complete(self, request):
        return LLMResponse(text="OK", model=request.model or "o1", provider="rp_ok", usage=Usage(1, 1))


@pytest.fixture
def capture(monkeypatch):
    """Isolate the metrics hooks and capture every emitted CallRecord."""
    from utils import llm_observability as llmo
    metrics.clear_hooks()
    records: list = []
    metrics.add_sink(records.append)
    monkeypatch.setattr(Config, "LLM_OBSERVABILITY", False)
    yield records
    metrics.clear_hooks()
    llmo._installed = False
    llmo.install()


def _service(monkeypatch, provider: str):
    monkeypatch.setenv("AI_PROVIDER", provider)
    return LLMService()


# ── Routing OFF (default) preserves legacy single-call behavior ─────────────────

def test_routing_disabled_uses_selected_provider(monkeypatch, capture):
    monkeypatch.setattr(Config, "LLM_ROUTING_ENABLED", False)
    svc = _service(monkeypatch, "rp_ok")
    assert svc.complete_text("hi", model="o1") == "OK"
    assert len(capture) == 1                       # exactly one call, no fan-out


# ── Escalation: cheap → strong on weak output ───────────────────────────────────

def test_routing_escalates_on_weak_output(monkeypatch, capture):
    monkeypatch.setattr(Config, "LLM_ROUTING_ENABLED", True)
    monkeypatch.setattr(LLMService, "_ESCALATION", {"rp_esc": ["cheap", "strong"]})
    monkeypatch.setattr(LLMService, "_FAILOVER", [])
    svc = _service(monkeypatch, "rp_esc")
    assert svc.complete_text("hi") == "STRONG-OK"
    # Both attempts were made and recorded (cheap empty, then strong).
    assert [r.model for r in capture] == ["cheap", "strong"]


def test_routing_stops_at_first_good_result(monkeypatch, capture):
    monkeypatch.setattr(Config, "LLM_ROUTING_ENABLED", True)
    # If the cheapest already succeeds, we never escalate.
    monkeypatch.setattr(LLMService, "_ESCALATION", {"rp_ok": ["o1", "o1-strong"]})
    monkeypatch.setattr(LLMService, "_FAILOVER", [])
    svc = _service(monkeypatch, "rp_ok")
    assert svc.complete_text("hi") == "OK"
    assert len(capture) == 1


# ── Failover: provider error → next configured provider ─────────────────────────

def test_routing_fails_over_to_next_provider(monkeypatch, capture):
    monkeypatch.setattr(Config, "LLM_ROUTING_ENABLED", True)
    monkeypatch.setattr(LLMService, "_ESCALATION", {})       # no ladders → single shot each
    monkeypatch.setattr(LLMService, "_FAILOVER", ["rp_ok"])
    svc = _service(monkeypatch, "rp_boom")                   # selected provider always errors
    assert svc.complete_text("hi") == "OK"                   # recovered via rp_ok
    # rp_boom raised (not recorded as a returned value); rp_ok succeeded.
    assert any(r.provider == "rp_ok" for r in capture)


def test_routing_returns_empty_when_all_fail(monkeypatch, capture):
    monkeypatch.setattr(Config, "LLM_ROUTING_ENABLED", True)
    monkeypatch.setattr(LLMService, "_ESCALATION", {})
    monkeypatch.setattr(LLMService, "_FAILOVER", [])          # nowhere to fail over
    svc = _service(monkeypatch, "rp_boom")
    assert svc.complete_text("hi") == ""                     # graceful empty, no raise


# ── Budget guard aborts the whole chain (no failover) ───────────────────────────

def test_budget_guard_stops_routing_chain(monkeypatch, capture):
    monkeypatch.setattr(Config, "LLM_ROUTING_ENABLED", True)
    monkeypatch.setattr(LLMService, "_FAILOVER", ["rp_ok"])

    def deny(provider, operation, feature):
        raise LLMBudgetExceeded("cap reached")

    metrics.add_guard(deny)
    svc = _service(monkeypatch, "rp_boom")
    # The budget error must propagate (LLMClient turns it into offline fallback),
    # NOT be treated as a provider error that triggers failover.
    with pytest.raises(LLMBudgetExceeded):
        svc.complete_text("hi")
    assert capture == []                                     # nothing ran
