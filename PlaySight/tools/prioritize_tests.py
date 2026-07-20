#!/usr/bin/env python3
"""
Change-Impact Test Prioritiser — Capability #9

Analyses git diff to find which Page Objects were touched, then maps them
to affected test files. Outputs a pytest -k filter for the CI fast-feedback loop.

Usage:
    python tools/prioritize_tests.py                  # diff vs HEAD~1
    python tools/prioritize_tests.py --base main      # diff vs main branch
    python tools/prioritize_tests.py --list           # print affected tests only

Output:
    pytest -k "login or purchase" --timeout=60
    (or "ALL" if core files changed, meaning full suite should run)
"""

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Map: path prefix/pattern → pytest -k keyword(s)
# Add entries as you add more Page Objects and features.
_PAGE_TO_TEST_MAP = {
    "pages/login_page":     ["login"],
    "pages/inventory_page": ["inventory", "purchase", "cart"],
    "pages/cart_page":      ["cart", "purchase"],
    "pages/checkout_page":  ["checkout", "purchase"],
    "tests/web/test_login":     ["login"],
    "tests/web/test_purchase": ["purchase", "cart", "checkout"],
    "tests/api/":           ["api"],
}

# These files trigger a FULL suite run (no keyword filter)
_FULL_SUITE_TRIGGERS = [
    "pages/base_page.py",
    "config/config.py",
    "config/.env",
    "tests/conftest.py",
    "utils/",
    "requirements.txt",
]


def get_changed_files(base_ref: str = "HEAD~1") -> list:
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", base_ref],
            capture_output=True, text=True, check=True,
        )
        return [f.strip() for f in result.stdout.splitlines() if f.strip()]
    except subprocess.CalledProcessError as exc:
        print(f"git diff failed: {exc.stderr}")
        return []
    except FileNotFoundError:
        print("git not found — returning empty diff.")
        return []


def resolve_keywords(changed_files: list) -> list:
    """Return list of -k keywords. Empty list means run ALL."""
    keywords = set()

    for changed in changed_files:
        # Check full-suite triggers first
        for trigger in _FULL_SUITE_TRIGGERS:
            if changed.startswith(trigger) or changed == trigger:
                return []  # Empty = run everything

        # Map to test keywords
        for prefix, kws in _PAGE_TO_TEST_MAP.items():
            if changed.startswith(prefix):
                keywords.update(kws)

    return sorted(keywords)


def main():
    parser = argparse.ArgumentParser(description="Output pytest -k filter based on changed files")
    parser.add_argument("--base",   default="HEAD~1", help="Base ref for git diff (default: HEAD~1)")
    parser.add_argument("--list",   action="store_true", help="Print affected test keywords only")
    parser.add_argument("--dry-run", action="store_true", help="Print command without executing")
    args = parser.parse_args()

    changed = get_changed_files(args.base)

    if not changed:
        print("No changed files detected — running full suite.")
        cmd = "pytest"
        if args.list:
            print("ALL")
        else:
            print(f"\n▶  {cmd}")
            if not args.dry_run:
                subprocess.run(cmd.split(), check=False)
        return

    print(f"Changed files ({len(changed)}):")
    for f in changed:
        print(f"  {f}")

    keywords = resolve_keywords(changed)

    if not keywords:
        print("\n⚠  Core file changed — full suite recommended.")
        cmd = "pytest"
    else:
        k_expr = " or ".join(keywords)
        cmd = f'pytest -k "{k_expr}"'
        print(f"\n🎯 Affected test keywords: {keywords}")

    if args.list:
        print(" or ".join(keywords) if keywords else "ALL")
        return

    print(f"\n▶  {cmd}")
    if not args.dry_run:
        import shlex
        subprocess.run(shlex.split(cmd), check=False)


if __name__ == "__main__":
    main()
