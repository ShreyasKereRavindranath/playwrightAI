"""
Validate-and-repair loop for AI-generated test code.

The NL generators (tools/generate_test.py, agents/generator.py) emit Python that
*looks* right but can fail to import, have a syntax slip, reference a fixture
that doesn't exist, or mis-name a symbol. This module closes that gap: after code
is written it runs `pytest --collect-only` on the generated files and, on
failure, feeds the exact pytest error back to an LLM to self-correct — repeating
until the files collect cleanly or the attempt budget is exhausted.

Design:
- `collect_only()` is the validation gate (imports + syntax + fixture resolution
  are all checked at collection time — no browser, no target, fast).
- `repair_generation()` is pure orchestration: the *validator* and the *repair
  function* are both injectable, so the loop is fully unit-testable without a
  real pytest subprocess or a live LLM.
- Best-effort: it never raises into the generation path; on give-up it returns
  the best attempt with `ok=False` so callers can warn ("review before running").
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parent.parent

# Validator: (test_paths) -> (ok, combined_output)
Validator = Callable[[list], "tuple[bool, str]"]
# Repair fn: (code, error, kind) -> corrected_code
RepairFn = Callable[[str, str, str], str]

_COLLECT_TIMEOUT_S = 120


@dataclass
class GenFile:
    """A generated file to validate/repair."""
    path: str            # repo-relative path (e.g. "tests/web/test_login.py")
    code: str            # current source
    kind: str = "test"   # "test" | "page"


@dataclass
class RepairOutcome:
    ok: bool
    repairs: int                       # number of repair rounds performed
    files: list                        # list[GenFile] with final code
    last_error: str = ""
    log: list = field(default_factory=list)


# ── Validation gate ──────────────────────────────────────────────────────────

def collect_only(test_paths: list, *, cwd: Optional[Path] = None,
                 extra_env: Optional[dict] = None) -> "tuple[bool, str]":
    """Run `pytest --collect-only` on *test_paths*; return (ok, output).

    Collection alone catches syntax errors, import errors, and unresolved
    fixtures — the failure modes AI-generated tests hit — without launching a
    browser or hitting a target.
    """
    cwd = cwd or ROOT
    env = os.environ.copy()
    # Keep validation fast and side-effect-free: don't auto-install browsers,
    # and satisfy Config.validate() if any collected module triggers it.
    env["AUTO_INSTALL_BROWSERS"] = "false"
    env.setdefault("TEST_USER_EMAIL", "validator@example.com")
    env.setdefault("TEST_USER_PASSWORD", "validator-password")
    env["PYTHONPATH"] = str(cwd) + os.pathsep + env.get("PYTHONPATH", "")
    if extra_env:
        env.update(extra_env)
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", *[str(p) for p in test_paths],
             "--collect-only", "-q", "-p", "no:cacheprovider", "--no-header"],
            cwd=str(cwd), env=env, capture_output=True, text=True,
            timeout=_COLLECT_TIMEOUT_S,
        )
        return proc.returncode == 0, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return False, "pytest --collect-only timed out"
    except Exception as exc:  # pragma: no cover - defensive
        return False, f"could not run pytest --collect-only: {exc}"


def extract_error(output: str, max_chars: int = 1800) -> str:
    """Pull the most useful slice of pytest output for the repair prompt."""
    if not output:
        return ""
    # Prefer the traceback/errors region if pytest emitted one.
    for marker in ("=== ERRORS ===", "ERRORS", "Traceback (most recent call last)"):
        idx = output.find(marker)
        if idx != -1:
            return output[idx:idx + max_chars].strip()
    return output[-max_chars:].strip()


# ── Orchestration ────────────────────────────────────────────────────────────

def _write(f: GenFile, cwd: Path) -> None:
    dest = cwd / f.path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(f.code, encoding="utf-8")


def repair_generation(
    files: list,
    repair_fn: RepairFn,
    *,
    validator: Optional[Validator] = None,
    max_attempts: int = 2,
    write: bool = True,
    cwd: Optional[Path] = None,
    log: Optional[Callable[[str], None]] = None,
) -> RepairOutcome:
    """Write → validate → repair the given files until they collect (or give up).

    Args:
        files: list[GenFile] to validate together (the test plus any Page Object
               it imports). Their `.code` is updated in place with repairs.
        repair_fn: (code, error, kind) -> corrected code. Injectable for tests.
        validator: (test_paths) -> (ok, output). Defaults to `collect_only`.
        max_attempts: number of repair rounds after the initial validation.
        write: write files to disk before each validation (True in real use;
               callers that only want the loop logic can pass a fake validator).
        cwd: repo root (defaults to the framework root).
    """
    cwd = cwd or ROOT
    validator = validator or (lambda paths: collect_only(paths, cwd=cwd))
    messages: list = []

    def _log(msg: str) -> None:
        messages.append(msg)
        if log:
            log(msg)

    # Validate the test files; if none are tagged "test", validate everything
    # (e.g. a Page-Object-only generation still gets an import check).
    targets = [f for f in files if f.kind == "test"] or list(files)

    repairs = 0
    last_error = ""
    while True:
        if write:
            for f in files:
                _write(f, cwd)
        ok, output = validator([f.path for f in targets])
        if ok:
            _log(f"Validation passed after {repairs} repair(s).")
            return RepairOutcome(ok=True, repairs=repairs, files=files,
                                 last_error="", log=messages)
        last_error = extract_error(output)
        if repairs >= max_attempts:
            _log(f"Gave up after {repairs} repair(s); still failing collection.")
            return RepairOutcome(ok=False, repairs=repairs, files=files,
                                 last_error=last_error, log=messages)
        repairs += 1
        _log(f"Validation failed — repair attempt {repairs}/{max_attempts}.")
        for f in files:
            try:
                fixed = repair_fn(f.code, last_error, f.kind)
            except Exception as exc:  # pragma: no cover - defensive
                _log(f"Repair fn errored on {f.path}: {exc}")
                fixed = ""
            if fixed and fixed.strip() and fixed.strip() != f.code.strip():
                f.code = fixed.strip()


# ── Default LLM-backed repair function ───────────────────────────────────────

_REPAIR_SYSTEM = (
    "You are a senior QA automation engineer maintaining a Playwright + pytest "
    "framework. You fix files so they import and collect cleanly under pytest. "
    "Return ONLY the corrected, complete file contents — no markdown, no prose."
)

_REPAIR_PROMPT = """The following {kind} file fails `pytest --collect-only`.

