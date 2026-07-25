"""
Self-heal → Page Object auto-fix / auto-PR.

Runtime self-healing logs each recovered locator to data/healing_log.json (with
the original + healed selector). This tool folds those fixes back into your Page
Objects — previewing, applying, or opening a PR — instead of you hand-editing.

    python -m tools.heal_pr                 # preview proposed edits (dry run)
    python -m tools.heal_pr --apply         # write the fixes into pages/*.py
    python -m tools.heal_pr --open-pr       # write + commit on a branch + open a PR (needs gh)
    python -m tools.heal_pr --json          # machine-readable plan

Only *unambiguous* healings are applied automatically (original selector found in
exactly one place in one Page Object). Ambiguous / not-found / no-original
entries are reported for a human to handle.
"""

from __future__ import annotations

import argparse
import json
import sys

from utils import heal_pr as core
from utils.ai_self_heal import AISelfHeal

_ICON = {"applied": "✅", "would_apply": "📝", "not_found": "❓",
         "ambiguous": "⚠️", "no_original": "➖"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Fold self-healed locators back into Page Objects.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--apply", action="store_true", help="write the fixes into pages/*.py")
    group.add_argument("--open-pr", action="store_true", dest="open_pr",
                       help="write, commit on a branch, and open a PR (requires gh)")
    parser.add_argument("--branch", default="shreyzen/self-heal", help="branch name for --open-pr")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)

    entries = core.load_pending()
    if not entries:
        print("No pending self-healing events. ✅" if not args.json else "[]")
        return 0

    write = args.apply or args.open_pr
    report = core.plan(entries, write=write)

    if args.json:
        print(json.dumps([r.__dict__ for r in report.results], indent=2, default=str))
    else:
        _print_report(report, write)

    # Mark applied entries so they don't resurface next run.
    if write:
        for r in report.results:
            if r.status == "applied":
                AISelfHeal.set_status(r.intent, core.APPLIED)

    if args.open_pr:
        out = core.open_pr(report, branch=args.branch)
        _print_pr(out)
        if out.get("pr"):
            for r in report.applied:
                AISelfHeal.set_status(r.intent, core.PR_OPENED)

    # Exit non-zero if nothing could be auto-applied but there were candidates,
    # so CI can flag "healings need attention".
    if not report.applied and not args.json:
        return 1
    return 0


def _print_report(report: core.ApplyReport, write: bool) -> None:
    print(f"\n🩹 Self-heal → Page Object ({len(report.results)} pending healing(s))\n")
    for r in report.results:
        icon = _ICON.get(r.status, "•")
        print(f"  {icon}  [{r.status}] {r.intent}")
        print(f"       {r.original!r} → {r.healed!r}"
              + (f"   ({r.path})" if r.path else ""))
        if r.detail:
            print(f"       {r.detail}")
        if r.diff and r.status in ("would_apply", "applied"):
            for line in r.diff.splitlines():
                if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
                    print(f"         {line}")
        print()
    applied = len(report.applied)
    if write:
        print(f"{applied} fix(es) written. Review the diff before committing.")
    else:
        print(f"{applied} fix(es) would be applied. Re-run with --apply to write, "
              "or --open-pr to write + open a PR.")


def _print_pr(out: dict) -> None:
    if out.get("pr"):
        print(f"\n🚀 PR opened: {out.get('url', '(url unknown)')}")
    elif out.get("committed"):
        print(f"\n✅ Committed on branch '{out.get('branch')}'.\n   {out.get('detail', '')}")
    else:
        print(f"\n⚠  Could not open PR: {out.get('reason')}. {out.get('detail', '')}")


if __name__ == "__main__":
    sys.exit(main())
