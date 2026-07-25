"""
`shreyzen init` — scaffold Shreyzen onto a new project.

Turns the framework from a saucedemo demo into a clean starting point for *your*
application: writes ``config/.env`` (pointed at your app), creates a starter
Page Object and a starter smoke test in the framework's conventions, and — with
``--clean`` — archives the bundled saucedemo demo so you start from a blank slate
without losing the reference implementation.

Usage:
    python -m tools.init                                   # interactive
    python -m tools.init --name "Acme Store" --url https://acme.example.com
    python -m tools.init --template api --url https://api.acme.com --page account
    python -m tools.init --url https://acme.example.com --clean --yes

Flags:
    --name       Human-readable project name (used in the generated docstrings).
    --url        Application root URL → BASE_URL in config/.env.
    --template   web | api | mobile   (which starter test to scaffold; default web).
    --page       Page/feature name for the starter files (default: home).
    --clean      Archive the bundled saucedemo demo into examples/legacy_saucedemo/.
    --force      Overwrite existing generated files (never overwrites silently otherwise).
    --yes        Assume "yes" to prompts (non-interactive / CI).
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

from tools import scaffold

# Repo root = parent of tools/
ROOT = Path(__file__).resolve().parent.parent

# Demo files archived by --clean (relative to ROOT). Only ones that exist move.
_DEMO_PAGES = ["login_page.py", "inventory_page.py", "cart_page.py", "checkout_page.py"]
_DEMO_TESTS = [
    "web/test_purchase_flow.py",
    "web/generated",
    "mobile/test_mobile_shopping.py",
    "api/test_api_contracts.py",
]
_DEMO_DATA = ["e2e_test_data.json", "login_test_data.json"]


# ── helpers ──────────────────────────────────────────────────────────────────

def _prompt(question: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{question}{suffix}: ").strip()
    except EOFError:
        answer = ""
    return answer or default


def _confirm(question: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    return _prompt(f"{question} (y/N)").lower() in {"y", "yes"}


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return slug or "home"


def _class_name(slug: str) -> str:
    return "".join(part.capitalize() for part in slug.split("_")) + "Page"


def _write(path: Path, content: str, force: bool) -> bool:
    """Write *content* to *path* unless it exists and force is False.

    Returns True if written, False if skipped because the file already exists.
    """
    existed = path.exists()
    if existed and not force:
        print(f"  ⏭  exists, skipped: {path.relative_to(ROOT)}  (use --force to overwrite)")
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    verb = "overwrote" if existed else "wrote"
    print(f"  ✓  {verb}: {path.relative_to(ROOT)}")
    return True


# ── config/.env ──────────────────────────────────────────────────────────────

def _write_env(base_url: str, force: bool) -> None:
    env_path = ROOT / "config" / ".env"
    example = ROOT / "config" / ".env.example"

    if env_path.exists() and not force:
        # Never clobber a real .env; just make sure BASE_URL points at their app.
        text = env_path.read_text(encoding="utf-8")
        if re.search(r"(?m)^BASE_URL=", text):
            text = re.sub(r"(?m)^BASE_URL=.*$", f"BASE_URL={base_url}", text)
        else:
            text = f"BASE_URL={base_url}\n" + text
        env_path.write_text(text, encoding="utf-8")
        print(f"  ✓  updated BASE_URL in existing config/.env → {base_url}")
        return

    if example.exists():
        text = example.read_text(encoding="utf-8")
        if re.search(r"(?m)^BASE_URL=", text):
            text = re.sub(r"(?m)^BASE_URL=.*$", f"BASE_URL={base_url}", text)
        else:
            text = f"BASE_URL={base_url}\n" + text
    else:
        text = f"BASE_URL={base_url}\nBROWSER=chromium\nHEADLESS=true\n"
    _write(env_path, text, force=True)


# ── starter files ──────────────────────────────────────────────────────────

def _scaffold_starter(app_name: str, base_url: str, page_slug: str,
                      template: str, force: bool) -> None:
    spec = scaffold.TEMPLATES[template]
    page_class = _class_name(page_slug)
    tokens = dict(
        APP_NAME=app_name, BASE_URL=base_url.rstrip("/"),
        PAGE_CLASS=page_class, PAGE_SLUG=page_slug, START_PATH="/",
    )

    if spec["page"]:
        _write(ROOT / "pages" / f"{page_slug}_page.py",
               scaffold.render(scaffold.PAGE_OBJECT, **tokens), force)

    test_name = f"test_{page_slug}_contract.py" if template == "api" else f"test_{page_slug}_smoke.py"
    _write(ROOT / "tests" / spec["subdir"] / test_name,
           scaffold.render(spec["test"], **tokens), force)


# ── --clean: archive the saucedemo demo ─────────────────────────────────────

def _archive_demo(assume_yes: bool) -> None:
    archive = ROOT / "examples" / "legacy_saucedemo"
    moves: list[tuple[Path, Path]] = []
    for name in _DEMO_PAGES:
        src = ROOT / "pages" / name
        if src.exists():
            moves.append((src, archive / "pages" / name))
    for rel in _DEMO_TESTS:
        src = ROOT / "tests" / rel
        if src.exists():
            moves.append((src, archive / "tests" / rel))
    for name in _DEMO_DATA:
        src = ROOT / "data" / name
        if src.exists():
            moves.append((src, archive / "data" / name))

    if not moves:
        print("  ℹ  no bundled demo files found to archive (already clean).")
        return

    print("\nThese saucedemo demo files will be MOVED (reversible) to "
          f"{archive.relative_to(ROOT)}/:")
    for src, _ in moves:
        print(f"    • {src.relative_to(ROOT)}")
    if not _confirm("Archive them now?", assume_yes):
        print("  ✗  skipped archiving the demo.")
        return

    for src, dst in moves:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        print(f"  ✓  moved {src.relative_to(ROOT)} → {dst.relative_to(ROOT)}")
    print("\n  ℹ  The demo now lives under examples/legacy_saucedemo/ — move files "
          "back anytime to restore it. The framework core is unaffected because "
          "tests/conftest.py loads the demo fixtures defensively.")


# ── main ─────────────────────────────────────────────────────────────────────

def run(args: argparse.Namespace) -> int:
    print("\n🎯 Shreyzen — project scaffolding\n")

    app_name = args.name or (_prompt("Project name", "My App") if not args.yes else "My App")
    base_url = args.url or (_prompt("Application base URL", "http://localhost:3000")
                            if not args.yes else "http://localhost:3000")
    template = args.template or (_prompt("Starter template (web/api/mobile)", "web")
                                 if not args.yes else "web")
    if template not in scaffold.TEMPLATES:
        print(f"❌ Unknown template '{template}'. Choose one of: "
              f"{', '.join(scaffold.TEMPLATES)}", file=sys.stderr)
        return 2
    page_slug = _slugify(args.page or (_prompt("Starter page/feature name", "home")
                                       if not args.yes else "home"))

    print(f"\nScaffolding a '{template}' project for {app_name!r} → {base_url}\n")

    print("config/.env:")
    _write_env(base_url, args.force)

    print("\nstarter files:")
    _scaffold_starter(app_name, base_url, page_slug, template, args.force)

    if args.clean:
        _archive_demo(args.yes)

    print("\n✅ Done. Next steps:")
    print("   1. Fill in real credentials in config/.env (TEST_USER_EMAIL / _PASSWORD).")
    print("   2. Replace the placeholder locators in "
          f"pages/{page_slug}_page.py with your app's selectors.")
    print("   3. Validate your setup:   python -m tools.doctor")
    print("   4. Run your starter test: pytest -m smoke -v")
    print("   5. Open Studio:           ./run.sh\n")
    return 0


def build_parser(parser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    parser = parser or argparse.ArgumentParser(
        prog="shreyzen init", description="Scaffold Shreyzen onto a new project.")
    parser.add_argument("--name", help="human-readable project name")
    parser.add_argument("--url", help="application base URL (BASE_URL)")
    parser.add_argument("--template", choices=list(scaffold.TEMPLATES),
                        help="starter template to scaffold (default: web)")
    parser.add_argument("--page", help="starter page/feature name (default: home)")
    parser.add_argument("--clean", action="store_true",
                        help="archive the bundled saucedemo demo to examples/legacy_saucedemo/")
    parser.add_argument("--force", action="store_true",
                        help="overwrite existing generated files")
    parser.add_argument("--yes", action="store_true",
                        help="assume yes to all prompts (non-interactive)")
    parser.set_defaults(func=run)
    return parser


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
