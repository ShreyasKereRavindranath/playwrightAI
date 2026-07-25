"""
Test impact analysis — Capability: run only what a change can break.

Unlike the keyword-map prioritiser (tools/prioritize_tests.py), this resolves
the *actual* Python import graph: a test is impacted if it transitively imports
any changed module. No hand-maintained mapping to keep in sync.

How it works:
  1. Ask git which files changed (vs a base ref).
  2. Parse every .py file under the project into an import graph
     (module → set of first-party modules it imports).
  3. Invert it and compute, for each test module, its transitive import
     closure. If that closure intersects the changed set, the test is impacted.
  4. Certain "core" changes (conftest, config, base_page, requirements) force a
     full run — their blast radius is everything.

Returns test *files*; callers can pass those straight to pytest. Non-Python
changes (e.g. data/*.json) that a test reads aren't captured by the import
graph, so when in doubt we fall back to the full suite rather than skip.
"""

from __future__ import annotations

import ast
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
_TESTS_DIR = _ROOT / "tests"

# Any change touching these forces the full suite (blast radius = everything).
_FULL_SUITE_TRIGGERS = (
    "tests/conftest.py",
    "config/config.py",
    "config/.env",
    "pages/base_page.py",
    "requirements.txt",
    "pytest.ini",
)


@dataclass
class ImpactResult:
    changed_files: list[str] = field(default_factory=list)
    impacted_tests: list[str] = field(default_factory=list)  # repo-relative .py paths
    run_all: bool = False
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "changed_files": self.changed_files,
            "impacted_tests": self.impacted_tests,
            "run_all": self.run_all,
            "reason": self.reason,
        }


def get_changed_files(base_ref: str = "HEAD") -> list[str]:
    """
    Files changed vs base_ref, plus uncommitted working-tree changes.

    Paths are returned relative to the project root (_ROOT) via `git diff
    --relative`, so they line up with the import-graph module resolution even
    when the project is a subdirectory of the git repo.
    """
    files: set[str] = set()
    for args in (["git", "diff", "--relative", "--name-only", base_ref],   # committed vs base
                 ["git", "diff", "--relative", "--name-only"],             # unstaged
                 ["git", "diff", "--relative", "--name-only", "--cached"]):  # staged
        try:
            out = subprocess.run(args, cwd=str(_ROOT), capture_output=True,
                                 text=True, check=True).stdout
            files.update(f.strip() for f in out.splitlines() if f.strip())
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            logger.debug("git diff (%s) failed: %s", " ".join(args), exc)
    return sorted(files)


def _module_name(path: Path) -> str:
    """Repo-relative .py path → dotted module name (best-effort)."""
    rel = path.relative_to(_ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _first_party_imports(path: Path, known_modules: set[str]) -> set[str]:
    """Dotted module names imported by `path` that are first-party."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, OSError, UnicodeDecodeError):
        return set()
    imports: set[str] = set()

    def _match(dotted: str) -> Optional[str]:
        # Resolve a dotted import to the longest known first-party module.
        parts = dotted.split(".")
        for i in range(len(parts), 0, -1):
            cand = ".".join(parts[:i])
            if cand in known_modules:
                return cand
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                m = _match(alias.name)
                if m:
                    imports.add(m)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                # relative import — resolve against this file's package
                pkg = _module_name(path).split(".")
                base = pkg[: len(pkg) - (node.level - 1)] if not path.name == "__init__.py" \
                    else pkg[: len(pkg) - (node.level - 1)]
                mod = ".".join(base + ([node.module] if node.module else []))
            else:
                mod = node.module or ""
            m = _match(mod)
            if m:
                imports.add(m)
            # also try each imported name as a submodule (from pkg import sub)
            for alias in node.names:
                if mod:
                    m2 = _match(f"{mod}.{alias.name}")
                    if m2:
                        imports.add(m2)
    return imports


def _build_graph() -> tuple[dict[str, set[str]], dict[str, Path]]:
    """Return (module → first-party imports) and (module → file path)."""
    py_files = [p for p in _ROOT.rglob("*.py")
                if ".venv" not in p.parts and "__pycache__" not in p.parts
                and "node_modules" not in p.parts]
    mod_to_path = {_module_name(p): p for p in py_files}
    known = set(mod_to_path)
    graph = {mod: _first_party_imports(path, known)
             for mod, path in mod_to_path.items()}
    return graph, mod_to_path


def _transitive_closure(start: str, graph: dict[str, set[str]]) -> set[str]:
    seen: set[str] = set()
    stack = [start]
    while stack:
        cur = stack.pop()
        for dep in graph.get(cur, ()):  # modules `cur` imports
            if dep not in seen:
                seen.add(dep)
                stack.append(dep)
    return seen


def analyze_impact(base_ref: str = "HEAD") -> ImpactResult:
    """Compute which test files are impacted by the current changes."""
    changed = get_changed_files(base_ref)
    result = ImpactResult(changed_files=changed)

    if not changed:
        result.run_all = False
        result.reason = "No changes detected."
        return result

    # Full-suite triggers short-circuit everything.
    for c in changed:
        if any(c == t or c.startswith(t) for t in _FULL_SUITE_TRIGGERS):
            result.run_all = True
            result.reason = f"Core file changed ({c}) — full suite required."
            return result

    graph, mod_to_path = _build_graph()

    # Changed Python modules (that we can resolve in the graph).
    changed_mods = set()
    non_py_changed = []
    for c in changed:
        p = (_ROOT / c)
        if c.endswith(".py"):
            try:
                changed_mods.add(_module_name(p))
            except ValueError:
                pass
        elif (_TESTS_DIR in p.parents) or c.startswith("data/"):
            non_py_changed.append(c)

    # A non-.py change under tests/ or data/ can't be traced through imports →
    # be safe and run everything.
    if non_py_changed:
        result.run_all = True
        result.reason = f"Non-Python change not traceable via imports ({non_py_changed[0]}) — full suite."
        return result

    if not changed_mods:
        result.run_all = True
        result.reason = "Changes not resolvable to modules — full suite."
        return result

    # A test module is impacted if it *is* a changed module, or its transitive
    # import closure intersects the changed set.
    test_mods = [m for m, p in mod_to_path.items()
                 if _TESTS_DIR in p.parents and p.name.startswith("test_")]
    impacted: list[str] = []
    for tmod in test_mods:
        closure = _transitive_closure(tmod, graph)
        if tmod in changed_mods or (closure & changed_mods):
            impacted.append(str(mod_to_path[tmod].relative_to(_ROOT)))

    result.impacted_tests = sorted(impacted)
    if impacted:
        result.reason = (f"{len(impacted)} test file(s) import "
                         f"{len(changed_mods)} changed module(s).")
    else:
        result.reason = "No test imports the changed modules — nothing to run."
    return result


def pytest_targets(base_ref: str = "HEAD") -> list[str]:
    """
    Convenience for callers: the pytest path args to run.
    Returns ['tests'] when a full run is required, the impacted files otherwise
    (possibly empty, meaning genuinely nothing to run).
    """
    res = analyze_impact(base_ref)
    if res.run_all:
        return ["tests"]
    return res.impacted_tests
