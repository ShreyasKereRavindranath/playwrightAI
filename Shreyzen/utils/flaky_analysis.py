"""
Flaky-test diagnosis.

Given a test's recent outcome history (from FlakinessTracker) and, optionally, its
latest failure text, produce a structured diagnosis:

    {category, explanation, confidence, suggested_fix}

There are two paths, mirroring the rest of the framework's AI features:
- **LLM path** — asks the configured provider for a JSON diagnosis.
- **Offline heuristic** — a deterministic classifier over the history (duration
  variance → timing, browser-correlated failures → cross-browser, strict
  alternation → order/state), so diagnosis works with no LLM and is unit-testable.

Categories (stable ids): timing | animation | network | data | selector |
order_dependency | cross_browser | unknown.
"""

from __future__ import annotations

import statistics
from typing import Optional

CATEGORIES = ("timing", "animation", "network", "data", "selector",
              "order_dependency", "cross_browser", "unknown")

_FAILED = {"FAILED", "ERROR"}

_SYSTEM = (
    "You are a senior test-automation engineer diagnosing a flaky Playwright test. "
    "Given its recent outcome history and any failure text, identify the single most "
    "likely root-cause category and a concrete fix. Respond with JSON only."
)

_PROMPT = """A test is flaky (it both passes and fails without code changes).

Test: {test_id}
Recent outcomes (newest first): {outcomes}
Durations (s): {durations}
Browsers seen: {browsers}
Latest failure text (may be empty):
{failure_text}

Choose ONE category from: {categories}.
Return JSON with exactly these keys:
  "category": one of the categories above,
  "explanation": one or two sentences on why,
  "confidence": a number 0.0–1.0,
  "suggested_fix": a concrete, actionable fix.
"""


def _durations(history: list) -> list:
    return [float(h.get("duration") or 0.0) for h in history]


def offline_diagnosis(test_id: str, history: list, failure_text: str = "") -> dict:
    """Deterministic heuristic diagnosis — no LLM required."""
    outcomes = [str(h.get("outcome", "")).upper() for h in history]
    durations = [d for d in _durations(history) if d > 0]
    text = (failure_text or "").lower()

    # 1. Failure text keywords are the strongest signal when present.
    if any(k in text for k in ("timeout", "waiting for", "exceeded")):
        cat, why = "timing", "The failure text mentions a timeout / waiting for an element."
    elif any(k in text for k in ("animation", "transition", "not stable", "unstable")):
        cat, why = "animation", "The failure text points to an element still animating/settling."
    elif any(k in text for k in ("net::", "econnreset", "connection", "503", "502", "429")):
        cat, why = "network", "The failure text points to a network/backend hiccup."
    elif any(k in text for k in ("no node found", "no element", "selector", "strict mode")):
        cat, why = "selector", "The failure text points to a selector that didn't match."
    # 2. Otherwise infer from the outcome/duration/browser pattern.
    elif _browser_correlated(history):
        cat, why = "cross_browser", "Failures cluster on specific browser(s), passing on others."
    elif len(durations) >= 3 and _high_variance(durations):
        cat, why = "timing", "Run time swings widely between runs — a race/timing dependency."
    elif _strictly_alternating(outcomes):
        cat, why = "order_dependency", "Outcomes alternate cleanly — likely shared state / test ordering."
    else:
        cat, why = "unknown", "No dominant signal in the history; needs a manual look."

    return {
        "category": cat,
        "explanation": why,
        "confidence": 0.4,   # heuristics are advisory
        "suggested_fix": _FIX_HINTS.get(cat, "Investigate the failing run's trace and stabilise the step."),
    }


_FIX_HINTS = {
    "timing": "Replace implicit waits with explicit waits on the target state "
              "(expect(...).to_be_visible()), and avoid sleep().",
    "animation": "Wait for the element to be stable/enabled before acting, or disable "
                 "CSS animations in the test context.",
    "network": "Stub/await the network call the step depends on, or add a bounded retry.",
    "data": "Use isolated, per-test data (unique fixtures) instead of shared records.",
    "selector": "Adopt a stable locator (data-testid > role) — self-healing may already "
                "suggest one (see tools/heal_pr.py).",
    "order_dependency": "Make the test independent: reset state in setup/teardown; don't rely "
                        "on another test running first.",
    "cross_browser": "Reproduce on the failing browser; guard browser-specific behaviour.",
    "unknown": "Open the failing run's Playwright trace and stabilise the offending step.",
}


def _high_variance(durations: list) -> bool:
    if len(durations) < 3:
        return False
    mean = statistics.mean(durations)
    if mean <= 0:
        return False
    # Coefficient of variation > 0.5 = wide swings relative to the mean.
    return (statistics.pstdev(durations) / mean) > 0.5


def _browser_correlated(history: list) -> bool:
    """True when failures concentrate on some browsers and passes on others."""
    by_browser: dict = {}
    for h in history:
        b = h.get("browser") or ""
        if not b:
            continue
        rec = by_browser.setdefault(b, [0, 0])  # [fail, pass]
        if str(h.get("outcome", "")).upper() in _FAILED:
            rec[0] += 1
        else:
            rec[1] += 1
    if len(by_browser) < 2:
        return False
    mostly_fail = [b for b, (f, p) in by_browser.items() if f > 0 and p == 0]
    mostly_pass = [b for b, (f, p) in by_browser.items() if p > 0 and f == 0]
    return bool(mostly_fail and mostly_pass)


def _strictly_alternating(outcomes: list) -> bool:
    if len(outcomes) < 4:
        return False
    bools = [o in _FAILED for o in outcomes]
    return all(bools[i] != bools[i + 1] for i in range(len(bools) - 1))


def diagnose(test_id: str, history: list, *, failure_text: str = "", llm=None) -> dict:
    """Diagnose a flaky test. Uses the LLM when available, else the heuristic.

    `llm` is injectable (an object with .available and .complete_json) for tests.
    Always returns a dict with category/explanation/confidence/suggested_fix and a
    `via` key ("llm" | "offline").
    """
    offline = offline_diagnosis(test_id, history, failure_text)
    offline["via"] = "offline"

    try:
        if llm is None:
            from utils.llm_client import LLMClient
            llm = LLMClient()
        if not getattr(llm, "available", False):
            return offline
        outcomes = [str(h.get("outcome", "")).upper() for h in history]
        result = llm.complete_json(
            prompt=_PROMPT.format(
                test_id=test_id, outcomes=outcomes[:20],
                durations=[round(d, 2) for d in _durations(history)][:20],
                browsers=sorted({h.get("browser") or "?" for h in history}),
                failure_text=(failure_text or "")[:1500],
                categories=", ".join(CATEGORIES),
            ),
            system=_SYSTEM,
        )
        cat = str(result.get("category", "")).strip().lower()
        if cat not in CATEGORIES:
            return offline   # invalid/empty LLM output → trust the heuristic
        return {
            "category": cat,
            "explanation": str(result.get("explanation", "")).strip() or offline["explanation"],
            "confidence": float(result.get("confidence", 0.6) or 0.6),
            "suggested_fix": str(result.get("suggested_fix", "")).strip()
                             or _FIX_HINTS.get(cat, offline["suggested_fix"]),
            "via": "llm",
        }
    except Exception:
        return offline
