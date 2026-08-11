"""
Unit tests for LLM cost/latency observability + guardrails
(utils/llm_observability.py). Pure and offline — no provider or network.
"""

import pytest

from config.config import Config
from llm.policies.metrics import CallRecord
from utils import llm_observability as llmo


# ── Cost estimation ────────────────────────────────────────────────────────────

def test_cost_exact_model():
    # 1M in + 1M out at gpt-4o-mini prices (0.15 / 0.60)
    assert llmo.estimate_cost("openai", "gpt-4o-mini", 1_000_000, 1_000_000) == pytest.approx(0.75)


def test_cost_prefix_match_handles_version_suffix():
    assert llmo.estimate_cost("openai", "gpt-4o-mini-2026-01-01", 1_000_000, 0) == pytest.approx(0.15)


def test_cost_local_provider_is_free():
    assert llmo.estimate_cost("ollama", "llama3.1", 5_000_000, 5_000_000) == 0.0


def test_cost_unknown_model_is_zero_not_crash():
    assert llmo.estimate_cost("mystery", "totally-unknown-model", 1000, 1000) == 0.0


# ── Budget guardrails ──────────────────────────────────────────────────────────

def test_guard_blocks_after_call_ceiling(monkeypatch):
    monkeypatch.setattr(Config, "LLM_MAX_CALLS", 2)
    monkeypatch.setattr(Config, "LLM_OBSERVABILITY", False)   # don't touch the real DB
    llmo.reset_budget()
    llmo._guard("openai", "complete", "test")                 # under budget → allowed
    for _ in range(2):
        llmo._sink(CallRecord(provider="openai", model="gpt-4o-mini",
                              input_tokens=10, output_tokens=10))
    with pytest.raises(llmo.LLMBudgetExceeded):
        llmo._guard("openai", "complete", "test")
    llmo.reset_budget()


def test_guard_blocks_after_cost_ceiling(monkeypatch):
    monkeypatch.setattr(Config, "LLM_MAX_COST_USD", 0.10)
    monkeypatch.setattr(Config, "LLM_OBSERVABILITY", False)
    llmo.reset_budget()
    # one 1M-in gpt-4o-mini call = $0.15 > $0.10 cap
    llmo._sink(CallRecord(provider="openai", model="gpt-4o-mini", input_tokens=1_000_000))
    assert llmo.budget_state()["cost_usd"] == pytest.approx(0.15)
    with pytest.raises(llmo.LLMBudgetExceeded):
        llmo._guard("openai", "complete", "test")
    llmo.reset_budget()


def test_cached_calls_do_not_accrue_budget(monkeypatch):
    monkeypatch.setattr(Config, "LLM_OBSERVABILITY", False)
    llmo.reset_budget()
    llmo._sink(CallRecord(provider="openai", model="gpt-4o-mini",
                         input_tokens=1_000_000, cached=True))
    assert llmo.budget_state()["calls"] == 0
    assert llmo.budget_state()["cost_usd"] == 0.0


def test_no_ceiling_means_unlimited(monkeypatch):
    monkeypatch.setattr(Config, "LLM_MAX_CALLS", 0)
    monkeypatch.setattr(Config, "LLM_MAX_COST_USD", 0)
    monkeypatch.setattr(Config, "LLM_MAX_TOKENS", 0)
    monkeypatch.setattr(Config, "LLM_OBSERVABILITY", False)
    llmo.reset_budget()
    for _ in range(100):
        llmo._sink(CallRecord(provider="openai", model="gpt-4o", input_tokens=1000))
    llmo._guard("openai", "complete", "test")   # never raises
    llmo.reset_budget()


# ── Feature attribution ─────────────────────────────────────────────────────────

def test_infer_feature_skips_infra_returns_caller():
    # Called from this test module → the leaf module name, never 'unknown'.
    feature = llmo._infer_feature()
    assert feature.endswith("test_llm_observability")


# ── Persistent store ────────────────────────────────────────────────────────────

def test_store_records_and_aggregates(tmp_path):
    store = llmo.LLMUsageStore(db_path=tmp_path / "u.db")
    store.record(CallRecord(provider="openai", model="gpt-4o-mini", feature="healer",
                            input_tokens=1000, output_tokens=500, cost_usd=0.01, latency_s=0.5))
    store.record(CallRecord(provider="openai", model="gpt-4o-mini", feature="ai_summary",
                            input_tokens=2000, output_tokens=800, cost_usd=0.02, latency_s=1.5))
    store.record(CallRecord(provider="openai", model="gpt-4o-mini", feature="healer",
                            cached=True, ok=True))

    totals = store.totals()
    assert totals["calls"] == 3 and totals["cached"] == 1
    assert totals["input_tokens"] == 3000 and totals["output_tokens"] == 1300
    assert totals["cost_usd"] == pytest.approx(0.03)

    by_feature = {r["key"]: r for r in store.by("feature")}
    assert by_feature["healer"]["calls"] == 2
    assert by_feature["ai_summary"]["cost_usd"] == pytest.approx(0.02)

    lat = store.latency_percentiles()
    assert lat["count"] == 2          # cached call excluded from latency
    assert lat["max_ms"] == 1500

    assert len(store.recent(10)) == 3


def test_store_by_rejects_bad_dimension(tmp_path):
    store = llmo.LLMUsageStore(db_path=tmp_path / "u.db")
    with pytest.raises(ValueError):
        store.by("not-a-column")


# ── Response cache ──────────────────────────────────────────────────────────────

def test_cache_reuses_identical_text(monkeypatch):
    monkeypatch.setattr(Config, "LLM_CACHE_ENABLED", True)
    monkeypatch.setattr(Config, "LLM_OBSERVABILITY", False)   # memory cache only
    llmo._mem_cache.clear()
    calls = {"n": 0}

    def produce():
        calls["n"] += 1
        return "RESULT"

    v1 = llmo.maybe_cached("text", "openai", "gpt-4o-mini", "same-input", produce)
    v2 = llmo.maybe_cached("text", "openai", "gpt-4o-mini", "same-input", produce)
    assert v1 == v2 == "RESULT"
    assert calls["n"] == 1            # second call served from cache


def test_cache_json_roundtrip(monkeypatch):
    monkeypatch.setattr(Config, "LLM_CACHE_ENABLED", True)
    monkeypatch.setattr(Config, "LLM_OBSERVABILITY", False)
    llmo._mem_cache.clear()
    v1 = llmo.maybe_cached("json", "openai", "m", "k", lambda: {"a": 1})
    v2 = llmo.maybe_cached("json", "openai", "m", "k", lambda: {"a": 999})
    assert v1 == v2 == {"a": 1}


def test_cache_does_not_store_empty(monkeypatch):
    monkeypatch.setattr(Config, "LLM_CACHE_ENABLED", True)
    monkeypatch.setattr(Config, "LLM_OBSERVABILITY", False)
    llmo._mem_cache.clear()
    calls = {"n": 0}

    def empty():
        calls["n"] += 1
        return ""

    llmo.maybe_cached("text", "p", "m", "empty-key", empty)
    llmo.maybe_cached("text", "p", "m", "empty-key", empty)
    assert calls["n"] == 2            # empty results are never cached


def test_cache_disabled_always_produces(monkeypatch):
    monkeypatch.setattr(Config, "LLM_CACHE_ENABLED", False)
    llmo._mem_cache.clear()
    calls = {"n": 0}

    def produce():
        calls["n"] += 1
        return "X"

    llmo.maybe_cached("text", "p", "m", "k", produce)
    llmo.maybe_cached("text", "p", "m", "k", produce)
    assert calls["n"] == 2
