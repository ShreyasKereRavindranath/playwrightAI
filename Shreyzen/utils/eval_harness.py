"""
AI-feature evaluation harness.

The framework ships several AI features (failure healing/RCA, flaky-test
diagnosis, failure-cluster triage) that each classify an input into a fixed set
of categories. This harness measures whether those classifiers are *correct*
against hand-curated golden datasets, and — crucially — lets you detect
**regressions** when a prompt, model, or heuristic changes.

Why it matters: every other AI feature is only as trustworthy as our ability to
say "it still gets the known cases right." This is that ability.

Two evaluation modes per suite, mirroring the rest of the framework:
  • offline  — force the deterministic heuristic (no LLM). Always runnable, so it
               protects heuristic refactors and runs in CI with zero secrets.
  • llm      — run the real LLM path (needs a configured provider). Measures the
               prompt/model against the same golden set, catching prompt drift.

Suites (each backed by data/evals/<suite>_cases.json):
  • heal    → agents.healer.Healer.diagnose        (failure category)
  • flaky   → utils.flaky_analysis.diagnose         (flake root-cause category)
  • triage  → utils.failure_cluster.triage          (cluster root-cause category)

Scoring is classification accuracy + a per-category breakdown + the list of
misses (expected vs predicted), so a drop is always explainable. Consumed by
`tools/eval.py` (CLI + CI gate) and unit-tested in tests/unit/test_eval_harness.py.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

EVAL_DATA_DIR = Path("data/evals")
BASELINE_PATH = EVAL_DATA_DIR / "baseline.json"

SUITES = ("heal", "flaky", "triage")


# ── Result types ────────────────────────────────────────────────────────────

@dataclass
class CaseResult:
    id: str
    expected: str
    predicted: str
    passed: bool
    detail: str = ""


@dataclass
class SuiteResult:
    suite: str
    via: str            # "offline" | "llm"
    total: int
    passed: int
    accuracy: float
    by_category: dict = field(default_factory=dict)   # expected → {total, correct}
    failures: list = field(default_factory=list)      # list[CaseResult] (misses only)

    def as_dict(self, include_failures: bool = True) -> dict:
        d = {
            "suite": self.suite,
            "via": self.via,
            "total": self.total,
            "passed": self.passed,
            "accuracy": self.accuracy,
            "by_category": self.by_category,
        }
        if include_failures:
            d["failures"] = [asdict(f) for f in self.failures]
        return d


# ── A tiny "no LLM" stub used to force the offline path deterministically ─────

class _NoLLM:
    available = False

    def complete_json(self, *a, **k):  # pragma: no cover - never called (available=False)
        return {}

    def complete(self, *a, **k):  # pragma: no cover
        return ""


def _llm_arg(use_llm: bool):
    """Return the `llm` argument for the diagnosers: None → real client, else stub."""
    return None if use_llm else _NoLLM()


# ── Dataset loading ───────────────────────────────────────────────────────────

def load_cases(suite: str, data_dir: Path = EVAL_DATA_DIR) -> list:
    path = data_dir / f"{suite}_cases.json"
    if not path.exists():
        raise FileNotFoundError(f"No golden dataset for suite '{suite}': {path}")
    return json.loads(path.read_text(encoding="utf-8"))


# ── Per-suite prediction ──────────────────────────────────────────────────────

def _predict_heal(case: dict, use_llm: bool) -> str:
    # The Healer's *category* is a deterministic classification of the error
    # text; the LLM only influences the fix diff, so use_llm doesn't change the
    # label here — we still run through the real code path for fidelity.
    from agents.healer import Healer
    healer = Healer(offline=not use_llm)
    dx = healer.diagnose(case["error_text"], test_id=case.get("id", "?"))
    return dx.category


def _predict_flaky(case: dict, use_llm: bool) -> str:
    from utils import flaky_analysis
    result = flaky_analysis.diagnose(
        case.get("id", "?"),
        case.get("history", []),
        failure_text=case.get("failure_text", ""),
        llm=_llm_arg(use_llm),
    )
    return str(result.get("category", "unknown"))


def _predict_triage(case: dict, use_llm: bool) -> str:
    from utils import failure_cluster
    result = failure_cluster.triage(case["cluster"], llm=_llm_arg(use_llm))
    return str(result.get("category", "unknown"))


_PREDICTORS: dict[str, Callable[[dict, bool], str]] = {
    "heal": _predict_heal,
    "flaky": _predict_flaky,
    "triage": _predict_triage,
}


# ── Scoring ────────────────────────────────────────────────────────────────────

def _score(suite: str, via: str, results: list[CaseResult]) -> SuiteResult:
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    by_category: dict = {}
    for r in results:
        bucket = by_category.setdefault(r.expected, {"total": 0, "correct": 0})
        bucket["total"] += 1
        bucket["correct"] += int(r.passed)
    accuracy = round(passed / total, 4) if total else 0.0
    failures = [r for r in results if not r.passed]
    return SuiteResult(suite, via, total, passed, accuracy, by_category, failures)


def run_suite(suite: str, *, use_llm: bool = False,
              data_dir: Path = EVAL_DATA_DIR) -> SuiteResult:
    if suite not in _PREDICTORS:
        raise ValueError(f"Unknown eval suite '{suite}'. Known: {', '.join(SUITES)}")
    predict = _PREDICTORS[suite]
    cases = load_cases(suite, data_dir)
    via = "llm" if use_llm else "offline"
    results: list[CaseResult] = []
    for case in cases:
        expected = str(case["expected_category"])
        try:
            predicted = predict(case, use_llm)
        except Exception as exc:  # a broken predictor is a (loud) miss, not a crash
            predicted = f"<error: {exc}>"
        results.append(CaseResult(
            id=str(case.get("id", "?")),
            expected=expected,
            predicted=predicted,
            passed=(predicted == expected),
        ))
    return _score(suite, via, results)


def run_all(suites: Optional[list] = None, *, use_llm: bool = False,
            data_dir: Path = EVAL_DATA_DIR) -> dict[str, SuiteResult]:
    return {s: run_suite(s, use_llm=use_llm, data_dir=data_dir)
            for s in (suites or list(SUITES))}


# ── Baseline + regression gate ─────────────────────────────────────────────────

def to_baseline(results: dict[str, SuiteResult]) -> dict:
    """Compact scorecard (no failure detail) keyed by via → suite → metrics."""
    scorecard: dict = {}
    for r in results.values():
        scorecard.setdefault(r.via, {})[r.suite] = {
            "accuracy": r.accuracy, "total": r.total, "passed": r.passed,
            "by_category": r.by_category,
        }
    return scorecard


def save_baseline(results: dict[str, SuiteResult], path: Path = BASELINE_PATH) -> Path:
    """Merge into any existing baseline so offline/llm scorecards coexist."""
    existing: dict = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    for via, suites in to_baseline(results).items():
        existing.setdefault(via, {}).update(suites)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    return path


def load_baseline(path: Path = BASELINE_PATH) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


@dataclass
class Regression:
    suite: str
    via: str
    baseline_accuracy: float
    current_accuracy: float
    drop: float

    def as_dict(self) -> dict:
        return asdict(self)


def compare_to_baseline(results: dict[str, SuiteResult], baseline: dict,
                        threshold: float) -> list[Regression]:
    """Flag suites whose accuracy dropped more than `threshold` vs the baseline.

    Only compares against a baseline entry recorded for the *same* via (offline
    vs llm), so an offline run is never gated against an llm baseline. A suite
    with no baseline entry is skipped (nothing to regress against).
    """
    regressions: list[Regression] = []
    for r in results.values():
        base = (baseline.get(r.via) or {}).get(r.suite)
        if not base:
            continue
        base_acc = float(base.get("accuracy", 0.0))
        drop = round(base_acc - r.accuracy, 4)
        if drop > threshold:
            regressions.append(Regression(
                suite=r.suite, via=r.via,
                baseline_accuracy=base_acc, current_accuracy=r.accuracy, drop=drop,
            ))
    return regressions
