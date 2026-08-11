"""
AI-feature eval CLI — score the framework's AI classifiers against golden data.

The AI features (failure healing/RCA, flaky diagnosis, cluster triage) each
classify inputs into fixed categories. This tool runs them over curated golden
datasets (data/evals/) and reports accuracy, so you can trust the AI and catch
regressions when a prompt, model, or heuristic changes.

    python -m tools.eval                    # score all suites (offline heuristics)
    python -m tools.eval --ai               # score the LLM path (needs a provider)
    python -m tools.eval --suite heal       # one suite only
    python -m tools.eval --json             # machine-readable scorecard
    python -m tools.eval --update-baseline  # record current scores as the baseline
    python -m tools.eval --gate             # exit 1 if accuracy regressed vs baseline (CI)

Offline mode needs no API key and is deterministic — ideal as a CI gate. `--ai`
measures the real prompts/model against the same golden set (prompt-drift guard).
"""

from __future__ import annotations

import argparse
import json
import sys

from config.config import Config
from utils import eval_harness as eh


def _print_scorecard(results: dict) -> None:
    via = next(iter(results.values())).via if results else "offline"
    print(f"\n🧪 AI eval scorecard — via {via}\n")
    overall_total = overall_pass = 0
    for r in results.values():
        overall_total += r.total
        overall_pass += r.passed
        bar = "✅" if r.accuracy == 1.0 else ("🟡" if r.accuracy >= 0.8 else "🔴")
        print(f"  {bar}  {r.suite:<8} {r.passed}/{r.total}  acc={r.accuracy:.0%}")
        # Per-category misses make a drop explainable at a glance.
        for cat, b in sorted(r.by_category.items()):
            if b["correct"] < b["total"]:
                print(f"        · {cat}: {b['correct']}/{b['total']}")
        for f in r.failures:
            print(f"        ✗ [{f.id}] expected '{f.expected}' → got '{f.predicted}'")
    if overall_total:
        print(f"\n  overall: {overall_pass}/{overall_total} "
              f"({overall_pass / overall_total:.0%})\n")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Evaluate the framework's AI features against golden data.")
    p.add_argument("--suite", choices=list(eh.SUITES), default=None,
                   help="only this suite (default: all)")
    p.add_argument("--ai", action="store_true",
                   help="run the LLM path instead of the offline heuristic (needs a provider)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--update-baseline", action="store_true",
                   help="record the current scores as the regression baseline")
    p.add_argument("--gate", action="store_true",
                   help="exit non-zero if accuracy regressed vs the baseline")
    p.add_argument("--threshold", type=float, default=Config.EVAL_REGRESSION_THRESHOLD,
                   help="max allowed accuracy drop before --gate fails (default from config)")
    args = p.parse_args(argv)

    suites = [args.suite] if args.suite else None
    results = eh.run_all(suites, use_llm=args.ai)

    if args.update_baseline:
        path = eh.save_baseline(results)
        if not args.json:
            print(f"✅ Baseline updated → {path}")

    regressions: list = []
    if args.gate:
        baseline = eh.load_baseline()
        if baseline is None:
            print("⚠️  No baseline found — run `python -m tools.eval --update-baseline` first.",
                  file=sys.stderr)
            return 2
        regressions = eh.compare_to_baseline(results, baseline, args.threshold)

    if args.json:
        print(json.dumps({
            "via": "llm" if args.ai else "offline",
            "suites": {s: r.as_dict() for s, r in results.items()},
            "regressions": [r.as_dict() for r in regressions],
        }, indent=2))
    else:
        _print_scorecard(results)
        if args.gate:
            if regressions:
                print("📉 REGRESSIONS vs baseline:")
                for r in regressions:
                    print(f"  [{r.suite}/{r.via}] {r.baseline_accuracy:.0%} → "
                          f"{r.current_accuracy:.0%} (−{r.drop:.0%})")
            else:
                print("✅ No regressions vs baseline.")

    return 1 if regressions else 0


if __name__ == "__main__":
    sys.exit(main())
