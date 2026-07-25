"""
Unit tests for flaky diagnosis + quarantine (Capability 2 / auto-quarantine).

Pure Python: the quarantine store runs against a temp JSON file, and diagnosis is
exercised via the deterministic offline heuristic plus an injected fake LLM.
"""

from utils import flaky_analysis as fa
from utils import quarantine


# ── quarantine store ─────────────────────────────────────────────────────────

def test_quarantine_add_list_remove(tmp_path):
    p = tmp_path / "q.json"
    assert quarantine.load(p) == {}
    assert quarantine.add("tests/web/t.py::test_x[chromium]", reason="timing",
                          category="timing", path=p) is True
    # Re-adding is an update, not a new entry.
    assert quarantine.add("tests/web/t.py::test_x[chromium]", category="timing", path=p) is False
    assert quarantine.ids(p) == {"tests/web/t.py::test_x[chromium]"}
    assert quarantine.is_quarantined("tests/web/t.py::test_x[chromium]", p)

    entries = quarantine.list_entries(p)
    assert entries[0]["test_id"] == "tests/web/t.py::test_x[chromium]"
    assert entries[0]["category"] == "timing"

    assert quarantine.remove("tests/web/t.py::test_x[chromium]", p) is True
    assert quarantine.remove("nope", p) is False
    assert quarantine.ids(p) == set()


def test_quarantine_add_preserves_added_timestamp(tmp_path):
    p = tmp_path / "q.json"
    quarantine.add("t1", reason="a", path=p)
    first_added = quarantine.load(p)["t1"]["added"]
    quarantine.add("t1", reason="b", path=p)      # update
    assert quarantine.load(p)["t1"]["added"] == first_added
    assert quarantine.load(p)["t1"]["reason"] == "b"


def test_quarantine_load_handles_garbage(tmp_path):
    p = tmp_path / "q.json"
    p.write_text("{not json", encoding="utf-8")
    assert quarantine.load(p) == {}


# ── offline diagnosis heuristics ─────────────────────────────────────────────

def _hist(outcomes, durations=None, browsers=None):
    durations = durations or [1.0] * len(outcomes)
    browsers = browsers or [""] * len(outcomes)
    return [{"outcome": o, "duration": d, "browser": b}
            for o, d, b in zip(outcomes, durations, browsers)]


def test_offline_timeout_text_is_timing():
    dx = fa.offline_diagnosis("t", _hist(["FAILED", "PASSED"]),
                              failure_text="TimeoutError: waiting for locator")
    assert dx["category"] == "timing"


def test_offline_selector_text():
    dx = fa.offline_diagnosis("t", _hist(["FAILED"]),
                              failure_text="strict mode: no element matches selector")
    assert dx["category"] == "selector"


def test_offline_network_text():
    dx = fa.offline_diagnosis("t", _hist(["FAILED"]),
                              failure_text="net::ERR_CONNECTION_RESET")
    assert dx["category"] == "network"


def test_offline_cross_browser_pattern():
    # chromium always fails, firefox always passes → cross_browser.
    hist = _hist(["FAILED", "FAILED", "PASSED", "PASSED"],
                 browsers=["chromium", "chromium", "firefox", "firefox"])
    assert fa.offline_diagnosis("t", hist)["category"] == "cross_browser"


def test_offline_high_variance_is_timing():
    hist = _hist(["PASSED", "FAILED", "PASSED", "FAILED"], durations=[0.2, 9.0, 0.3, 8.0])
    assert fa.offline_diagnosis("t", hist)["category"] == "timing"


def test_offline_alternating_is_order_dependency():
    hist = _hist(["PASSED", "FAILED", "PASSED", "FAILED"], durations=[1, 1, 1, 1])
    assert fa.offline_diagnosis("t", hist)["category"] == "order_dependency"


def test_offline_unknown_when_no_signal():
    hist = _hist(["PASSED", "PASSED"], durations=[1, 1])
    assert fa.offline_diagnosis("t", hist)["category"] == "unknown"


def test_offline_always_has_actionable_fix():
    for cat in fa.CATEGORIES:
        assert fa._FIX_HINTS[cat]


# ── diagnose(): LLM path + fallback ──────────────────────────────────────────

class _FakeLLM:
    def __init__(self, available=True, result=None):
        self.available = available
        self._result = result or {}
    def complete_json(self, *a, **k):
        return self._result


def test_diagnose_uses_llm_when_available():
    llm = _FakeLLM(result={"category": "network", "explanation": "backend 503s",
                           "confidence": 0.9, "suggested_fix": "retry the call"})
    dx = fa.diagnose("t", _hist(["FAILED", "PASSED"]), llm=llm)
    assert dx["via"] == "llm" and dx["category"] == "network" and dx["confidence"] == 0.9


def test_diagnose_falls_back_when_llm_unavailable():
    dx = fa.diagnose("t", _hist(["FAILED", "PASSED"]),
                     failure_text="TimeoutError", llm=_FakeLLM(available=False))
    assert dx["via"] == "offline" and dx["category"] == "timing"


def test_diagnose_rejects_invalid_llm_category():
    llm = _FakeLLM(result={"category": "banana", "explanation": "x"})
    dx = fa.diagnose("t", _hist(["FAILED", "PASSED"]), failure_text="net::ERR", llm=llm)
    assert dx["via"] == "offline" and dx["category"] == "network"
