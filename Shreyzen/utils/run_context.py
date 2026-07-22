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
    # Record the *effective* LLM details for reports. If the selected provider
    # isn't active/configured we don't advertise its broken details — we record
    # the deterministic offline fallback the agents actually run on instead.
    ctx.update(_llm_context())
    return ctx


# Details recorded when no LLM is active/configured. The agents (planner /
# generator / healer) transparently fall back to their deterministic offline
# implementations, so reports reflect *that* rather than an inactive provider.
_FALLBACK_LLM = {
    "llm_provider": "offline (deterministic fallback)",
    "llm_model": "rule-based · no LLM",
    "llm_active": "no",
}


def _llm_context() -> dict:
    """Effective LLM (provider/model/active) details for a report.

    Returns the selected provider + model only when it validates (configured and
    active); otherwise returns the offline fallback details. Never raises.
    """
    try:
        from llm.service import get_service
        svc = get_service()
        if svc.validate().ok:
            return {
                "llm_provider": svc.current_provider_name(),
                "llm_model": svc.current_model() or "(default)",
                "llm_active": "yes",
            }
    except Exception:
        pass
    return dict(_FALLBACK_LLM)


def as_rows(ctx: dict) -> list[tuple[str, str]]:
    """Human-friendly (label, value) rows for report tables."""
    labels = {
        "user": "Run by", "hostname": "Host", "os": "OS", "os_version": "OS version",
        "platform": "Platform", "arch": "Architecture", "python": "Python",
        "timestamp": "Timestamp", "timezone": "Timezone", "tz_offset": "UTC offset",
        "browser": "Browser", "browser_version": "Browser version", "device": "Device",
        "target": "Target", "scenario": "Scenario", "profile": "Profile",
        "provider": "LLM provider", "selection": "Selected tests", "markers": "Markers",
        "llm_provider": "LLM provider", "llm_model": "LLM model", "llm_active": "LLM active",
    }
    rows = []
    for key, val in ctx.items():
        rows.append((labels.get(key, key.replace("_", " ").title()), str(val)))
    return rows
