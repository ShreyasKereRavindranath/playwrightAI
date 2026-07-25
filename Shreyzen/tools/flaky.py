"""
Flaky-test triage CLI — diagnose and quarantine known-flaky tests.

Runtime flakiness is tracked in logs_and_reports/flakiness.db (Capability 2).
This tool surfaces the flaky tests, explains *why* each is flaky (AI diagnosis,
with a deterministic offline fallback), and manages the quarantine list that
keeps them out of the gating run.

    python -m tools.flaky                      # list flaky tests + quarantine status
    python -m tools.flaky --diagnose           # diagnose every flaky test
    python -m tools.flaky --diagnose "<id>"    # diagnose one test id
    python -m tools.flaky --quarantine "<id>"  # add to quarantine (with diagnosis)
    python -m tools.flaky --unquarantine "<id>"
    python -m tools.flaky --list-quarantine
    python -m tools.flaky --json
"""

from __future__ import annotations

import argparse
import json
import sys

from utils import flaky_analysis, quarantine
from utils.flakiness_tracker import FlakinessTracker


def _diagnose(tracker: FlakinessTracker, test_id: str) -> dict:
    return flaky_analysis.diagnose(test_id, tracker.get_history(test_id))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Diagnose and quarantine flaky tests.")
    p.add_argument("--diagnose", nargs="?", const="*", metavar="TEST_ID",
                   help="diagnose all flaky tests, or one TEST_ID")
    p.add_argument("--quarantine", metavar="TEST_ID", help="quarantine a test (with diagnosis)")
    p.add_argument("--unquarantine", metavar="TEST_ID", help="remove a test from quarantine")
    p.add_argument("--list-quarantine", action="store_true", help="show the quarantine list")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    args = p.parse_args(argv)

    tracker = FlakinessTracker()

    # ── mutations ────────────────────────────────────────────────────────────
    if args.quarantine:
        dx = _diagnose(tracker, args.quarantine)
        new = quarantine.add(args.quarantine, reason=dx["explanation"], category=dx["category"],
                             confidence=dx["confidence"], suggested_fix=dx["suggested_fix"],
                             source=f"manual:{dx['via']}")
        print(f"{'Quarantined' if new else 'Updated quarantine for'} {args.quarantine} "
              f"[{dx['category']}] — {dx['suggested_fix']}")
        return 0
    if args.unquarantine:
        ok = quarantine.remove(args.unquarantine)
        print(("Removed from quarantine: " if ok else "Not in quarantine: ") + args.unquarantine)
        return 0
    if args.list_quarantine:
        entries = quarantine.list_entries()
        if args.json:
            print(json.dumps(entries, indent=2))
        elif not entries:
            print("Quarantine is empty. ✅")
        else:
            print(f"\n🚧 Quarantine ({len(entries)} test(s)):\n")
            for e in entries:
                print(f"  • {e['test_id']}")
                print(f"      category: {e.get('category','?')}  ·  source: {e.get('source','?')}")
                if e.get("suggested_fix"):
                    print(f"      fix: {e['suggested_fix']}")
        return 0

    # ── read: flaky list (+ optional diagnosis) ────────────────────────────────
    flaky = tracker.get_flaky_tests()
    q_ids = quarantine.ids()

    if args.diagnose:
        targets = [args.diagnose] if args.diagnose != "*" else [f["test_id"] for f in flaky]
        report = []
        for tid in targets:
            dx = _diagnose(tracker, tid)
            report.append({"test_id": tid, **dx})
        if args.json:
            print(json.dumps(report, indent=2))
        elif not report:
            print("No flaky tests to diagnose. ✅")
        else:
            print(f"\n🔬 Flaky diagnosis ({len(report)} test(s)):\n")
            for r in report:
                print(f"  • {r['test_id']}")
                print(f"      category  : {r['category']} (confidence {r['confidence']:.0%}, via {r['via']})")
                print(f"      why       : {r['explanation']}")
                print(f"      fix       : {r['suggested_fix']}")
                print(f"      quarantined: {'yes' if r['test_id'] in q_ids else 'no'}\n")
        return 0

    # default: list flaky tests
    rows = [{**f, "quarantined": f["test_id"] in q_ids} for f in flaky]
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print("No flaky tests detected. ✅")
        return 0
    print(f"\n⚠️  {len(rows)} flaky test(s) (flake_rate ≥ threshold):\n")
    for r in rows:
        tag = " [QUARANTINED]" if r["quarantined"] else ""
        print(f"  {r['flake_rate']:.0%}  {r['test_id']}{tag}")
        print(f"        {r['passes']} pass / {r['failures']} fail of last {r['total']}")
    print("\nDiagnose:  python -m tools.flaky --diagnose")
    print("Quarantine: python -m tools.flaky --quarantine \"<test id>\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
