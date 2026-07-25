"""
Failure root-cause clustering & triage.

Turns a wall of red into a ranked, labelled triage list:
  1. `signature()` normalizes a failure message/traceback into a stable key
     (strip line numbers, addresses, hex ids, quoted values, timestamps).
  2. `cluster()` groups failures by signature — recurring failures collapse into
     one cluster with a count and the affected tests.
  3. `triage()` labels each cluster: product_bug | test_bug | flaky | environment,
     with an explanation and confidence — via the LLM when available, else a
     deterministic heuristic.

The normalization and heuristic are pure functions (no LLM, no I/O), so the
whole pipeline is unit-testable. `triage()` takes an injectable `llm`.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Optional

CATEGORIES = ("product_bug", "test_bug", "flaky", "environment", "unknown")

# Normalization substitutions applied in order — collapse volatile tokens so the
# same underlying error produces one signature across runs.
_SUBS = [
    (re.compile(r"0x[0-9a-fA-F]+"), "0xADDR"),
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F-]{27}\b"), "UUID"),
    (re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}"), "TIMESTAMP"),
    (re.compile(r':\d+'), ":N"),                 # file:line, ports
    (re.compile(r'\bline \d+\b'), "line N"),
    (re.compile(r'\b\d+\b'), "N"),               # remaining bare numbers
    (re.compile(r"'[^']*'"), "'X'"),             # single-quoted literals
    (re.compile(r'"[^"]*"'), '"X"'),             # double-quoted literals
    (re.compile(r'\s+'), " "),
]


_ERR_LINE = re.compile(r"(\w*(Error|Exception|Timeout|Failed|AssertionError))\b", re.I)


def _last_meaningful_line(text: str) -> str:
    """The most error-like line of a traceback (prefer an exception line).

    Scans bottom-up for a line that names an error/exception (or a pytest ``E``
    marker); falls back to the last non-empty line. This keeps the signature
    anchored on the failure cause, not trailing location noise.
    """
    lines = [ln.strip() for ln in (text or "").strip().splitlines() if ln.strip()]
    if not lines:
        return ""
    for line in reversed(lines):
        stripped = line[2:].strip() if line.startswith("E ") else line
        if _ERR_LINE.search(stripped):
            return stripped
    return lines[-1]


def signature(message: str, traceback: str = "") -> str:
    """Return a stable, normalized signature for grouping like failures."""
    basis = _last_meaningful_line(traceback) or (message or "")
    sig = basis
    for pattern, repl in _SUBS:
        sig = pattern.sub(repl, sig)
    return sig.strip()[:200] or "unknown-failure"


def cluster(failures: list) -> list:
    """Group failure records by signature; return clusters sorted by size desc.

    Each failure dict may have: test_id, message, traceback, run_ts, browser.
    Each cluster: {signature, count, tests, runs, browsers, sample, sample_traceback}.
    """
    groups: dict = defaultdict(list)
    for f in failures:
        groups[signature(f.get("message", ""), f.get("traceback", ""))].append(f)

    clusters = []
    for sig, items in groups.items():
        clusters.append({
            "signature": sig,
            "count": len(items),
            "tests": sorted({i.get("test_id", "") for i in items if i.get("test_id")}),
            "runs": sorted({i.get("run_ts", "") for i in items if i.get("run_ts")}),
            "browsers": sorted({i.get("browser", "") for i in items if i.get("browser")}),
            "sample": (items[0].get("message") or "")[:300],
            "sample_traceback": (items[0].get("traceback") or "")[:1500],
        })
    # Rank by count desc, then by number of distinct tests affected.
    clusters.sort(key=lambda c: (c["count"], len(c["tests"])), reverse=True)
    return clusters


# ── Triage ───────────────────────────────────────────────────────────────────

def heuristic_triage(cluster_row: dict) -> dict:
    """Deterministic label for a cluster from its signature/sample (no LLM)."""
    text = f"{cluster_row.get('signature','')} {cluster_row.get('sample','')} " \
           f"{cluster_row.get('sample_traceback','')}".lower()
    multi_run = len(cluster_row.get("runs", [])) > 1

    if any(k in text for k in ("timeouterror", "timed out", "waiting for", "not stable")):
        # A timeout recurring across runs looks flaky/env; a one-off leans flaky.
        cat = "environment" if multi_run else "flaky"
        why = "Timeout / element-wait failure — often timing, animation, or a slow environment."
    elif any(k in text for k in ("net::", "econnrefused", "connection refused", "connection reset",
                                 "503", "502", "500", "gateway", "dns")):
        cat, why = "environment", "Network / backend error — the target or environment, not the test."
    elif any(k in text for k in ("importerror", "modulenotfound", "fixture", "not found",
                                 "attributeerror", "typeerror", "no such", "syntaxerror")):
        cat, why = "test_bug", "Import / fixture / API-usage error — the test or its harness is broken."
    elif "assertionerror" in text or "expect(" in text or "to_have" in text or "to_be" in text:
        cat, why = "product_bug", "Assertion failed — the app behaved differently than the test expects."
    else:
        cat, why = "unknown", "No dominant signal — needs a manual look."
    return {"category": cat, "explanation": why, "confidence": 0.4, "via": "offline"}


_SYSTEM = (
    "You triage automated-test failures. Given a cluster of similar failures "
    "(a normalized signature, a sample error, affected tests, how many runs it "
    "spans), classify the most likely root cause. Respond with JSON only."
)

_PROMPT = """A cluster of similar test failures:

