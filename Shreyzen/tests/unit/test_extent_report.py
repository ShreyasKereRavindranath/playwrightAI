"""
Unit tests for the Extent-style HTML report builder.

The builder is a pure function of its inputs, so these tests need no pytest
session, browser, or filesystem (except the write_report round-trip).
"""

from utils import extent_report as er


# ── normalize_status ─────────────────────────────────────────────────────────

def test_normalize_status_maps_outcomes():
    assert er.normalize_status("passed") == "pass"
    assert er.normalize_status("pass") == "pass"
    assert er.normalize_status("skipped") == "skip"
    assert er.normalize_status("xfailed") == "skip"
    assert er.normalize_status("failed") == "fail"
    assert er.normalize_status("error") == "fail"
    assert er.normalize_status("") == "fail"          # unknown → fail (safe default)


# ── summarize ────────────────────────────────────────────────────────────────

def _sample():
    return [
        {"name": "a", "status": "passed", "duration_s": 1.0, "category": "web"},
        {"name": "b", "status": "failed", "duration_s": 0.5, "category": "api",
         "message": "AssertionError"},
        {"name": "c", "status": "skipped", "duration_s": 0.0, "category": "web"},
    ]


def test_summarize_counts_and_pass_rate():
    s = er.summarize(_sample())
    assert s == {
        "total": 3, "passed": 1, "failed": 1, "skipped": 1,
        "pass_rate": 50.0, "sum_duration_s": 1.5,
    }


def test_summarize_pass_rate_excludes_skips():
    # 2 pass, 0 fail, 5 skip → 100% (skips don't drag the rate down).
    tests = [{"status": "passed"}] * 2 + [{"status": "skipped"}] * 5
    assert er.summarize(tests)["pass_rate"] == 100.0


def test_summarize_empty_is_safe():
    s = er.summarize([])
    assert s["total"] == 0 and s["pass_rate"] == 0.0


# ── build_html ───────────────────────────────────────────────────────────────

def test_build_html_is_self_contained_and_complete():
    h = er.build_html(_sample(), {"title": "T", "generated_at": "now"})
    assert h.startswith("<!DOCTYPE html>")
    assert "<style>" in h and "<script>" in h   # inline CSS + JS, no external deps
    assert "donut" in h
    assert "a" in h and "b" in h and "c" in h
    assert "AssertionError" in h                # failure message rendered
    assert "data-status='fail'" in h


def test_build_html_escapes_untrusted_content():
    h = er.build_html([{"name": "<script>alert(1)</script>", "status": "pass"}], {})
    assert "<script>alert(1)</script>" not in h
    assert "&lt;script&gt;" in h


def test_build_html_renders_context_rows():
    h = er.build_html(_sample(), {"context": {"Run": "2026-07-25", "Browser": "chromium"}})
    assert "2026-07-25" in h and "chromium" in h


def test_write_report_round_trip(tmp_path):
    out = er.write_report(tmp_path / "sub" / "extent.html", _sample(), {"title": "T"})
    assert out.exists()
    assert out.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")
