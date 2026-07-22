#!/usr/bin/env python3
"""
Playwright Codegen → Page Object Converter — Capability #6

Converts a raw Playwright codegen recording into the framework's POM pattern:
  raw page.click / page.fill calls  →  @property locators + action methods

Usage:
    playwright codegen https://www.saucedemo.com -o recorded.py
    python tools/codegen_converter.py recorded.py --page login
    python tools/codegen_converter.py recorded.py --page checkout --output pages/checkout_page.py
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.llm_client import LLMClient

_SYSTEM = (
    "You are a senior QA automation engineer converting raw Playwright scripts to Page Object Model. "
    "Follow the framework conventions exactly. "
    "Return only valid Python code — no markdown, no prose."
)

_PROMPT = """
Convert this raw Playwright codegen script into a proper Page Object class.

## Framework conventions:
- Class inherits from BasePage (from pages.base_page import BasePage)
- All locators declared as @property methods returning Locator
- Locator priority: data-testid > aria role > stable id > name > CSS class
- Group related actions into one method (e.g., fill username + fill password + click login = login())
- NO assertions inside the Page Object
- NO page.goto() calls — use navigate() method with self.goto(URL)
- Use self.click(), self.fill(), self.select_option() etc. (BasePage helpers)
- Include a URL = "/path" class variable if identifiable
- Include clear docstrings

## Raw recorded script:
```python
{raw_code}
```

## Page name: {page_name}

Generate the complete Page Object class:
"""

_TEST_PROMPT = """
Now generate a basic smoke test for this Page Object class.

Page Object file: pages/{page_name}_page.py
Page Object class: {class_name}

Generate one smoke test function that exercises the main user flow from the recording.
Use the framework fixtures (page, e2e_data). Include imports, marker, and docstring.
"""


def main():
    parser = argparse.ArgumentParser(description="Convert Playwright codegen output to Page Object Model")
    parser.add_argument("input_file",  help="Path to the codegen-recorded .py file")
    parser.add_argument("--page",      default="recorded", help="Page name (e.g. login, checkout)")
    parser.add_argument("--output",    default="", help="Output path for Page Object (optional)")
    parser.add_argument("--with-test", action="store_true", help="Also generate a smoke test")
    args = parser.parse_args()

    input_path = Path(args.input_file)
    if not input_path.exists():
        print(f"ERROR: File not found: {input_path}")
        sys.exit(1)

    raw_code   = input_path.read_text(encoding="utf-8")
    page_name  = args.page
    class_name = "".join(w.capitalize() for w in page_name.split("_")) + "Page"

    llm = LLMClient()
    if not llm.available:
        print("ERROR: OPENAI_API_KEY not set in config/.env")
        sys.exit(1)

    print(f"\n🔄 Converting '{input_path.name}' → {class_name}\n")

    po_code = llm.complete(
        prompt=_PROMPT.format(raw_code=raw_code[:6_000], page_name=page_name),
        system=_SYSTEM,
        max_tokens=1200,
    )

    print("── Page Object ──────────────────────────────")
    print(po_code)

    dest = Path(args.output or f"pages/{page_name}_page.py")
    if not dest.exists():
        dest.write_text(po_code)
        print(f"\n✅ Page Object written to {dest}")
    else:
        print(f"\n⚠  {dest} already exists — printed above for manual merge.")

    if args.with_test:
        print("\n── Smoke Test ───────────────────────────────")
        test_code = llm.complete(
            prompt=_TEST_PROMPT.format(page_name=page_name, class_name=class_name),
            system=_SYSTEM,
            max_tokens=600,
        )
        print(test_code)
        test_dest = Path(f"tests/web/test_{page_name}.py")
        if not test_dest.exists():
            test_dest.write_text(test_code)
            print(f"\n✅ Smoke test written to {test_dest}")

    print("\nDone. Review all generated files before committing.")


if __name__ == "__main__":
    main()