Signature: {signature}
Occurrences: {count} across {run_count} run(s), {test_count} distinct test(s)
Affected tests: {tests}
Sample error:
{sample}
Sample traceback (truncated):
{traceback}

Classify the root cause. Choose ONE category from: {categories}.
  product_bug  — the application misbehaved (a real defect the test caught)
  test_bug     — the test/harness is wrong (bad selector, fixture, assertion, import)
  flaky        — nondeterministic (timing, animation, order/state) — passes sometimes
  environment  — infra/network/backend/config, not the app or the test
Return JSON with keys: "category", "explanation" (one or two sentences),
"confidence" (0.0-1.0), "suggested_action" (what to do next).
"""


def triage(cluster_row: dict, *, llm=None) -> dict:
    """Label one cluster. Uses the LLM when available, else the heuristic.

    Always returns {category, explanation, confidence, via, suggested_action?}.
    """
    offline = heuristic_triage(cluster_row)
    try:
        if llm is None:
            from utils.llm_client import LLMClient
            llm = LLMClient()
        if not getattr(llm, "available", False):
            return offline
        result = llm.complete_json(
            prompt=_PROMPT.format(
                signature=cluster_row.get("signature", ""),
                count=cluster_row.get("count", 0),
                run_count=len(cluster_row.get("runs", [])),
                test_count=len(cluster_row.get("tests", [])),
                tests=", ".join(cluster_row.get("tests", [])[:10]),
                sample=(cluster_row.get("sample", ""))[:500],
                traceback=(cluster_row.get("sample_traceback", ""))[:1500],
                categories=", ".join(CATEGORIES),
            ),
            system=_SYSTEM,
        )
        cat = str(result.get("category", "")).strip().lower()
        if cat not in CATEGORIES:
            return offline
        out = {
            "category": cat,
            "explanation": str(result.get("explanation", "")).strip() or offline["explanation"],
            "confidence": float(result.get("confidence", 0.6) or 0.6),
            "via": "llm",
        }
        if result.get("suggested_action"):
            out["suggested_action"] = str(result["suggested_action"]).strip()
        return out
    except Exception:
        return offline


def cluster_and_triage(failures: list, *, llm=None, top: Optional[int] = None) -> list:
    """Cluster failures and attach a triage label to each (ranked, optionally top-N)."""
    clusters = cluster(failures)
    if top is not None:
        clusters = clusters[:top]
    return [{**c, "triage": triage(c, llm=llm)} for c in clusters]
