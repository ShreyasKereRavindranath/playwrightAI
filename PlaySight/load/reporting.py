"""
Report generation for a load run — produces JUnit XML, JSON, and Allure results.

The Locust HTML report is emitted natively by the engine's `--html` flag; this
module turns the *normalized* per-endpoint statistics (see `normalize_row`) into
the other three formats so **every run yields html + junit + json + allure**,
matching the rest of the PlaySight framework's reporting story.

Pass/fail is threshold-driven per profile (see `load/catalog.py`):
an endpoint fails if its failure ratio exceeds `max_fail_ratio` OR its p95
response time exceeds `p95_budget_ms`.

Everything here is stdlib-only and takes plain dicts, so it is unit-testable
without Locust or a live target.
"""

import json
import re
import uuid
import xml.etree.ElementTree as ET
from html import escape
from pathlib import Path
from typing import Optional
from xml.dom import minidom

from load.catalog import PROFILES
from utils import run_context

AGGREGATED = "Aggregated"


# ── Normalized stat row ──────────────────────────────────────────────────────

def normalize_row(
    name: str, num_requests: int, num_failures: int,
    median_ms: float, avg_ms: float, p95_ms: float, rps: float,
    *, method: str = "", min_ms: float = 0.0, max_ms: float = 0.0,
) -> dict:
    """Build the canonical stat dict the report writers consume."""
    return {
        "name": name, "method": method,
        "num_requests": int(num_requests), "num_failures": int(num_failures),
        "median_ms": round(float(median_ms), 1), "avg_ms": round(float(avg_ms), 1),
        "p95_ms": round(float(p95_ms), 1), "rps": round(float(rps), 2),
        "min_ms": round(float(min_ms), 1), "max_ms": round(float(max_ms), 1),
    }


def _fail_ratio(row: dict) -> float:
    reqs = row["num_requests"]
    return (row["num_failures"] / reqs) if reqs else 0.0


def evaluate(rows: list[dict], profile: str) -> dict:
    """
    Judge each endpoint against the profile's thresholds.

    Returns a dict with an overall `passed` bool and a per-endpoint breakdown
    (excluding the Aggregated row, which is reported separately as the summary).
    """
    prof = PROFILES.get(profile, PROFILES["custom"])
    endpoints = []
    all_passed = True
    for row in rows:
        if row["name"] == AGGREGATED:
            continue
        fr = _fail_ratio(row)
        reasons = []
        if fr > prof.max_fail_ratio:
            reasons.append(f"fail ratio {fr:.2%} > {prof.max_fail_ratio:.2%}")
        if row["p95_ms"] > prof.p95_budget_ms:
            reasons.append(f"p95 {row['p95_ms']:.0f}ms > {prof.p95_budget_ms}ms budget")
        passed = not reasons
        all_passed = all_passed and passed
        endpoints.append({**row, "passed": passed, "fail_ratio": round(fr, 4),
                          "reasons": reasons})
    return {
        "passed": all_passed,
        "profile": profile,
        "thresholds": {"max_fail_ratio": prof.max_fail_ratio, "p95_budget_ms": prof.p95_budget_ms},
        "endpoints": endpoints,
    }


def _aggregate(rows: list[dict]) -> Optional[dict]:
    for row in rows:
        if row["name"] == AGGREGATED:
            return row
    return None


# ── JSON ─────────────────────────────────────────────────────────────────────

def write_json(run_dir: Path, meta: dict, rows: list[dict], verdict: dict) -> Path:
    agg = _aggregate(rows) or {}
    out = {
        "meta": meta,
        "passed": verdict["passed"],
        "thresholds": verdict["thresholds"],
        "summary": {
            "total_requests": agg.get("num_requests", 0),
            "total_failures": agg.get("num_failures", 0),
            "fail_ratio": round(_fail_ratio(agg), 4) if agg else 0.0,
            "avg_ms": agg.get("avg_ms", 0.0),
            "p95_ms": agg.get("p95_ms", 0.0),
            "rps": agg.get("rps", 0.0),
        },
        "endpoints": verdict["endpoints"],
    }
    path = run_dir / "results.json"
    path.write_text(json.dumps(out, indent=2))
    return path


# ── JUnit XML ────────────────────────────────────────────────────────────────

def write_junit(run_dir: Path, meta: dict, verdict: dict) -> Path:
    suite_name = f"{meta.get('scenario', '?')}/{meta.get('profile', '?')}"
    duration = float(meta.get("duration_s", 0.0))
    endpoints = verdict["endpoints"]
    failures = sum(1 for e in endpoints if not e["passed"])

    testsuites = ET.Element("testsuites", {
        "name": "PlaySight Load", "tests": str(len(endpoints)),
        "failures": str(failures), "time": f"{duration:.2f}",
    })
    testsuite = ET.SubElement(testsuites, "testsuite", {
        "name": suite_name, "tests": str(len(endpoints)),
        "failures": str(failures), "time": f"{duration:.2f}",
    })
    # Embed run context (user / system / timestamp) as JUnit properties.
    context = meta.get("context") or {}
    if context:
        props = ET.SubElement(testsuite, "properties")
        for key, val in context.items():
            ET.SubElement(props, "property", {"name": str(key), "value": str(val)})
    per = duration / len(endpoints) if endpoints else 0.0
    for e in endpoints:
        case = ET.SubElement(testsuite, "testcase", {
            "name": e["name"],
            "classname": f"playsight.load.{meta.get('scenario', 'load')}",
            "time": f"{per:.2f}",
        })
        if not e["passed"]:
            failure = ET.SubElement(case, "failure", {
                "message": "; ".join(e["reasons"]) or "threshold breach",
            })
            failure.text = (
                f"{e['name']}: requests={e['num_requests']} failures={e['num_failures']} "
                f"p95={e['p95_ms']}ms avg={e['avg_ms']}ms rps={e['rps']}"
            )
    path = run_dir / "junit.xml"
    xml = minidom.parseString(ET.tostring(testsuites)).toprettyxml(indent="  ")
    path.write_text(xml)
    return path


