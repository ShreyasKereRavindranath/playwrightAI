"""
Failure triage CLI — cluster recurring test failures and label their root cause.

Failures are captured to logs_and_reports/flakiness.db during test runs
(FAILURE_TRACKING). This tool groups similar failures into clusters, ranks them
by frequency, and labels each: product_bug | test_bug | flaky | environment
(AI when a provider is configured, deterministic heuristic otherwise).

    python -m tools.triage                 # ranked clusters + heuristic labels
    python -m tools.triage --ai            # use the LLM to label each cluster
    python -m tools.triage --top 5         # only the N largest clusters
    python -m tools.triage --limit 500     # consider the last N failures
    python -m tools.triage --json
"""

from __future__ import annotations

import argparse
import json
import sys

from utils import failure_cluster
from utils.failure_store import FailureStore

_ICON = {"product_bug": "🐞", "test_bug": "🔧", "flaky": "🎲",
         "environment": "🌐", "unknown": "❓"}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Cluster and triage recurring test failures.")
    p.add_argument("--ai", action="store_true", help="use the LLM to label clusters")
    p.add_argument("--top", type=int, default=None, help="only the N largest clusters")
    p.add_argument("--limit", type=int, default=200, help="consider the last N failures")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    args = p.parse_args(argv)

    failures = FailureStore().recent(limit=args.limit)
    if not failures:
        print("No failures recorded. ✅" if not args.json else "[]")
        return 0

    # Only pass an LLM when --ai; otherwise force the deterministic heuristic.
    llm = None
    if not args.ai:
        class _NoLLM:
            available = False
        llm = _NoLLM()

    clusters = failure_cluster.cluster_and_triage(failures, llm=llm, top=args.top)

    if args.json:
        print(json.dumps(clusters, indent=2))
        return 0

    print(f"\n🩺 Failure triage — {len(clusters)} cluster(s) from "
          f"{len(failures)} recent failure(s):\n")
    for c in clusters:
        t = c["triage"]
        icon = _ICON.get(t["category"], "•")
        print(f"  {icon}  [{t['category']}] ×{c['count']}  "
              f"({len(c['tests'])} test(s), {len(c['runs'])} run(s), via {t['via']})")
        print(f"       signature: {c['signature'][:100]}")
        print(f"       why: {t['explanation']}")
        if t.get("suggested_action"):
            print(f"       action: {t['suggested_action']}")
        for tid in c["tests"][:3]:
            print(f"         · {tid}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
