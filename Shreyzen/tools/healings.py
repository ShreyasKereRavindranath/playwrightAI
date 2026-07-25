"""
Self-healing review CLI.

When ENABLE_AI_HEALING is on, a locator that times out at runtime is recovered
live (DOM snapshot → LLM → retry) and the healed locator is logged to
data/healing_log.json. This CLI surfaces those healings so you can fold the good
ones back into your Page Objects.

    python -m tools.healings              # list pending (unreviewed) healings
    python -m tools.healings --all        # include already-reviewed entries
    python -m tools.healings --json        # machine-readable
    python -m tools.healings --reviewed "<intent>"   # mark an entry reviewed
"""

import argparse
import json
import sys

from utils.ai_self_heal import AISelfHeal


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Review runtime self-healing events.")
    parser.add_argument("--all", action="store_true", help="Include reviewed entries too.")
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    parser.add_argument("--reviewed", metavar="INTENT",
                        help="Mark the healing entry with this intent as reviewed.")
    args = parser.parse_args(argv)

    if args.reviewed:
        AISelfHeal.mark_reviewed(args.reviewed)
        print(f"Marked reviewed: {args.reviewed}")
        return 0

    from pathlib import Path
    log_path = Path("data/healing_log.json")
    if args.all and log_path.exists():
        entries = json.loads(log_path.read_text())
    else:
        entries = AISelfHeal.get_pending_reviews()

    if args.json:
        print(json.dumps(entries, indent=2))
        return 0

    if not entries:
        print("No self-healing events recorded. ✅")
        return 0

    print(f"{len(entries)} self-healing event(s):\n")
    for e in entries:
        status = e.get("status", "?")
        print(f"  [{status}] {e.get('timestamp','')}")
        print(f"    intent : {e.get('intent','')}")
        print(f"    healed : {e.get('healed_locator','')}")
        print(f"    page   : {e.get('page_url','')}\n")
    print("Fold the good locators into your Page Objects, then:")
    print('  python -m tools.healings --reviewed "<intent>"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
