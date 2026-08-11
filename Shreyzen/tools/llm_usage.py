"""
LLM usage CLI — cost, latency, and budget observability for the AI features.

Every LLM call is timed, costed, and persisted to logs_and_reports/llm_usage.db
(LLM_OBSERVABILITY, on by default). This tool aggregates that history.

    python -m tools.llm_usage                 # overall summary + latency + by feature
    python -m tools.llm_usage --by model      # group by provider | model | feature
    python -m tools.llm_usage --recent 20     # the last N calls
    python -m tools.llm_usage --json          # machine-readable

Guardrails (per-process ceilings; 0 = unlimited) are set in config/.env:
    LLM_MAX_COST_USD, LLM_MAX_TOKENS, LLM_MAX_CALLS
When a ceiling is hit, further calls are blocked and each AI feature falls back
to its deterministic offline path.
"""

from __future__ import annotations

import argparse
import json
import sys

from utils.llm_observability import LLMUsageStore


def _fmt_usd(v: float) -> str:
    return f"${v:.4f}" if v < 1 else f"${v:,.2f}"


def _print_summary(store: LLMUsageStore) -> None:
    totals = store.totals()
    if not totals.get("calls"):
        print("No LLM calls recorded yet. (Run an AI feature, or check LLM_OBSERVABILITY.)")
        return
    lat = store.latency_percentiles()
    print("\n🤖 LLM usage summary\n")
    print(f"  calls          {totals['calls']}  "
          f"({totals['cached']} cached, {totals['failures']} failed)")
    print(f"  tokens         {totals['input_tokens']:,} in / {totals['output_tokens']:,} out")
    print(f"  est. cost      {_fmt_usd(totals['cost_usd'])}")
    if lat:
        print(f"  latency        p50 {lat['p50_ms']}ms · p95 {lat['p95_ms']}ms · "
              f"max {lat['max_ms']}ms")
    if totals["calls"]:
        hit_rate = totals["cached"] / totals["calls"] * 100
        print(f"  cache hit-rate {hit_rate:.0f}%")

    print("\n  by feature:")
    for row in store.by("feature"):
        print(f"    {row['key']:<20} {row['calls']:>4} calls  "
              f"{_fmt_usd(row['cost_usd']):>10}  avg {row['avg_latency_ms']:.0f}ms")
    print()


def _print_group(store: LLMUsageStore, dimension: str) -> None:
    rows = store.by(dimension)
    if not rows:
        print("No LLM calls recorded yet.")
        return
    print(f"\n🤖 LLM usage by {dimension}\n")
    for row in rows:
        print(f"  {row['key']:<28} {row['calls']:>4} calls  "
              f"{_fmt_usd(row['cost_usd']):>10}  "
              f"{row['input_tokens']:,}/{row['output_tokens']:,} tok  "
              f"avg {row['avg_latency_ms']:.0f}ms")
    print()


def _print_recent(store: LLMUsageStore, limit: int) -> None:
    rows = store.recent(limit)
    if not rows:
        print("No LLM calls recorded yet.")
        return
    print(f"\n🤖 Last {len(rows)} LLM call(s)\n")
    for r in rows:
        flag = "cache" if r["cached"] else ("ok" if r["ok"] else "FAIL")
        print(f"  {r['ts']}  [{flag:<5}] {r['feature']:<16} {r['model']:<22} "
              f"{r['input_tokens']}/{r['output_tokens']}tok  "
              f"{_fmt_usd(r['cost_usd'])}  {r['latency_ms']}ms")
    print()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Report LLM cost/latency/usage.")
    p.add_argument("--by", choices=["provider", "model", "feature"], default=None,
                   help="group usage by this dimension")
    p.add_argument("--recent", type=int, default=None, metavar="N",
                   help="show the last N calls")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    args = p.parse_args(argv)

    store = LLMUsageStore()

    if args.json:
        print(json.dumps({
            "totals": store.totals(),
            "latency": store.latency_percentiles(),
            "by_feature": store.by("feature"),
            "by_model": store.by("model"),
            "by_provider": store.by("provider"),
            "recent": store.recent(args.recent or 20),
        }, indent=2))
        return 0

    if args.recent is not None:
        _print_recent(store, args.recent)
    elif args.by:
        _print_group(store, args.by)
    else:
        _print_summary(store)
    return 0


if __name__ == "__main__":
    sys.exit(main())
