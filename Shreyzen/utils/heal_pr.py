"""
Self-heal → auto-PR core.

Runtime self-healing (utils/ai_self_heal.py) recovers a broken locator live and
logs it to data/healing_log.json with the **original** selector and the **healed**
one. This module turns those log entries into an actual Page Object fix: it finds
the original selector in `pages/*.py`, rewrites it to the healed selector, and
(optionally) commits the change on a branch and opens a PR.

Safety first — it only auto-applies when a healing is *unambiguous*: the original
selector must appear in exactly one place in exactly one Page Object file. Zero
matches → `not_found`; more than one → `ambiguous`; no original recorded →
`no_original`. Those are reported for a human, never guessed at.

The find/apply/diff functions are pure filesystem operations (no git, no LLM), so
they're fully unit-testable. Git/PR helpers degrade gracefully when the repo or
the `gh` CLI isn't available.
"""

from __future__ import annotations

import difflib
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
_PAGES_DIR = ROOT / "pages"
_HEAL_LOG = ROOT / "data" / "healing_log.json"

# Healing statuses (extends ai_self_heal's PENDING_REVIEW / REVIEWED).
APPLIED = "APPLIED"
PR_OPENED = "PR_OPENED"


@dataclass
class HealResult:
    intent: str
    original: str
    healed: str
    status: str                 # applied | would_apply | not_found | ambiguous | no_original
    path: Optional[str] = None  # repo-relative Page Object path when resolved
    diff: str = ""
    detail: str = ""


@dataclass
class ApplyReport:
    results: list = field(default_factory=list)

    @property
    def applied(self) -> list:
        return [r for r in self.results if r.status in (APPLIED.lower(), "would_apply")]

    @property
    def applied_paths(self) -> list:
        return sorted({r.path for r in self.applied if r.path})


# ── Log access ───────────────────────────────────────────────────────────────

def load_pending(log_path: Path = _HEAL_LOG) -> list:
    """Return healing entries still awaiting a Page Object update."""
    if not log_path.exists():
        return []
    try:
        entries = json.loads(log_path.read_text())
    except Exception:
        return []
    return [e for e in entries if e.get("status") == "PENDING_REVIEW"]


# ── Find + apply (pure filesystem) ───────────────────────────────────────────

def find_locator_site(old_selector: str, pages_dir: Path = _PAGES_DIR) -> list:
    """Return [{path, count}] for Page Object files containing *old_selector*."""
    sites = []
    if not old_selector or not pages_dir.exists():
        return sites
    for py in sorted(pages_dir.glob("*.py")):
        if py.name == "__init__.py":
            continue
        try:
            text = py.read_text(encoding="utf-8")
        except OSError:
            continue
        count = text.count(old_selector)
        if count:
            sites.append({"path": py, "count": count})
    return sites


def _unified_diff(old_text: str, new_text: str, rel_path: str) -> str:
    return "".join(difflib.unified_diff(
        old_text.splitlines(keepends=True), new_text.splitlines(keepends=True),
        fromfile=f"a/{rel_path}", tofile=f"b/{rel_path}"))


def apply_healing(entry: dict, *, pages_dir: Path = _PAGES_DIR,
                  root: Path = ROOT, write: bool = False) -> HealResult:
    """Resolve one healing entry to a Page Object edit.

    Only unambiguous single-site matches are applied; everything else is
    reported for manual handling. When write=True the file is rewritten in place.
    """
    intent = entry.get("intent", "")
    old = (entry.get("original_locator") or "").strip()
    new = (entry.get("healed_locator") or "").strip()

    if not old:
        return HealResult(intent, old, new, "no_original",
                          detail="No original selector recorded — can't locate it in a Page Object.")
    if not new:
        return HealResult(intent, old, new, "not_found", detail="No healed locator recorded.")
    if old == new:
        return HealResult(intent, old, new, "not_found",
                          detail="Original and healed selectors are identical.")

    sites = find_locator_site(old, pages_dir)
    total = sum(s["count"] for s in sites)
    if total == 0:
        return HealResult(intent, old, new, "not_found",
                          detail=f"Selector {old!r} not found in any Page Object.")
    if len(sites) > 1 or total > 1:
        where = ", ".join(f"{s['path'].name}×{s['count']}" for s in sites)
        return HealResult(intent, old, new, "ambiguous",
                          detail=f"Selector {old!r} appears in multiple places ({where}) — fix by hand.")

    site = sites[0]
    rel = str(site["path"].relative_to(root))
    old_text = site["path"].read_text(encoding="utf-8")
    new_text = old_text.replace(old, new)
    diff = _unified_diff(old_text, new_text, rel)

    if write:
        site["path"].write_text(new_text, encoding="utf-8")
        return HealResult(intent, old, new, APPLIED.lower(), path=rel, diff=diff)
    return HealResult(intent, old, new, "would_apply", path=rel, diff=diff)


def plan(entries: list, *, pages_dir: Path = _PAGES_DIR, root: Path = ROOT,
         write: bool = False) -> ApplyReport:
    """Apply/preview a list of healing entries; return an ApplyReport."""
    return ApplyReport(results=[
        apply_healing(e, pages_dir=pages_dir, root=root, write=write) for e in entries])


# ── Git / PR (best-effort, degrades gracefully) ──────────────────────────────

def _run(cmd: list, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)


def open_pr(report: ApplyReport, *, root: Path = ROOT, branch: str = "shreyzen/self-heal",
            title: Optional[str] = None) -> dict:
    """Commit the applied fixes on a branch and open a PR via `gh`.

    Returns a dict describing what happened. Degrades gracefully:
    - not a git repo         → {ok: False, reason: "not_a_git_repo"}
    - nothing applied        → {ok: False, reason: "nothing_to_commit"}
    - `gh` missing/uncfg'd   → commits on the branch and returns instructions.
    """
    paths = report.applied_paths
    if not paths:
        return {"ok": False, "reason": "nothing_to_commit"}
    if not (root / ".git").exists():
        return {"ok": False, "reason": "not_a_git_repo",
                "detail": "Files were edited; commit them yourself."}

    n = len(paths)
    title = title or f"fix(self-heal): update {n} healed locator(s)"
    body_lines = [f"- `{r.path}`: `{r.original}` → `{r.healed}`"
                  for r in report.applied]
    body = "Auto-generated by Shreyzen self-heal.\n\n" + "\n".join(body_lines)

    steps = [
        (["git", "checkout", "-b", branch], "create branch"),
        (["git", "add", *paths], "stage files"),
        (["git", "commit", "-m", title], "commit"),
    ]
    log = []
    for cmd, label in steps:
        proc = _run(cmd, root)
        log.append(f"{label}: {'ok' if proc.returncode == 0 else proc.stderr.strip()}")
        if proc.returncode != 0 and label == "commit":
            return {"ok": False, "reason": "commit_failed", "log": log}

    # Open the PR if gh is available and authenticated.
    gh = _run(["gh", "--version"], root)
    if gh.returncode != 0:
        return {"ok": True, "committed": True, "pr": False, "branch": branch, "log": log,
                "detail": "Committed on branch; `gh` not found. Push and open a PR manually:\n"
                          f"  git push -u origin {branch} && gh pr create --fill"}
    pr = _run(["gh", "pr", "create", "--title", title, "--body", body], root)
    if pr.returncode != 0:
        return {"ok": True, "committed": True, "pr": False, "branch": branch, "log": log,
                "detail": f"Committed on branch; `gh pr create` failed: {pr.stderr.strip()}"}
    return {"ok": True, "committed": True, "pr": True, "branch": branch,
            "url": pr.stdout.strip(), "log": log}
