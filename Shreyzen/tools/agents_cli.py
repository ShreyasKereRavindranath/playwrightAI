#!/usr/bin/env python3
"""
Shreyzen Agents CLI — one entry point for the Planner → Generator → Healer trio.

Subcommands
-----------
  plan       Feature/user story  → TestPlan JSON
  generate   TestPlan (or feature)→ pytest test files
  heal       pytest log / error  → diagnosis + fix
  pipeline   Feature → plan → generate (end-to-end authoring)

All subcommands run with no API key (deterministic offline mode). Set
OPENAI_API_KEY in config/.env to switch to the LLM path automatically, or pass
--offline to force the deterministic path.

Examples
--------
  python tools/agents_cli.py plan "User can sort products by price low-to-high"
  python tools/agents_cli.py pipeline "Full purchase flow" --write
  python tools/agents_cli.py generate --plan logs_and_reports/plans/full_purchase_flow.json --write
  python tools/agents_cli.py heal --log logs_and_reports/pytest.log
  python tools/agents_cli.py heal --error "strict mode violation: locator('.btn') resolved to 3" --file pages/x.py
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents import Generator, Healer, Planner, TestPlan  # noqa: E402
from agents.base_agent import BaseAgent  # noqa: E402

_PLAN_DIR = Path("logs_and_reports/plans")


def _banner(agent: BaseAgent, name: str) -> None:
    print(f"\n🧠 {name} — mode: {agent.mode.upper()}\n" + "─" * 52)


# ── plan ──────────────────────────────────────────────────────────────────────

def cmd_plan(args) -> None:
    planner = Planner(offline=args.offline)
    _banner(planner, "Planner")
    plan = planner.plan(args.feature)

    print(f"Feature : {plan.feature}")
    print(f"Summary : {plan.summary}")
    print(f"Scenarios ({len(plan.scenarios)}):")
    for s in plan.scenarios:
        print(f"  • [{s.priority}] {s.id}  {s.markers}  pages={s.pages}")
        for step in s.steps:
            print(f"        {step.kind:<8} {step.description}")

    out = Path(args.output) if args.output else _PLAN_DIR / f"{BaseAgent.slugify(plan.feature)}.json"
    saved = plan.save(out)
    print(f"\n✅ Plan saved → {saved}")


# ── generate ────────────────────────────────────────────────────────────────

def cmd_generate(args) -> None:
    gen = Generator(offline=args.offline)
    _banner(gen, "Generator")

    if args.plan:
        plan = TestPlan.load(args.plan)
    elif args.feature:
        plan = Planner(offline=args.offline).plan(args.feature)
    else:
        print("ERROR: pass --plan <file.json> or a feature string with --feature")
        sys.exit(2)

    for scenario in plan.scenarios:
        artifact = gen.generate(scenario, output_dir=args.output_dir)
        print(f"\n── {scenario.id}  ({artifact.generated_by}) → {artifact.test_path}")
        if args.write:
            written = artifact.write(overwrite=args.overwrite)
            print("   " + ("wrote: " + ", ".join(written) if written else "skipped (exists)"))
            v = artifact.last_validation
            if v:
                print("   " + ("✅ collects under pytest"
                               + (f" (after {v['repairs']} AI repair round(s))" if v['repairs'] else "")
                               if v.get("ok") else
                               f"⚠ still fails collection after {v['repairs']} repair round(s) — review"))
        else:
            print("\n".join("   " + ln for ln in artifact.test_code.splitlines()[:20]))
            print("   … (use --write to save)")

    if not args.write:
        print("\n(Dry run — re-run with --write to save files.)")


# ── heal ──────────────────────────────────────────────────────────────────────

def cmd_heal(args) -> None:
    healer = Healer(offline=args.offline)
    _banner(healer, "Healer")

    if args.error:
        results = [healer.heal(error_text=args.error, source_file=args.file or None)]
    elif args.log:
        results = healer.heal_from_log(args.log)
    else:
        print("ERROR: pass --log <pytest.log> or --error <text> [--file <src>]")
        sys.exit(2)

    for i, r in enumerate(results, 1):
        dx = r.diagnosis
        print(f"\n── Failure {i}: {dx.test_id}")
        print(f"   category   : {dx.category} (confidence {dx.confidence:.0%}, via {r.generated_by})")
        print(f"   root cause : {dx.root_cause}")
        if dx.failing_symbol:
            print(f"   symbol     : {dx.failing_symbol}")
        print(f"   explanation: {r.explanation}")
        if r.suggested_fix:
            print(f"   suggested fix ({r.fix_kind}):")
            print("\n".join("      " + ln for ln in r.suggested_fix.splitlines()))
        if args.apply and r.fix_kind == "diff":
            print("   → applying…", "OK" if healer.apply(r) else "FAILED (review manually)")


# ── pipeline ────────────────────────────────────────────────────────────────

def cmd_pipeline(args) -> None:
    planner, gen = Planner(offline=args.offline), Generator(offline=args.offline)
    print(f"\n🔗 Pipeline — Planner+Generator mode: {planner.mode.upper()}\n" + "═" * 52)

    plan = planner.plan(args.feature)
    saved = plan.save(_PLAN_DIR / f"{BaseAgent.slugify(plan.feature)}.json")
    print(f"1. Planned {len(plan.scenarios)} scenario(s) → {saved}")

    print("2. Generating tests:")
    for scenario in plan.scenarios:
        artifact = gen.generate(scenario, output_dir=args.output_dir)
        if args.write:
            written = artifact.write(overwrite=args.overwrite)
            status = ("wrote " + ", ".join(written)) if written else "skipped (exists)"
            v = artifact.last_validation
            if v:
                status += (" · ✅ collects" + (f" (+{v['repairs']} repair)" if v['repairs'] else "")
                           if v.get("ok") else f" · ⚠ fails collection")
        else:
            status = "dry-run"
        print(f"   • {scenario.id} → {artifact.test_path}  [{status}]")

    print("\n✅ Pipeline complete." + ("" if args.write else "  (dry run — add --write to save)"))


# ── arg parsing ────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--offline", action="store_true", help="Force deterministic path (no LLM)")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("plan", help="Feature → TestPlan JSON")
    sp.add_argument("feature")
    sp.add_argument("--output", default="", help="Where to save the plan JSON")
    sp.set_defaults(func=cmd_plan)

    sg = sub.add_parser("generate", help="Plan/feature → test files")
    sg.add_argument("feature", nargs="?", default="")
    sg.add_argument("--plan", default="", help="Path to a saved TestPlan JSON")
    sg.add_argument("--output-dir", default="tests/web/generated", help="Where to write test files")
    sg.add_argument("--write", action="store_true", help="Write files (default: dry run)")
    sg.add_argument("--overwrite", action="store_true", help="Overwrite existing files")
    sg.set_defaults(func=cmd_generate)

    sh = sub.add_parser("heal", help="pytest log/error → fix")
    sh.add_argument("--log", default="", help="Path to a pytest log file")
    sh.add_argument("--error", default="", help="Paste an error string directly")
    sh.add_argument("--file", default="", help="Source file to repair (with --error)")
    sh.add_argument("--apply", action="store_true", help="Apply diff fixes to disk")
    sh.set_defaults(func=cmd_heal)

    spl = sub.add_parser("pipeline", help="Feature → plan → generate")
    spl.add_argument("feature")
    spl.add_argument("--output-dir", default="tests/web/generated")
    spl.add_argument("--write", action="store_true")
    spl.add_argument("--overwrite", action="store_true")
    spl.set_defaults(func=cmd_pipeline)

    return p


def main() -> None:
    args = build_parser().parse_args()
    # Propagate the top-level --offline onto subcommand namespaces that lack it.
    if not hasattr(args, "offline"):
        args.offline = False
    args.func(args)


if __name__ == "__main__":
    main()
