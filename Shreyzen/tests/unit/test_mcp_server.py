"""
Unit tests for the MCP server tool layer (tools/mcp_server.py).

The tool functions are tested directly (no MCP dependency needed) — the ones
that touch heavy subsystems (runner, LLM, load engine) are exercised with those
modules monkeypatched, so no browser/LLM/subprocess runs.
"""

import sys

import pytest

from tools import mcp_server as m


# ── registry + graceful degradation ──────────────────────────────────────────

def test_tool_registry_is_populated_and_callable():
    assert set(m.TOOLS) >= {
        "discover_tests", "run_tests", "generate_test", "flaky_list",
        "flaky_diagnose", "quarantine", "heal", "impact_analysis",
        "run_load", "results", "doctor",
    }
    assert all(callable(fn) for fn in m.TOOLS.values())


def test_build_server_without_mcp_raises_helpful_error(monkeypatch):
    # Simulate `mcp` not being installed.
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", None)
    with pytest.raises(SystemExit, match="pip install mcp"):
        m.build_server()


# ── quarantine tool (real store, temp path via monkeypatch) ──────────────────

def test_quarantine_tool_add_list_remove(monkeypatch, tmp_path):
    from utils import quarantine
    monkeypatch.setattr(quarantine, "_PATH", tmp_path / "q.json")
    # diagnosis is offline (no history) → deterministic
    out = m.tool_quarantine("add", "tests/web/t.py::test_x[chromium]")
    assert out["ok"] and out["added"] is True
    assert m.tool_quarantine("list")["quarantine"][0]["test_id"] == "tests/web/t.py::test_x[chromium]"
    assert m.tool_quarantine("remove", "tests/web/t.py::test_x[chromium]")["removed"] is True


def test_quarantine_add_requires_test_id():
    assert m.tool_quarantine("add", "")["ok"] is False


# ── run_tests reuses FunctionalRunner (monkeypatched) ────────────────────────

def test_run_tests_summarizes_runner_snapshot(monkeypatch):
    from tools import functional_engine

    class _FakeRunner:
        def start(self, selection, target):
            self._sel = selection
            return "run_123"
        def is_active(self):
            return False   # finishes immediately
        def snapshot(self):
            return {"status": "completed", "passed_overall": True,
                    "counts": {"tests": 3, "passed": 3, "failed": 0},
                    "reports": {"html": "x.html"}, "error": None}

    monkeypatch.setattr(functional_engine, "FunctionalRunner", _FakeRunner)
    out = m.tool_run_tests(selection=["tests/web"], markers="smoke", timeout_s=30)
    assert out["run_id"] == "run_123"
    assert out["passed_overall"] is True
    assert out["counts"]["passed"] == 3


# ── run_load validates params before running ─────────────────────────────────

def test_run_load_rejects_bad_scenario():
    out = m.tool_run_load("not_a_scenario", "smoke")
    assert out["ok"] is False and "Unknown scenario" in out["error"]


def test_run_load_invokes_engine(monkeypatch):
    from load import engine
    captured = {}

    def fake_run_blocking(params, quiet=True):
        captured["endpoints"] = list(params.endpoints)
        captured["scenario"] = params.scenario
        return {"run_id": "load_1", "passed": True, "verdict": {"thresholds": {}}}

    monkeypatch.setattr(engine, "run_blocking", fake_run_blocking)
    out = m.tool_run_load("api_select", "stress", endpoints=["create", "read"])
    assert out["ok"] and out["run_id"] == "load_1"
    assert captured["endpoints"] == ["create", "read"]


# ── flaky_diagnose falls back to the offline heuristic ───────────────────────

def test_flaky_diagnose_returns_structured_result():
    dx = m.tool_flaky_diagnose("tests/web/x.py::test_y[chromium]")
    assert dx["test_id"] == "tests/web/x.py::test_y[chromium]"
    assert dx["category"] in __import__("utils.flaky_analysis", fromlist=["CATEGORIES"]).CATEGORIES
    assert "suggested_fix" in dx and dx["via"] == "offline"


# ── generate_test guards on no-LLM ───────────────────────────────────────────

def test_generate_test_requires_llm(monkeypatch):
    from utils import llm_client

    class _Unavailable:
        available = False

    monkeypatch.setattr(llm_client, "LLMClient", lambda *a, **k: _Unavailable())
    out = m.tool_generate_test("User cannot checkout with an empty cart")
    assert out["ok"] is False and "LLM" in out["error"]
