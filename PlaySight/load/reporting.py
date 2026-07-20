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
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional
from xml.dom import minidom

from load.catalog import PROFILES

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


# ── Orchestration ────────────────────────────────────────────────────────────

def write_reports(run_dir: Path, meta: dict, rows: list[dict]) -> dict:
    """Write json + junit + allure for a run; return the verdict dict."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    verdict = evaluate(rows, meta.get("profile", "custom"))
    write_json(run_dir, meta, rows, verdict)
    write_junit(run_dir, meta, verdict)
    write_allure(run_dir, meta, verdict)
    return verdict
