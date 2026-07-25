"""
Unit tests for failure clustering + triage (Capability 30).

Store runs against a temp SQLite file; signature/cluster/heuristic are pure;
the LLM path uses an injected fake.
"""

from utils import failure_cluster as fc
from utils.failure_store import FailureStore


# ── FailureStore ─────────────────────────────────────────────────────────────

def test_failure_store_record_and_read(tmp_path):
    store = FailureStore(db_path=tmp_path / "f.db")
    store.record("tests/web/t.py::test_a[chromium]", message="AssertionError: x != y",
                 traceback="...\nAssertionError: x != y", run_ts="R1", browser="chromium")
    store.record("tests/web/t.py::test_b[chromium]", message="TimeoutError",
                 traceback="TimeoutError: waiting for locator", run_ts="R1")
    recent = store.recent()
    assert len(recent) == 2
    assert store.for_run("R1")[0]["run_ts"] == "R1"
    assert store.for_run("nope") == []


def test_failure_store_truncates_long_traceback(tmp_path):
    store = FailureStore(db_path=tmp_path / "f.db")
    store.record("t::x", traceback="A" * 10_000, run_ts="R1")
    assert len(store.recent()[0]["traceback"]) <= 4000


# ── signature normalization ──────────────────────────────────────────────────

def test_signature_collapses_volatile_tokens():
    a = fc.signature("", "  File 'x.py', line 42\nAssertionError: got 17, expected 4")
    b = fc.signature("", "  File 'y.py', line 99\nAssertionError: got 3, expected 8")
    assert a == b   # line numbers + quoted names + numbers normalized away


def test_signature_distinguishes_different_errors():
    assert fc.signature("TimeoutError: waiting") != fc.signature("AssertionError: nope")


def test_signature_uses_last_traceback_line_over_message():
    sig = fc.signature("generic failure", "traceback...\nValueError: bad thing")
    assert "ValueError" in sig


# ── clustering ───────────────────────────────────────────────────────────────

def _fail(test, tb, run="R1"):
    return {"test_id": test, "message": tb.splitlines()[-1], "traceback": tb, "run_ts": run}


def test_cluster_groups_and_ranks():
    failures = [
        _fail("t1", "AssertionError: got 1, expected 2"),
        _fail("t2", "AssertionError: got 5, expected 9"),   # same signature as t1
        _fail("t3", "TimeoutError: waiting for locator"),
    ]
    clusters = fc.cluster(failures)
    assert clusters[0]["count"] == 2                     # assertion cluster ranks first
    assert clusters[0]["tests"] == ["t1", "t2"]
    assert len(clusters) == 2


def test_cluster_tracks_runs_and_browsers():
    # Same normalized signature (only the quoted selector differs) → one cluster.
    failures = [
        {"test_id": "t1", "message": "TimeoutError",
         "traceback": "TimeoutError: waiting for locator '#a'",
         "run_ts": "R1", "browser": "chromium"},
        {"test_id": "t1", "message": "TimeoutError",
         "traceback": "TimeoutError: waiting for locator '#b'",
         "run_ts": "R2", "browser": "firefox"},
    ]
    c = fc.cluster(failures)[0]
    assert c["count"] == 2 and c["runs"] == ["R1", "R2"]
    assert c["browsers"] == ["chromium", "firefox"]


# ── heuristic triage ─────────────────────────────────────────────────────────

def _clus(sig, sample="", runs=("R1",)):
    return {"signature": sig, "sample": sample, "sample_traceback": "", "runs": list(runs), "tests": []}


def test_heuristic_assertion_is_product_bug():
    assert fc.heuristic_triage(_clus("AssertionError: X"))["category"] == "product_bug"


def test_heuristic_import_is_test_bug():
    assert fc.heuristic_triage(_clus("ModuleNotFoundError: no module"))["category"] == "test_bug"
    assert fc.heuristic_triage(_clus("fixture 'foo' not found"))["category"] == "test_bug"


def test_heuristic_network_is_environment():
    assert fc.heuristic_triage(_clus("net::ERR_CONNECTION_REFUSED"))["category"] == "environment"


def test_heuristic_timeout_flaky_vs_environment():
    assert fc.heuristic_triage(_clus("TimeoutError waiting", runs=("R1",)))["category"] == "flaky"
    assert fc.heuristic_triage(_clus("TimeoutError waiting", runs=("R1", "R2")))["category"] == "environment"


# ── triage() LLM path + fallback ─────────────────────────────────────────────

class _FakeLLM:
    def __init__(self, available=True, result=None):
        self.available = available
        self._result = result or {}
    def complete_json(self, *a, **k):
        return self._result


def test_triage_uses_llm_when_available():
    llm = _FakeLLM(result={"category": "flaky", "explanation": "races",
                           "confidence": 0.8, "suggested_action": "add explicit wait"})
    out = fc.triage(_clus("TimeoutError"), llm=llm)
    assert out["via"] == "llm" and out["category"] == "flaky"
    assert out["suggested_action"] == "add explicit wait"


def test_triage_falls_back_on_unavailable_or_invalid():
    assert fc.triage(_clus("AssertionError: X"), llm=_FakeLLM(available=False))["via"] == "offline"
    bad = _FakeLLM(result={"category": "banana"})
    assert fc.triage(_clus("net::ERR"), llm=bad) == fc.heuristic_triage(_clus("net::ERR"))


def test_cluster_and_triage_attaches_labels_and_respects_top():
    failures = [_fail(f"t{i}", "AssertionError: x") for i in range(3)] + \
               [_fail("tn", "TimeoutError: waiting")]
    out = fc.cluster_and_triage(failures, llm=_FakeLLM(available=False), top=1)
    assert len(out) == 1 and out[0]["count"] == 3
    assert out[0]["triage"]["category"] == "product_bug"
