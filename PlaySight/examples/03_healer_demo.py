"""
Example 3 — Healer agent.

pytest failure  →  diagnosis + concrete, reviewable fix.

Run:
    python examples/03_healer_demo.py

Demonstrates three representative Playwright failures. For the first two the
healer emits a unified diff and applies it to a throwaway copy of the source
(your real files are never touched). Works fully offline; with OPENAI_API_KEY
set, the healer additionally asks the LLM for a richer diff.
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents import Healer

# ── Case A: strict mode violation — locator matches many elements ─────────────
STRICT_ERROR = (
    'playwright._impl._errors.Error: strict mode violation: '
    'locator(".inventory_item_name") resolved to 6 elements'
)
STRICT_SOURCE = (
    "from pages.base_page import BasePage\n\n"
    "class InventoryPage(BasePage):\n"
    "    @property\n"
    "    def item_name(self):\n"
    '        return self.page.locator(".inventory_item_name")\n'
)

# ── Case B: hardcoded wait — the framework forbids time.sleep ─────────────────
SLEEP_ERROR = (
    "FAILED tests/test_cart.py::test_add_to_cart\n"
    '  File "pages/cart_page.py", line 12, in add_item\n'
    "    flaky because of a hardcoded time.sleep(3) before the click"
)
SLEEP_SOURCE = (
    "import time\n"
    "from pages.base_page import BasePage\n\n"
    "class CartPage(BasePage):\n"
    "    def add_item(self, key):\n"
    "        time.sleep(3)\n"
    "        self.click(self.add_button(key))\n"
)

# ── Case C: locator timeout on a fragile locator — guidance only ──────────────
TIMEOUT_ERROR = (
    "playwright._impl._errors.TimeoutError: Timeout 30000ms exceeded.\n"
    '  waiting for locator("text=Submit Order Now") to be visible'
)


def show(title: str, result, healer: Healer, source: str | None = None) -> None:
    dx = result.diagnosis
    print(f"\n═══ {title} " + "═" * (46 - len(title)))
    print(f"category   : {dx.category} (confidence {dx.confidence:.0%}, via {result.generated_by})")
    print(f"root cause : {dx.root_cause}")
    print(f"explanation: {result.explanation}")
    if result.suggested_fix:
        print(f"suggested fix ({result.fix_kind}):")
        print("\n".join("    " + ln for ln in result.suggested_fix.splitlines()))

    # For diff fixes, prove it applies by patching a throwaway copy.
    if result.fix_kind == "diff" and source is not None:
        tmp = Path(tempfile.mkdtemp()) / "patched.py"
        tmp.write_text(source, encoding="utf-8")
        result.diagnosis.file = str(tmp)
        if healer.apply(result):
            print("applied to throwaway copy →")
            print("\n".join("    " + ln for ln in tmp.read_text().splitlines()))


def main() -> None:
    healer = Healer()
    print(f"Healer mode: {healer.mode.upper()}")

    show("Case A — strict mode violation", healer.heal(STRICT_ERROR, source_code=STRICT_SOURCE),
         healer, STRICT_SOURCE)
    show("Case B — hardcoded wait", healer.heal(SLEEP_ERROR, source_code=SLEEP_SOURCE),
         healer, SLEEP_SOURCE)
    show("Case C — fragile locator timeout", healer.heal(TIMEOUT_ERROR), healer)


if __name__ == "__main__":
    main()