# ── Allure results ───────────────────────────────────────────────────────────

def write_allure(run_dir: Path, meta: dict, verdict: dict) -> Path:
    """Emit allure2 *-result.json files (view with `allure serve <dir>`)."""
    allure_dir = run_dir / "allure-results"
    allure_dir.mkdir(parents=True, exist_ok=True)
    start = int(float(meta.get("start_epoch_ms", 0)))
    stop = int(float(meta.get("stop_epoch_ms", start)))
    scenario = meta.get("scenario", "load")
    profile = meta.get("profile", "custom")

    for e in verdict["endpoints"]:
        result = {
            "uuid": str(uuid.uuid4()),
            "historyId": f"{scenario}:{profile}:{e['name']}",
            "name": e["name"],
            "fullName": f"PlaySight Load / {scenario} / {e['name']}",
            "status": "passed" if e["passed"] else "failed",
            "statusDetails": {"message": "; ".join(e["reasons"])} if not e["passed"] else {},
            "stage": "finished",
            "start": start,
            "stop": stop,
            "labels": [
                {"name": "suite", "value": f"Load · {profile}"},
                {"name": "feature", "value": scenario},
                {"name": "story", "value": e["name"]},
                {"name": "severity", "value": "critical" if not e["passed"] else "normal"},
                {"name": "framework", "value": "locust"},
            ],
            "parameters": [
                {"name": "requests", "value": str(e["num_requests"])},
                {"name": "failures", "value": str(e["num_failures"])},
                {"name": "fail_ratio", "value": f"{e['fail_ratio']:.2%}"},
                {"name": "p95_ms", "value": str(e["p95_ms"])},
                {"name": "avg_ms", "value": str(e["avg_ms"])},
                {"name": "rps", "value": str(e["rps"])},
            ],
        }
        (allure_dir / f"{result['uuid']}-result.json").write_text(json.dumps(result, indent=2))

    # Environment panel for the Allure report (incl. who/where/when).
    env_lines = [
        f"Scenario={scenario}",
        f"Profile={profile}",
        f"Peak_VUs={meta.get('users', '?')}",
        f"Duration_s={meta.get('duration_s', '?')}",
        f"Host={meta.get('host', '?')}",
    ]
    for key, val in (meta.get("context") or {}).items():
        env_lines.append(f"{str(key).title().replace('_', '')}={val}")
    (allure_dir / "environment.properties").write_text("\n".join(env_lines) + "\n")
    return allure_dir


# ── Run-context panel for the (native Locust) HTML report ────────────────────

def inject_context_panel(run_dir: Path, meta: dict) -> Optional[Path]:
    """Prepend a user/system/LLM context panel to the Locust HTML report.

    Locust's native report.html has no notion of who/where/when a run happened,
    so we splice in the captured run context (see utils.run_context). Best-effort
    and idempotent — never raises, and skips if the report or context is absent.
    """
    report = Path(run_dir) / "report.html"
    context = meta.get("context") or {}
    if not report.exists() or not context:
        return None
    try:
        html = report.read_text()
    except OSError:
        return None
    if "playsight-context" in html:  # already injected
        return report

    # Prefix the report title with the load type (scenario/profile) instead of a
    # generic Locust title, so reports are identifiable by what they tested.
    scenario = meta.get("scenario", "load")
    profile = meta.get("profile", "custom")
    title = f"LOAD_{scenario}_{profile} — {meta.get('run_id', '')}".strip(" —")
    html = re.sub(r"<title>.*?</title>", f"<title>{escape(title)}</title>", html,
                  count=1, flags=re.DOTALL | re.IGNORECASE)

    rows = "".join(
        f"<tr><th style='text-align:left;padding:4px 12px 4px 0;color:#64748b;"
        f"font-weight:600;white-space:nowrap'>{escape(label)}</th>"
        f"<td style='padding:4px 0'>{escape(value)}</td></tr>"
        for label, value in run_context.as_rows(context)
    )
    panel = (
        "<section class='playsight-context' style=\"font-family:system-ui,-apple-system,"
        "sans-serif;margin:16px;padding:16px 20px;border:1px solid #e2e8f0;border-radius:12px;"
        "background:#f8fafc\">"
        "<h3 style='margin:0 0 10px;font-size:14px;color:#1e293b'>🎭 PlaySight — Run context "
        "<span style='font-weight:400;color:#64748b;font-size:12px'>(user · system · LLM)</span></h3>"
        f"<table style='border-collapse:collapse;font-size:12.5px;color:#1e293b'>{rows}</table>"
        "</section>"
    )
    if "<body>" in html:
        html = html.replace("<body>", "<body>\n" + panel, 1)
    elif "</body>" in html:
        html = html.replace("</body>", panel + "\n</body>", 1)
    else:
        html = panel + html
    report.write_text(html)
    return report


# ── Orchestration ────────────────────────────────────────────────────────────

def write_reports(run_dir: Path, meta: dict, rows: list[dict]) -> dict:
    """Write json + junit + allure for a run; return the verdict dict."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    verdict = evaluate(rows, meta.get("profile", "custom"))
    write_json(run_dir, meta, rows, verdict)
    write_junit(run_dir, meta, verdict)
    write_allure(run_dir, meta, verdict)
    inject_context_panel(run_dir, meta)  # user/system/LLM panel in the HTML report
    return verdict
