"""
Shreyzen MCP server — drive the framework from any MCP client (Claude Code,
Cursor, …).

Exposes the framework's capabilities as MCP tools so an agent can run tests,
generate + self-validate a test from natural language, diagnose/quarantine flaky
tests, apply self-heal fixes, run impact analysis, run a load profile, and query
run history — conversationally, without leaving the editor.

The tool *logic* lives in the `tool_*` functions below (plain, JSON-returning,
unit-testable with no MCP dependency). FastMCP is a thin wrapper registered in
`build_server()`; it's an optional dependency (`pip install mcp`), so importing
this module never fails just because MCP isn't installed.

Run it (stdio transport):
    python -m tools.mcp_server

Register with Claude Code (example .mcp.json):
    {"mcpServers": {"shreyzen": {"command": "python", "args": ["-m", "tools.mcp_server"],
                                  "cwd": "/path/to/Shreyzen"}}}
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_POLL_INTERVAL_S = 1.5
_DEFAULT_RUN_TIMEOUT_S = 900  # 15 min ceiling for a blocking test/load run


# ── Tool logic (no MCP dependency — unit-testable) ───────────────────────────

def tool_discover_tests() -> dict:
    """List every collectable test, grouped by layer (api/web/mobile/unit)."""
    from tools import functional_engine
    return functional_engine.discover_tests()


def tool_run_tests(selection: list | None = None, markers: str = "",
                   base_url: str = "", browser: str = "chromium",
                   headless: bool = True, timeout_s: int = _DEFAULT_RUN_TIMEOUT_S) -> dict:
    """Run the selected tests (or all, or by marker) and return a pass/fail summary.

    Reuses the real functional runner, so artifacts, reports, and the results DB
    are populated exactly as a Studio run would be.
    """
    import time
    from tools import functional_engine
    from utils import quarantine  # noqa: F401 (ensures module importable)

    sel = selection or ["tests"]
    target = {"base_url": base_url, "api_target": "mock", "api_url": "",
              "mobile_device": "Pixel 5", "browser": browser,
              "headless": headless, "markers": markers}
    runner = functional_engine.FunctionalRunner()
    run_id = runner.start(sel, target)

    deadline = time.monotonic() + max(30, timeout_s)
    while runner.is_active() and time.monotonic() < deadline:
        time.sleep(_POLL_INTERVAL_S)
    snap = runner.snapshot()
    return {
        "run_id": run_id,
        "status": snap.get("status"),
        "passed_overall": snap.get("passed_overall"),
        "counts": snap.get("counts", {}),
        "reports": snap.get("reports", {}),
        "error": snap.get("error"),
    }


def tool_generate_test(scenario: str, page: str = "", feature: str = "") -> dict:
    """Generate a Page Object + test from a plain-English scenario, then validate
    it with `pytest --collect-only` and let the LLM self-correct (Capability 28)."""
    from config.config import Config
    from utils.llm_client import LLMClient
    from tools import generate_test as gt
    from utils import generation_validator as gv

    llm = LLMClient()
    if not llm.available:
        return {"ok": False, "error": "No LLM provider configured."}

    scenario = (scenario or "").strip()
    if not scenario:
        return {"ok": False, "error": "A scenario description is required."}
    page = (page or gt._infer_page(scenario)).strip()
    feature = (feature or page).strip()

    def _strip(code):
        t = (code or "").strip()
        if t.startswith("```"):
            t = "\n".join(l for l in t.splitlines() if not l.strip().startswith("```")).strip()
        return t

    po = _strip(llm.complete(
        prompt=gt._PAGE_OBJECT_PROMPT.format(scenario=scenario, page_name=page),
        system=gt._SYSTEM, max_tokens=800))
    test = _strip(llm.complete(
        prompt=gt._TEST_PROMPT.format(scenario=scenario, page_fixture=f"{page}_page",
                                      feature=feature, scenario_snake_case=gt._to_snake(scenario)),
        system=gt._SYSTEM, max_tokens=800))

    written, validation = [], None
    root = Path(__file__).resolve().parent.parent
    files = []
    if po and not (root / f"pages/{page}_page.py").exists():
        (root / f"pages/{page}_page.py").write_text(po, encoding="utf-8")
        written.append(f"pages/{page}_page.py")
        files.append(gv.GenFile(path=f"pages/{page}_page.py", code=po, kind="page"))
    if test and not (root / f"tests/web/test_{feature}.py").exists():
        dest = root / f"tests/web/test_{feature}.py"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(test, encoding="utf-8")
        written.append(f"tests/web/test_{feature}.py")
        files.append(gv.GenFile(path=f"tests/web/test_{feature}.py", code=test, kind="test"))

    if files and Config.NL_REPAIR_ENABLED:
        outcome = gv.repair_generation(files, gv.make_llm_repair_fn(llm),
                                       max_attempts=Config.NL_REPAIR_ATTEMPTS)
        validation = {"ok": outcome.ok, "repairs": outcome.repairs,
                      "error": outcome.last_error if not outcome.ok else ""}

    return {"ok": True, "page": page, "feature": feature,
            "written": written, "validation": validation}


def tool_flaky_list() -> dict:
    """List flaky tests (both passed and failed within the recent window)."""
    from utils.flakiness_tracker import FlakinessTracker
    from utils import quarantine
    q = quarantine.ids()
    flaky = FlakinessTracker().get_flaky_tests()
    return {"flaky": [{**f, "quarantined": f["test_id"] in q} for f in flaky]}


def tool_flaky_diagnose(test_id: str) -> dict:
    """Diagnose why a test is flaky (AI + heuristic): category, why, suggested fix."""
    from utils.flakiness_tracker import FlakinessTracker
    from utils import flaky_analysis
    tracker = FlakinessTracker()
    return {"test_id": test_id, **flaky_analysis.diagnose(test_id, tracker.get_history(test_id))}


def tool_quarantine(action: str = "list", test_id: str = "") -> dict:
    """Manage the quarantine list. action ∈ list | add | remove."""
    from utils import quarantine
    from utils.flakiness_tracker import FlakinessTracker
    from utils import flaky_analysis
    if action == "add":
        if not test_id:
            return {"ok": False, "error": "test_id required"}
        dx = flaky_analysis.diagnose(test_id, FlakinessTracker().get_history(test_id))
        new = quarantine.add(test_id, reason=dx["explanation"], category=dx["category"],
                             confidence=dx["confidence"], suggested_fix=dx["suggested_fix"],
                             source=f"mcp:{dx['via']}")
        return {"ok": True, "added": new, "diagnosis": dx}
    if action == "remove":
        return {"ok": True, "removed": quarantine.remove(test_id)}
    return {"quarantine": quarantine.list_entries()}


def tool_heal(action: str = "status", open_pr: bool = False) -> dict:
    """Self-heal → Page Object. action ∈ status | apply | pr."""
    from utils import heal_pr
    entries = heal_pr.load_pending()
    report = heal_pr.plan(entries, write=(action in ("apply", "pr")))
    result = {"pending": len(entries),
              "results": [{"intent": r.intent, "status": r.status, "path": r.path,
                           "original": r.original, "healed": r.healed} for r in report.results]}
    if action == "pr" or open_pr:
        result["pr"] = heal_pr.open_pr(report)
    return result


def tool_impact_analysis(base: str = "HEAD") -> dict:
    """Which tests a change can break, from the real import graph (Capability 21)."""
    from utils import test_impact
    res = test_impact.analyze_impact(base)
    return {
        "base": base,
        "changed_files": list(res.changed_files),
        "run_all": res.run_all,
        "impacted_tests": test_impact.pytest_targets(base),
        "reason": getattr(res, "reason", ""),
    }


def tool_run_load(scenario: str, profile: str = "smoke", endpoints: list | None = None,
                  users: int | None = None, duration: int | None = None) -> dict:
    """Run a load profile (incl. api_select endpoint selection) and return the verdict."""
    from load import catalog, engine
    try:
        params = catalog.resolve_params(scenario, profile, users=users,
                                        duration=duration, endpoints=endpoints)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    result = engine.run_blocking(params, quiet=True)
    return {"ok": True, "run_id": result.get("run_id"), "passed": result.get("passed"),
            "summary": (result.get("verdict") or {}).get("thresholds"),
            "endpoints": list(params.endpoints)}


def tool_results(kind: str = "", limit: int = 20) -> dict:
    """Recent run history + totals from the central results DB (Capability 23)."""
    from utils import results_db
    return {"stats": results_db.stats(),
            "runs": results_db.list_runs(kind=kind or None, limit=limit)}


def tool_cluster_failures(top: int = 10, use_ai: bool = False, limit: int = 200) -> dict:
    """Cluster recent test failures by root cause and triage each (Capability 30)."""
    from utils import failure_cluster
    from utils.failure_store import FailureStore
    failures = FailureStore().recent(limit=limit)
    llm = None
    if not use_ai:
        class _NoLLM:
            available = False
        llm = _NoLLM()
    return {"failures": len(failures),
            "clusters": failure_cluster.cluster_and_triage(failures, llm=llm, top=top)}


def tool_doctor() -> dict:
    """Environment health check (same checks as `python -m tools.doctor`)."""
    from tools import doctor
    return {"checks": [c().__dict__ for c in doctor.CHECKS]}


# Registry: (name, fn) — used for both MCP registration and tests.
TOOLS = {
    "discover_tests": tool_discover_tests,
    "run_tests": tool_run_tests,
    "generate_test": tool_generate_test,
    "flaky_list": tool_flaky_list,
    "flaky_diagnose": tool_flaky_diagnose,
    "quarantine": tool_quarantine,
    "heal": tool_heal,
    "impact_analysis": tool_impact_analysis,
    "run_load": tool_run_load,
    "results": tool_results,
    "cluster_failures": tool_cluster_failures,
    "doctor": tool_doctor,
}


# ── FastMCP wrapper (optional dependency) ────────────────────────────────────

def build_server():
    """Construct the FastMCP server with every tool registered. Raises a helpful
    error if the `mcp` package isn't installed."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - exercised only without mcp
        raise SystemExit(
            "The MCP server needs the 'mcp' package. Install it with:\n"
            "  pip install mcp\n"
            "(it's an optional dependency; the rest of the framework doesn't need it.)"
        ) from exc

    mcp = FastMCP("shreyzen")
    # Register each tool; FastMCP reads the type hints + docstring for the schema.
    for name, fn in TOOLS.items():
        mcp.tool(name=name)(fn)
    return mcp


def main() -> None:  # pragma: no cover - entrypoint
    build_server().run()  # stdio transport


if __name__ == "__main__":  # pragma: no cover
    main()
