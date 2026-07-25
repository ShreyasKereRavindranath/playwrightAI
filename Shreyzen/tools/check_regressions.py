"""
CLI for regression detection.

Compares the most recent run to the median of prior runs and reports any
pass-rate / duration / performance regressions. Exits non-zero when a
regression is found (with --gate) so CI can fail on trend regressions.

    python -m tools.check_regressions            # report only
    python -m tools.check_regressions --gate     # exit 1 if any regression
    python -m tools.check_regressions --json      # machine-readable output
"""

import argparse
import json
import sys

from utils.regression_detector import detect_with_config


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Detect run-over-run regressions.")
    parser.add_argument("--gate", action="store_true",
                        help="Exit non-zero if any regression is detected.")
    parser.add_argument("--json", action="store_true",
                        help="Emit JSON instead of human-readable text.")
    args = parser.parse_args(argv)

    report = detect_with_config()

    if args.json:
        print(json.dumps(report.as_dict(), indent=2))
    else:
        if not report.latest_run:
            print("Not enough run history to detect regressions yet.")
        elif not report.has_regressions:
            print(f"No regressions in run {report.latest_run} "
                  f"(vs {report.baseline_runs} prior runs). ✅")
        else:
            print(f"⚠️  {len(report.regressions)} regression(s) in run "
                  f"{report.latest_run} (baseline: {report.baseline_runs} runs):\n")
            for r in report.regressions:
                icon = "🔴" if r.severity == "critical" else "🟠"
                print(f"  {icon} [{r.severity.upper()}] {r.message}")

    if args.gate and report.has_regressions:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
