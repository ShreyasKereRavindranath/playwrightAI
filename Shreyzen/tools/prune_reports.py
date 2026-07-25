"""
CLI for report retention / auto-pruning.

Enforce the retention caps on logs_and_reports/ on demand. Safe by default —
use --dry-run first to see what would be dropped.

    python -m tools.prune_reports --dry-run
    python -m tools.prune_reports
    python -m tools.prune_reports --max-runs 20 --max-age-days 14 --max-size-mb 1024

Any flag left off falls back to the Config / .env value. A limit of 0 disables
that particular check.
"""

import argparse
import sys

from utils.retention import prune_reports


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Prune old test run artifacts.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be removed without deleting.")
    parser.add_argument("--max-runs", type=int, default=None,
                        help="Keep at most N most-recent runs per category (0 = unlimited).")
    parser.add_argument("--max-age-days", type=int, default=None,
                        help="Drop runs older than D days (0 = no age limit).")
    parser.add_argument("--max-size-mb", type=int, default=None,
                        help="Total size ceiling in MB; oldest dropped first (0 = no limit).")
    args = parser.parse_args(argv)

    report = prune_reports(
        max_runs=args.max_runs,
        max_age_days=args.max_age_days,
        max_size_mb=args.max_size_mb,
        dry_run=args.dry_run,
        log=print,
    )

    if not report.total_removed:
        print("Nothing to prune — all categories within limits.")
        return 0

    verb = "Would free" if args.dry_run else "Freed"
    print(f"\n{verb} {report.freed_mb} MB across {report.total_removed} run(s).")
    for c in report.categories:
        if c.removed:
            print(f"\n{c.category}: removed {len(c.removed)}, kept {c.kept}")
            for name in c.removed:
                print(f"  - {name}  ({c.reasons.get(name, '')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
