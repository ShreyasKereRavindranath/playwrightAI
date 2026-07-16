"""
Example 2 — Generator agent.

TestPlan scenario  →  runnable pytest test wired to the framework's fixtures.

Run:
    python examples/02_generator_demo.py

This demo plans a feature, generates a test file for the happy-path scenario,
writes it under tests/, and prints the pytest command to run it. Works offline.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents import Generator, Planner


def main() -> None:
    plan = Planner().plan("User logs in with valid credentials and lands on the inventory page")
    scenario = plan.scenarios[0]  # the happy path

    gen = Generator()
    print(f"Generator mode: {gen.mode.upper()}")
    print(f"Scenario: {scenario.id}\n")

    artifact = gen.generate(scenario)

    print("── Generated test code " + "─" * 40)
    print(artifact.test_code)

    written = artifact.write()  # never overwrites existing files
    if written:
        print(f"Wrote → {', '.join(written)}")
        print(f"\nRun it with:\n    pytest {artifact.test_path} -v")
    else:
        print(f"{artifact.test_path} already exists — not overwritten.")


if __name__ == "__main__":
    main()
