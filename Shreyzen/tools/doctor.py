"""
`shreyzen doctor` — validate a Shreyzen environment before you run tests.

Runs a series of quick checks and prints a ✓/✗ report with a fix hint for
anything that's wrong. Exits non-zero if a *hard* prerequisite fails (so it can
gate CI), while soft/optional checks only warn.

Usage:
    python -m tools.doctor
    python -m tools.doctor --json     # machine-readable
"""

from __future__ import annotations

import argparse
import importlib
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

OK, WARN, FAIL = "ok", "warn", "fail"
_ICON = {OK: "✓", WARN: "!", FAIL: "✗"}


@dataclass
class Check:
    name: str
    status: str          # ok | warn | fail
    detail: str = ""
    fix: str = ""


# ── individual checks ────────────────────────────────────────────────────────

def _check_python() -> Check:
    v = sys.version_info
    ver = f"{v.major}.{v.minor}.{v.micro}"
    if v < (3, 11):
        return Check("Python version", FAIL, f"found {ver}",
                     "Shreyzen needs Python 3.11+. Create the venv with python3.11 -m venv .venv")
    if v >= (3, 14):
        return Check("Python version", WARN, f"found {ver}",
                     "3.14+ is untested (greenlet/pydantic-core). 3.11–3.13 recommended.")
    return Check("Python version", OK, ver)


def _check_dependencies() -> Check:
    required = ["playwright", "pytest", "pydantic", "requests", "dotenv", "jinja2"]
    missing = []
    for mod in required:
        try:
            importlib.import_module(mod)
        except Exception:
            missing.append("python-dotenv" if mod == "dotenv" else mod)
    if missing:
        return Check("Python dependencies", FAIL, f"missing: {', '.join(missing)}",
                     "pip install -r requirements.txt  (activate the venv first)")
    return Check("Python dependencies", OK, "core packages importable")


def _check_venv() -> Check:
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    if in_venv:
        return Check("Virtual environment", OK, "active")
    return Check("Virtual environment", WARN, "not running inside a venv",
                 "source .venv/bin/activate  (or run ./run.sh which manages it)")


def _check_env_file() -> Check:
    env = ROOT / "config" / ".env"
    if not env.exists():
        return Check("config/.env", FAIL, "not found",
                     "python -m tools.init   (or: cp config/.env.example config/.env)")
    return Check("config/.env", OK, "present")


def _check_required_config() -> Check:
    try:
        from config.config import Config
    except Exception as exc:  # pragma: no cover - defensive
        return Check("Required config", FAIL, f"could not load config: {exc}",
                     "Check config/config.py imports and config/.env syntax.")
    missing = [k for k in ("BASE_URL", "TEST_USER_EMAIL", "TEST_USER_PASSWORD")
               if not getattr(Config, k, "")]
    if "BASE_URL" in missing:
        return Check("Required config", FAIL, "BASE_URL is empty",
                     "Set BASE_URL in config/.env (or run python -m tools.init).")
    if missing:
        return Check("Required config", WARN, f"unset: {', '.join(missing)}",
                     "Set them in config/.env if your tests need authentication.")
    return Check("Required config", OK, f"BASE_URL={Config.BASE_URL}")


def _check_browser() -> Check:
    try:
        from config.config import Config
        from utils.browser_bootstrap import _binary_present
    except Exception as exc:  # pragma: no cover - defensive
        return Check("Playwright browser", WARN, f"could not check: {exc}",
                     "playwright install chromium")
    browser = getattr(Config, "BROWSER", "chromium")
    if browser in {"chrome", "msedge"}:
        return Check("Playwright browser", OK, f"{browser} (uses OS-installed channel)")
    if _binary_present(browser):
        return Check("Playwright browser", OK, f"{browser} installed")
    auto = getattr(Config, "AUTO_INSTALL_BROWSERS", True)
    hint = ("It will auto-install on the first run (AUTO_INSTALL_BROWSERS=true)."
            if auto else f"playwright install {browser}")
    return Check("Playwright browser", WARN, f"{browser} not installed", hint)


def _check_git() -> Check:
    if not shutil.which("git"):
        return Check("Git", WARN, "git not on PATH",
                     "Install git to enable impact analysis & change-based test selection.")
    try:
        inside = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=ROOT, capture_output=True, text=True, timeout=10)
        if inside.returncode == 0 and inside.stdout.strip() == "true":
            return Check("Git repository", OK, "detected")
    except Exception:
        pass
    return Check("Git repository", WARN, "not a git repo",
                 "Impact analysis (Cap 21) needs a git repo; git init to enable it.")


def _check_llm() -> Check:
    try:
        from llm.service import get_service
        svc = get_service()
        provider = svc.current_provider_name()
        result = svc.validate()
    except Exception as exc:  # pragma: no cover - defensive
        return Check("LLM provider", WARN, f"could not validate: {exc}",
                     "AI features are optional. See LLM_PROVIDERS.md to configure one.")
    if result.ok:
        return Check("LLM provider", OK, f"{provider} ready")
    return Check("LLM provider", WARN, f"{provider}: {result.detail}",
                 "AI features are optional. Set the provider's API key or pick a "
                 "local one (ollama/lmstudio). See LLM_PROVIDERS.md.")


CHECKS = [
    _check_python, _check_venv, _check_dependencies, _check_env_file,
    _check_required_config, _check_browser, _check_git, _check_llm,
]


# ── report ───────────────────────────────────────────────────────────────────

def run(args: argparse.Namespace) -> int:
    results = [c() for c in CHECKS]

    if args.json:
        print(json.dumps([asdict(r) for r in results], indent=2))
    else:
        print("\n🩺 Shreyzen doctor — environment check\n")
        width = max(len(r.name) for r in results)
        for r in results:
            print(f"  {_ICON[r.status]}  {r.name.ljust(width)}   {r.detail}")
            if r.status != OK and r.fix:
                print(f"       ↳ fix: {r.fix}")
        print()

    fails = [r for r in results if r.status == FAIL]
    warns = [r for r in results if r.status == WARN]
    if not args.json:
        if fails:
            print(f"❌ {len(fails)} blocking issue(s), {len(warns)} warning(s). "
                  "Fix the blockers above, then re-run: python -m tools.doctor")
        elif warns:
            print(f"✅ No blocking issues. {len(warns)} optional warning(s) — "
                  "safe to run tests.")
        else:
            print("✅ All checks passed. You're ready: pytest -m smoke -v")
    return 1 if fails else 0


def build_parser(parser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    parser = parser or argparse.ArgumentParser(
        prog="shreyzen doctor", description="Validate the Shreyzen environment.")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.set_defaults(func=run)
    return parser


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
