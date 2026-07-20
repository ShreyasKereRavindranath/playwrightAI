"""Capture who/where/when a run happened, for embedding in every report.

Collects the acting user, machine, OS, browser, and a timezone-aware timestamp
reflecting the region the run was launched from. Kept dependency-free and
defensive so it never breaks a run.
"""

from __future__ import annotations

import getpass
import platform
import socket
from datetime import datetime
from typing import Optional


def _safe(fn, default: str = "unknown") -> str:
    try:
        return str(fn()) or default
    except Exception:
        return default


def capture(extra: Optional[dict] = None) -> dict:
    """Return a flat dict of user + system + timestamp metadata."""
    now = datetime.now().astimezone()  # local, timezone-aware
    ctx = {
        "user": _safe(getpass.getuser),
        "hostname": _safe(socket.gethostname),
        "os": f"{platform.system()} {platform.release()}".strip(),
        "os_version": _safe(platform.version),
        "platform": _safe(platform.platform),
        "arch": _safe(platform.machine),
        "python": platform.python_version(),
        "timestamp": now.isoformat(timespec="seconds"),
        "timezone": now.tzname() or "",
        "tz_offset": now.strftime("%z"),
    }
    if extra:
        ctx.update({k: v for k, v in extra.items() if v is not None and v != ""})
    # Best-effort: record the active LLM provider + model so it shows in reports.
    try:
        from llm.service import get_service
        svc = get_service()
        ctx.setdefault("llm_provider", svc.current_provider_name())
        ctx.setdefault("llm_model", svc.current_model())
    except Exception:
        pass
    return ctx


def as_rows(ctx: dict) -> list[tuple[str, str]]:
    """Human-friendly (label, value) rows for report tables."""
    labels = {
        "user": "Run by", "hostname": "Host", "os": "OS", "os_version": "OS version",
        "platform": "Platform", "arch": "Architecture", "python": "Python",
        "timestamp": "Timestamp", "timezone": "Timezone", "tz_offset": "UTC offset",
        "browser": "Browser", "browser_version": "Browser version", "device": "Device",
        "target": "Target", "scenario": "Scenario", "profile": "Profile",
        "provider": "LLM provider", "selection": "Selected tests", "markers": "Markers",
        "llm_provider": "LLM provider", "llm_model": "LLM model",
    }
    rows = []
    for key, val in ctx.items():
        rows.append((labels.get(key, key.replace("_", " ").title()), str(val)))
    return rows
