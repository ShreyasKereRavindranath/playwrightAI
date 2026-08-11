"""
Unit tests for the AI-feature eval harness (utils/eval_harness.py).

Covers the scoring math, the real golden-dataset suites (offline path, so no
LLM/network), baseline round-trip, and the same-via regression gate. Uses the
shipped data/evals/*.json datasets — these tests also guard those goldens
against accidental edits that would break the CI gate.
"""

import json

import pytest

from utils import eval_harness as eh


# ── Scoring math ──────────────────────────────────────────────────────────────

def test_score_computes_accuracy_and_breakdown():
    results = [
        eh.CaseResult(id="1", expected="a", predicted="a", passed=True),
        eh.CaseResult(id="2", expected="a", predicted="b", passed=False),
        eh.CaseResult(id="3", expected="b", predicted="b", passed=True),
    ]
    sr = eh._score("demo", "offline", results)
    assert sr.total == 3 and sr.passed == 2
    assert sr.accuracy == pytest.approx(0.6667, abs=1e-3)
    assert sr.by_category["a"] == {"total": 2, "correct": 1}
    assert sr.by_category["b"] == {"total": 1, "correct": 1}
    assert [f.id for f in sr.failures] == ["2"]           # only misses recorded


def test_score_empty_is_zero_not_crash():
    sr = eh._score("demo", "offline", [])
    assert sr.total == 0 and sr.accuracy == 0.0


# ── Real suites (offline, deterministic) ───────────────────────────────────────

@pytest.mark.parametrize("suite", eh.SUITES)
def test_golden_suites_pass_offline(suite):
    """Every shipped golden case is classified correctly by the offline heuristic.

    This is the bar the CI gate protects; if a heuristic change breaks a case,
    this test (and the gate) go red.
    """
    sr = eh.run_suite(suite, use_llm=False)
    assert sr.total > 0
    assert 0.0 <= sr.accuracy <= 1.0
    assert sr.accuracy == 1.0, (
        f"{suite}: misses → "
        + "; ".join(f"{f.id}: expected {f.expected}, got {f.predicted}" for f in sr.failures)
    )


def test_run_all_covers_every_suite():
    results = eh.run_all(use_llm=False)
    assert set(results) == set(eh.SUITES)


def test_unknown_suite_raises():
    with pytest.raises(ValueError):
        eh.run_suite("does-not-exist")


# ── Baseline round-trip + regression gate ───────────────────────────────────────

def test_baseline_save_load_roundtrip(tmp_path):
    results = eh.run_all(use_llm=False)
    path = tmp_path / "baseline.json"
    eh.save_baseline(results, path=path)
    loaded = eh.load_baseline(path=path)
    assert "offline" in loaded
    assert set(loaded["offline"]) == set(eh.SUITES)
    # merge semantics: writing an llm scorecard keeps the offline one.
    for r in results.values():
        r.via = "llm"
    eh.save_baseline(results, path=path)
    merged = json.loads(path.read_text())
    assert "offline" in merged and "llm" in merged


def test_gate_flags_drop_beyond_threshold():
    results = eh.run_all(["heal"], use_llm=False)
    baseline = {"offline": {"heal": {"accuracy": 1.0}}}
    results["heal"].accuracy = 0.80          # a 20pt drop
    regs = eh.compare_to_baseline(results, baseline, threshold=0.05)
    assert len(regs) == 1 and regs[0].suite == "heal"
    assert regs[0].drop == pytest.approx(0.20, abs=1e-6)


def test_gate_ignores_drop_within_threshold():
    results = eh.run_all(["heal"], use_llm=False)
    baseline = {"offline": {"heal": {"accuracy": 1.0}}}
    results["heal"].accuracy = 0.97          # 3pt drop, under the 5pt threshold
    assert eh.compare_to_baseline(results, baseline, threshold=0.05) == []


def test_gate_only_compares_same_via():
    """An offline run must never be gated against an llm baseline (or vice-versa)."""
    results = eh.run_all(["heal"], use_llm=False)   # via == "offline"
    results["heal"].accuracy = 0.0
    baseline = {"llm": {"heal": {"accuracy": 1.0}}}  # only an llm baseline exists
    assert eh.compare_to_baseline(results, baseline, threshold=0.05) == []


def test_missing_baseline_entry_is_skipped():
    results = eh.run_all(["heal"], use_llm=False)
    results["heal"].accuracy = 0.0
    assert eh.compare_to_baseline(results, {"offline": {}}, threshold=0.05) == []
