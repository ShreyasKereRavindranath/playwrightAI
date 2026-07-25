"""
Central results DB CLI.

Every functional/load run is now persisted to a `run_summaries` table in
logs_and_reports/flakiness.db (alongside flakiness + perf). This CLI inspects
that history and backfills it from existing JSON files.

    python -m tools.results_db --stats                 # aggregate view
    python -m tools.results_db --list                  # recent runs (all kinds)
    python -m tools.results_db --list --kind load      # only load runs
    python -m tools.results_db --get <run_id>          # full stored summary
    python -m tools.results_db --backfill              # import summaries from disk
"""

import argparse
import json
import sys

from utils import results_db as db


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Inspect the central results DB.")
    parser.add_argument("--stats", action="store_true", help="Show aggregate stats.")
    parser.add_argument("--list", action="store_true", help="List recent runs.")
    parser.add_argument("--kind", choices=["functional", "load"], help="Filter by kind.")
    parser.add_argument("--limit", type=int, default=30, help="Max rows for --list.")
    parser.add_argument("--get", metavar="RUN_ID", help="Show one run's full summary.")
    parser.add_argument("--backfill", action="store_true",
                        help="Import run summaries from JSON files on disk.")
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    args = parser.parse_args(argv)

    if args.backfill:
        imported = db.backfill_from_files()
        print(f"Backfilled: {imported['functional']} functional, {imported['load']} load.")

    if args.get:
        run = db.get_run(args.get)
        if not run:
            print(f"No run found: {args.get}")
            return 1
        print(json.dumps(run, indent=2))
        return 0

    if args.stats or (not args.list and not args.backfill):
        s = db.stats()
        if args.json:
            print(json.dumps(s, indent=2))
        else:
            print(f"Total runs : {s['total_runs']}")
            print(f"By kind    : {s['by_kind']}")
            print(f"Pass rate  : {s['pass_rate']}%")

    if args.list:
        rows = db.list_runs(kind=args.kind, limit=args.limit)
        if args.json:
            print(json.dumps(rows, indent=2))
        else:
            print(f"\n{len(rows)} run(s):")
            for r in rows:
                verdict = "PASS" if r["passed"] else "FAIL"
                extra = f"{r['scenario']}/{r['profile']}" if r["kind"] == "load" else r["scenario"]
                print(f"  [{verdict}] {r['run_id']:<40} {r['kind']:<11} "
                      f"total={r['total']:<6} {r['duration_s']}s  {extra}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
