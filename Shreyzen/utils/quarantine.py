"""
Quarantine store — the list of known-flaky tests to keep out of the gating run.

A quarantined test still exists and can be run in its own lane
(`pytest --quarantine-only`), but it's deselected from the normal run so a known
flake can't fail the build. Entries are enriched with an AI diagnosis (category /
explanation / suggested fix) when available.

Backed by a small JSON file (data/quarantine.json). All functions take an
injectable `path` so they're unit-testable against a temp file.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
_PATH = ROOT / "data" / "quarantine.json"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load(path: Path = _PATH) -> dict:
    """Return the {test_id: entry} mapping (empty dict if none/unreadable)."""
    if not Path(path).exists():
        return {}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data.get("tests", {}) if isinstance(data, dict) else {}
    except Exception:
        return {}


def save(tests: dict, path: Path = _PATH) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps({"tests": tests}, indent=2), encoding="utf-8")


def ids(path: Path = _PATH) -> set:
    """Set of quarantined test node-ids."""
    return set(load(path).keys())


def is_quarantined(test_id: str, path: Path = _PATH) -> bool:
    return test_id in load(path)


def list_entries(path: Path = _PATH) -> list:
    """Quarantine entries as a list of dicts (each includes its test_id)."""
    return [{"test_id": tid, **entry} for tid, entry in sorted(load(path).items())]


def add(test_id: str, *, reason: str = "", category: str = "", confidence: float = 0.0,
        suggested_fix: str = "", flake_rate: Optional[float] = None,
        source: str = "manual", path: Path = _PATH) -> bool:
    """Add/update a quarantine entry. Returns True if it was newly added."""
    tests = load(path)
    is_new = test_id not in tests
    existing = tests.get(test_id, {})
    tests[test_id] = {
        "reason": reason or existing.get("reason", ""),
        "category": category or existing.get("category", ""),
        "confidence": confidence or existing.get("confidence", 0.0),
        "suggested_fix": suggested_fix or existing.get("suggested_fix", ""),
        "flake_rate": flake_rate if flake_rate is not None else existing.get("flake_rate"),
        "source": source,
        "added": existing.get("added", _now()),
        "updated": _now(),
    }
    save(tests, path)
    return is_new


def remove(test_id: str, path: Path = _PATH) -> bool:
    """Remove a test from quarantine. Returns True if it was present."""
    tests = load(path)
    if test_id in tests:
        del tests[test_id]
        save(tests, path)
        return True
    return False
