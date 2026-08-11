"""
Browser diagnostics — capture console errors, uncaught JS errors and failed
network requests from a live Page at failure time.

Built on Playwright's *retrospective* accessors added in 1.56/1.57:
    page.console_messages()   → every console message the page emitted
    page.page_errors()        → every uncaught JS exception
    page.requests()           → every network request the page made

Unlike the old `page.on("console", ...)` event wiring, these need no setup — we
just read them after a test fails. They're the single biggest signal for
answering "why did this fail?": a 500 on an XHR or a `TypeError` in the console
usually *is* the root cause, and feeding them to the AI Failure Analysis /
triage clustering makes those diagnoses source-aware.

Every accessor is guarded, so on a Playwright older than 1.56 (or any odd
browser state) this degrades to "no diagnostics" rather than breaking the run.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Keep the captured payload bounded — this rides along with the traceback into
# SQLite, the HTML report and the LLM prompt, so it must stay compact.
_MAX_ITEMS = 15
_MAX_TEXT = 300


def _truncate(text: str, limit: int = _MAX_TEXT) -> str:
    text = " ".join(str(text or "").split())  # collapse whitespace/newlines
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _console_errors(page) -> list:
    """console.error() / console.warning() lines emitted by the page."""
    out = []
    try:
        for msg in page.console_messages():
            mtype = getattr(msg, "type", "")
            if mtype in ("error", "warning"):
                out.append({"type": mtype, "text": _truncate(getattr(msg, "text", ""))})
                if len(out) >= _MAX_ITEMS:
                    break
    except Exception as exc:  # accessor absent (<1.56) or browser closed
        logger.debug("console_messages() unavailable: %s", exc)
    return out


def _page_errors(page) -> list:
    """Uncaught exceptions that surfaced in the page (window.onerror)."""
    out = []
    try:
        for err in page.page_errors():
            out.append(_truncate(getattr(err, "message", "") or str(err)))
            if len(out) >= _MAX_ITEMS:
                break
    except Exception as exc:
        logger.debug("page_errors() unavailable: %s", exc)
    return out


def _failed_requests(page) -> list:
    """Network requests that failed outright or returned a 4xx/5xx status."""
    out = []
    try:
        for req in page.requests():
            try:
                entry = None
                if getattr(req, "failure", None):
                    entry = {"url": _truncate(req.url, 200), "status": "failed",
                             "detail": _truncate(req.failure, 120)}
                else:
                    resp = req.response()
                    status = getattr(resp, "status", 0) if resp else 0
                    if status >= 400:
                        entry = {"url": _truncate(req.url, 200), "status": status,
                                 "detail": getattr(resp, "status_text", "") or ""}
                if entry:
                    out.append(entry)
                    if len(out) >= _MAX_ITEMS:
                        break
            except Exception:
                continue
    except Exception as exc:
        logger.debug("requests() unavailable: %s", exc)
    return out


def capture(page) -> dict:
    """Return {console, page_errors, failed_requests} for a live Page.

    Always returns a dict (empty lists on older Playwright / no browser),
    never raises — callers use it in best-effort failure-reporting paths.
    """
    if page is None:
        return {"console": [], "page_errors": [], "failed_requests": []}
    return {
        "console": _console_errors(page),
        "page_errors": _page_errors(page),
        "failed_requests": _failed_requests(page),
    }


def is_empty(diag: Optional[dict]) -> bool:
    if not diag:
        return True
    return not (diag.get("console") or diag.get("page_errors") or diag.get("failed_requests"))


def to_text(diag: Optional[dict]) -> str:
    """Flatten diagnostics into a compact human/LLM-readable block.

    Returns "" when there's nothing to report, so callers can skip empty
    sections without extra checks.
    """
    if is_empty(diag):
        return ""
    lines: list = []
    if diag.get("page_errors"):
        lines.append("Uncaught JS errors:")
        lines += [f"  • {m}" for m in diag["page_errors"]]
    if diag.get("failed_requests"):
        lines.append("Failed network requests:")
        lines += [f"  • [{r['status']}] {r['url']}"
                  + (f" — {r['detail']}" if r.get("detail") else "")
                  for r in diag["failed_requests"]]
    if diag.get("console"):
        lines.append("Console errors/warnings:")
        lines += [f"  • [{m['type']}] {m['text']}" for m in diag["console"]]
    return "\n".join(lines)
