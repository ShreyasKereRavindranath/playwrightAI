"""
Example 1 — Planner agent.

Feature / user story  →  structured TestPlan (scenarios, priorities, steps).

Run:
    python examples/01_planner_demo.py

Works with no API key (offline heuristic). Set OPENAI_API_KEY in config/.env to
use the LLM path — the same code, no changes required.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents import Planner


def main() -> None:
    feature = "User completes a full purchase: login, add a product to the cart, and check out"

    planner = Planner()  # pass offline=True to force the deterministic path
    print(f"Planner mode: {planner.mode.upper()}")
    print(f"Feature: {feature}\n")

    plan = planner.plan(feature)

    print(f"Summary: {plan.summary}")
    print(f"Scenarios ({len(plan.scenarios)}):\n")
    for s in plan.scenarios:
        print(f"  [{s.priority}] {s.title}")
        print(f"        id={s.id} markers={s.markers} pages={s.pages} data={s.data_needs}")
        for step in s.steps:
            print(f"          {step.kind:<8} {step.description}")
        print()

    out = Path("logs_and_reports/plans/example_purchase.json")
    plan.save(out)
    print(f"Saved plan → {out}")


if __name__ == "__main__":
    main()
