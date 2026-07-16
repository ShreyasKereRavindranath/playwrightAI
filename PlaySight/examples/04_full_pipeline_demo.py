"""
Example 4 — Full pipeline: Planner → Generator → Healer.

Shows the three agents working as one loop:
    1. Planner   decomposes a feature into scenarios.
    2. Generator turns each scenario into a pytest test file.
    3. Healer    repairs a simulated failure in one of the generated tests.

Run:
    python examples/04_full_pipeline_demo.py

Works fully offline. Generated tests are written under tests/ (existing files
are never overwritten).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents import Generator, Healer, Planner


def main() -> None:
    feature = "User can add a product to the cart and see the cart badge update"

    # 1. PLAN
    planner = Planner()
    plan = planner.plan(feature)
    print(f"① Planner ({planner.mode}) → {len(plan.scenarios)} scenario(s)")
    for s in plan.scenarios:
        print(f"     [{s.priority}] {s.id}")

    # 2. GENERATE
    gen = Generator()
    print(f"\n② Generator ({gen.mode}) → writing tests")
    artifacts = []
    for scenario in plan.scenarios:
        artifact = gen.generate(scenario)
        written = artifact.write()
        artifacts.append(artifact)
        print(f"     {scenario.id} → {artifact.test_path} "
              f"({'wrote' if written else 'exists'})")

    # 3. HEAL — simulate a failure the CI healer would receive
    print("\n③ Healer → repairing a simulated CI failure")
    healer = Healer()
    simulated_error = (
        "playwright._impl._errors.Error: strict mode violation: "
        'locator(".shopping_cart_badge") resolved to 2 elements'
    )
    result = healer.heal(simulated_error, test_id=plan.scenarios[0].id)
    print(f"     diagnosis : {result.diagnosis.category} "
          f"({result.diagnosis.confidence:.0%}, via {result.generated_by})")
    print(f"     fix ({result.fix_kind}): {result.explanation}")
    if result.suggested_fix:
        print("\n".join("       " + ln for ln in result.suggested_fix.splitlines()))

    print("\n✅ Planner → Generator → Healer loop complete.")
    paths = " ".join(a.test_path for a in artifacts)
    print(f"   Run the generated tests:  pytest {paths} -v   (needs browsers installed)")


if __name__ == "__main__":
    main()
