#!/usr/bin/env python3
"""
Natural Language Test Generator — Capability #5

Converts a plain-English scenario description into a complete Page Object
stub + parametrized pytest test function, wired to conftest.py fixtures.

Usage:
    python tools/generate_test.py "User cannot checkout with empty cart"
    python tools/generate_test.py "Login fails with SQL injection" --page login --output tests/test_security.py
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.llm_client import LLMClient

_SYSTEM = (
    "You are a senior QA automation engineer writing Playwright Python tests. "
    "Follow the framework conventions strictly. "
    "Always return ONLY valid Python code — no markdown, no explanations."
)

_PAGE_OBJECT_PROMPT = """
Generate a Playwright Python Page Object class for the following scenario.

Framework conventions:
- Class inherits from BasePage (from pages.base_page import BasePage)
- All locators as @property methods returning Locator
- Locator priority: data-testid > aria role > stable id > name > CSS
- Action methods describe user intent (e.g., login(), submit_form())
- NO assertions inside Page Object methods
- File: pages/{page_name}_page.py

Scenario: {scenario}
Page name: {page_name}

Generate the Page Object class only:
"""

_TEST_PROMPT = """
Generate a pytest test function for the following scenario.

Framework conventions:
- File: tests/test_{feature}.py
- Function name: test_{scenario_snake_case}
- Uses @pytest.mark.smoke or @pytest.mark.regression
- Structure: Arrange → Act → Assert
- Uses expect() from playwright.sync_api for UI assertions
- Uses page object fixtures (e.g., login_page, inventory_page)
- Uses e2e_data fixture for test data (loaded from data/{feature}_test_data.json)
- One scenario per function
- Include docstring with Scenario: and Expected:

Scenario: {scenario}
Available page fixture: {page_fixture}
Feature name: {feature}

Generate the test function only (including imports):
"""


def main():
    parser = argparse.ArgumentParser(description="Generate a Playwright test from a scenario description")
    parser.add_argument("scenario", help="Plain-English test scenario")
    parser.add_argument("--page",   default="", help="Page name (e.g. 'login', 'checkout')")
    parser.add_argument("--feature", default="", help="Feature name for file naming")
    parser.add_argument("--output", default="", help="Output test file path (optional)")
    parser.add_argument("--page-object-only", action="store_true", help="Generate Page Object only")
    parser.add_argument("--test-only", action="store_true", help="Generate test only")
    args = parser.parse_args()

    llm = LLMClient()
    if not llm.available:
        print("ERROR: OPENAI_API_KEY not set in config/.env")
        sys.exit(1)

    scenario   = args.scenario
    page_name  = args.page or _infer_page(scenario)
    feature    = args.feature or page_name
    page_fixture = f"{page_name}_page" if page_name else "page"

    print(f"\n🔧 Generating test artifacts for: '{scenario}'\n")

    if not args.test_only:
        print("── Page Object ──────────────────────────────")
        po_code = llm.complete(
            prompt=_PAGE_OBJECT_PROMPT.format(scenario=scenario, page_name=page_name),
            system=_SYSTEM,
            max_tokens=800,
        )
        print(po_code)
        po_path = Path(f"pages/{page_name}_page.py")
        if not po_path.exists():
            po_path.write_text(po_code)
            print(f"\n✅ Written to {po_path}\n")
        else:
            print(f"\n⚠  {po_path} already exists — printed above for manual merge.\n")

    if not args.page_object_only:
        print("── Test Function ────────────────────────────")
        test_code = llm.complete(
            prompt=_TEST_PROMPT.format(
                scenario=scenario,
                page_fixture=page_fixture,
                feature=feature,
                scenario_snake_case=_to_snake(scenario),
            ),
            system=_SYSTEM,
            max_tokens=800,
        )
        print(test_code)

        output = args.output or f"tests/test_{feature}.py"
        dest = Path(output)
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(test_code)
            print(f"\n✅ Written to {dest}\n")
        else:
            print(f"\n⚠  {dest} already exists — printed above for manual merge.\n")

    print("Done. Review generated code before committing.")


def _infer_page(scenario: str) -> str:
    words = scenario.lower().split()
    for kw in ["login", "checkout", "cart", "dashboard", "inventory", "profile", "register"]:
        if kw in words:
            return kw
    return "unknown"


def _to_snake(text: str) -> str:
    return "_".join(
        w.lower() for w in text.split() if w.isalnum()
    )[:60]


if __name__ == "__main__":
    main()