--- current file ({kind}) ---
{code}

--- pytest error ---
{error}

Framework conventions:
- Page Objects subclass BasePage: `from pages.base_page import BasePage`; locators
  are @property methods; no assertions inside a Page Object.
- Tests: `import pytest`, `from playwright.sync_api import expect`; receive page-
  object fixtures by name and `e2e_data` for data; module-level `pytestmark`.
- Fix syntax errors, bad/missing imports, undefined names, indentation, and any
  fixture that isn't available. Do not invent new external dependencies.

Return the complete corrected {kind} file only:"""


def _strip_fences(text: str) -> str:
    if not text:
        return ""
    m = re.match(r"^\s*```[a-zA-Z]*\n(.*)\n```\s*$", text, re.DOTALL)
    return (m.group(1) if m else text).strip()


def make_llm_repair_fn(llm=None) -> RepairFn:
    """Build a repair function backed by the configured LLM provider."""
    from utils.llm_client import LLMClient
    client = llm or LLMClient()

    def _fn(code: str, error: str, kind: str) -> str:
        if not client.available:
            return ""
        out = client.complete(
            prompt=_REPAIR_PROMPT.format(kind=kind, code=code, error=error),
            system=_REPAIR_SYSTEM, max_tokens=1200,
        )
        return _strip_fences(out)

    return _fn
