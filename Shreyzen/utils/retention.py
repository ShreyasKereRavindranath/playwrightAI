"""
Report retention / auto-pruning — Capability: bounded artifact growth.

Run artifacts (functional_runs, load_runs, screenshots, videos, a11y,
visual_diffs, runs/*.json) accumulate forever otherwise; videos and
self-contained HTML dominate disk. This module enforces three independent
caps per category and reports exactly what it dropped (no silent truncation):

  * RETENTION_MAX_RUNS      keep only the N most-recent entries per category
  * RETENTION_MAX_AGE_DAYS  drop entries older than D days
  * RETENTION_MAX_SIZE_MB   drop oldest entries until the whole tree is under budget

Ordering is by entry mtime (newest kept). A "run" is a single directory
(functional_runs/<id>) or, for the flat `runs/` folder, a single JSON file.

Usage:
    from utils.retention import prune_reports
    report = prune_reports()                 # honour Config, actually delete
    report = prune_reports(dry_run=True)     # report only, delete nothing

CLI:
    python -m tools.prune_reports [--dry-run] [--max-runs N] [--max-age-days D] [--max-size-mb M]
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from config.config import Config
from utils.logger import get_logger

logger = get_logger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
_REPORTS = _ROOT / "logs_and_reports"

# Categories that hold one sub-directory per run.
_DIR_CATEGORIES = (
    "functional_runs",
    "load_runs",
    "screenshots",
    "videos",
    "a11y",
    "visual_diffs",
)
# Categories that hold one file per run.
_FILE_CATEGORIES = ("runs",)


@dataclass
class Entry:
    """A single prunable unit — a run directory or a run summary file."""
    path: Path
    is_dir: bool
    mtime: float
    size: int


@dataclass
class CategoryResult:
    category: str
    kept: int = 0
    removed: list[str] = field(default_factory=list)
    freed_bytes: int = 0
    reasons: dict[str, str] = field(default_factory=dict)  # entry name → why dropped


@dataclass
class PruneReport:
    dry_run: bool
    categories: list[CategoryResult] = field(default_factory=list)
    total_removed: int = 0
    total_freed_bytes: int = 0

    @property
    def freed_mb(self) -> float:
        return round(self.total_freed_bytes / (1024 * 1024), 1)

    def as_dict(self) -> dict:
        return {
            "dry_run": self.dry_run,
            "total_removed": self.total_removed,
            "freed_mb": self.freed_mb,
            "categories": [
                {
                    "category": c.category,
                    "kept": c.kept,
                    "removed": c.removed,
                    "freed_mb": round(c.freed_bytes / (1024 * 1024), 1),
                    "reasons": c.reasons,
                }
                for c in self.categories
            ],
        }


def _dir_size(path: Path) -> int:
    total = 0
    try:
        for p in path.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                continue
    except OSError:
        pass
    return total


def _collect(category: str) -> list[Entry]:
    """List prunable entries in a category, newest first."""
    base = _REPORTS / category
    if not base.exists():
        return []
    entries: list[Entry] = []
    is_file_cat = category in _FILE_CATEGORIES
    for child in base.iterdir():
        # Skip hidden bookkeeping files (.gitkeep, .DS_Store).
        if child.name.startswith("."):
            continue
        try:
            st = child.stat()
        except OSError:
            continue
        if is_file_cat:
            if not child.is_file():
                continue
            entries.append(Entry(child, False, st.st_mtime, st.st_size))
        else:
            if not child.is_dir():
                continue
            entries.append(Entry(child, True, st.st_mtime, _dir_size(child)))
    entries.sort(key=lambda e: e.mtime, reverse=True)
    return entries


def _remove(entry: Entry, dry_run: bool) -> None:
    if dry_run:
        return
    if entry.is_dir:
        shutil.rmtree(entry.path, ignore_errors=True)
    else:
        try:
            entry.path.unlink()
        except OSError:
            pass


def prune_reports(
    *,
    max_runs: Optional[int] = None,
    max_age_days: Optional[int] = None,
    max_size_mb: Optional[int] = None,
    dry_run: bool = False,
    now: Optional[float] = None,
    log: Optional[Callable[[str], None]] = None,
) -> PruneReport:
    """
    Enforce retention caps across all artifact categories.

    Any limit left as None falls back to the matching Config value. A limit of
    0 disables that particular check. Returns a PruneReport describing exactly
    what was (or would be) removed.
    """
    max_runs = Config.RETENTION_MAX_RUNS if max_runs is None else max_runs
    max_age_days = Config.RETENTION_MAX_AGE_DAYS if max_age_days is None else max_age_days
    max_size_mb = Config.RETENTION_MAX_SIZE_MB if max_size_mb is None else max_size_mb
    now = time.time() if now is None else now
    emit = log or (lambda m: None)

    report = PruneReport(dry_run=dry_run)
    age_cutoff = now - max_age_days * 86400 if max_age_days > 0 else None

    # ── Per-category: age + count caps ────────────────────────────────────────
    for category in (*_DIR_CATEGORIES, *_FILE_CATEGORIES):
        entries = _collect(category)
        result = CategoryResult(category=category)
        survivors: list[Entry] = []
        for idx, entry in enumerate(entries):
            reason = None
            if age_cutoff is not None and entry.mtime < age_cutoff:
                reason = f"older than {max_age_days}d"
            elif max_runs > 0 and idx >= max_runs:
                reason = f"beyond keep-last-{max_runs}"
            if reason:
                _remove(entry, dry_run)
                result.removed.append(entry.path.name)
                result.reasons[entry.path.name] = reason
                result.freed_bytes += entry.size
            else:
                survivors.append(entry)
        result.kept = len(survivors)
        report.categories.append(result)

    # ── Global size cap: drop oldest survivors across categories until under budget
    if max_size_mb > 0:
        budget = max_size_mb * 1024 * 1024
        # Rebuild the survivor set with their owning category result.
        survivors: list[tuple[Entry, CategoryResult]] = []
        by_cat = {c.category: c for c in report.categories}
        for category in (*_DIR_CATEGORIES, *_FILE_CATEGORIES):
            cat_result = by_cat[category]
            for entry in _collect(category):
                if entry.path.name in cat_result.reasons:
                    continue  # already removed above (unless dry-run, still counts as gone)
                survivors.append((entry, cat_result))
        current = sum(e.size for e, _ in survivors)
        if current > budget:
            # Oldest first across the whole tree.
            survivors.sort(key=lambda pair: pair[0].mtime)
            for entry, cat_result in survivors:
                if current <= budget:
                    break
                _remove(entry, dry_run)
                cat_result.removed.append(entry.path.name)
                cat_result.reasons[entry.path.name] = f"over size budget {max_size_mb}MB"
                cat_result.freed_bytes += entry.size
                cat_result.kept = max(0, cat_result.kept - 1)
                current -= entry.size

    report.total_removed = sum(len(c.removed) for c in report.categories)
    report.total_freed_bytes = sum(c.freed_bytes for c in report.categories)

    if report.total_removed:
        verb = "Would prune" if dry_run else "Pruned"
        msg = (f"{verb} {report.total_removed} run artifact(s), "
               f"freeing {report.freed_mb} MB")
        logger.info(msg)
        emit(msg)
        for c in report.categories:
            if c.removed:
                emit(f"  {c.category}: dropped {len(c.removed)}, kept {c.kept}")
    else:
        emit("Retention: nothing to prune (within all limits).")

    return report


def auto_prune(log: Optional[Callable[[str], None]] = None) -> Optional[PruneReport]:
    """
    Fire-and-forget prune used by the run engines after each run. Honours
    Config.RETENTION_ENABLED and never raises — retention must not fail a run.
    """
    if not Config.RETENTION_ENABLED:
        return None
    try:
        return prune_reports(log=log)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Auto-prune skipped due to error: %s", exc)
        return None
