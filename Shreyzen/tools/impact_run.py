"""
Test impact analysis CLI — run only the tests a change can break.

Resolves the real Python import graph (not a keyword map) to find which test
files transitively depend on your changed modules.

    python -m tools.impact_run                     # analyse vs HEAD, print plan
    python -m tools.impact_run --base main         # diff vs main
    python -m tools.impact_run --list              # print impacted test files only
    python -m tools.impact_run --run               # actually run pytest on them
    python -m tools.impact_run --run --base origin/main

Exit codes: 0 on success (or nothing to run), pytest's code when --run is used.
"""

import argparse
import subprocess
import sys

from utils.test_impact import analyze_impact


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run only tests impacted by a change.")
    parser.add_argument("--base", default="HEAD",
                        help="Base git ref to diff against (default: HEAD = working tree).")
    parser.add_argument("--list", action="store_true",
                        help="Print impacted test files (or ALL) and exit.")
    parser.add_argument("--run", action="store_true",
                        help="Run pytest on the impacted tests.")
    args, extra = parser.parse_known_args(argv)

    res = analyze_impact(args.base)

    if args.list:
        if res.run_all:
            print("ALL")
        else:
            print("\n".join(res.impacted_tests))
        return 0

    print(f"Changed files ({len(res.changed_files)}):")
    for f in res.changed_files:
        print(f"  {f}")
    print(f"\n{res.reason}")

    if res.run_all:
        targets = ["tests"]
        print("\n▶  Running FULL suite")
    elif res.impacted_tests:
        targets = res.impacted_tests
        print(f"\n🎯 Impacted tests ({len(targets)}):")
        for t in targets:
            print(f"  {t}")
    else:
        print("\nNothing impacted — no tests to run.")
        return 0

    if args.run:
        cmd = [sys.executable, "-m", "pytest", *targets, *extra]
        print(f"\n$ {' '.join(cmd)}")
        return subprocess.run(cmd).returncode

    return 0


if __name__ == "__main__":
    sys.exit(main())
