"""
Extent-style consolidated HTML report.

ExtentReports is a Java/.NET library with no Python/pytest port, so this module
produces its Python equivalent: a single self-contained, interactive HTML file
with a pass/fail/skip donut, a category/suite breakdown, and filterable per-test
cards (status, duration, category, message). It complements — never replaces —
the existing pytest-html / Allure / JUnit outputs.

Everything here is stdlib-only and the HTML builder is a **pure function** of its
inputs (no I/O), so it is fully unit-testable without pytest or a browser.

Opt in with EXTENT_REPORT=true in config/.env. Generated at the end of a
functional pytest session (tests/conftest.py) and after each load run
(load/reporting.py).
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import Optional

# Canonical statuses and their colours (kept small + explicit).
_STATUS = {
    "pass": ("#22c55e", "PASS"),
    "fail": ("#ef4444", "FAIL"),
    "skip": ("#f59e0b", "SKIP"),
}


def normalize_status(raw: str) -> str:
    """Map any pytest/locust outcome string to one of pass|fail|skip."""
    r = (raw or "").strip().lower()
    if r in ("pass", "passed"):
        return "pass"
    if r in ("skip", "skipped", "xfail", "xfailed"):
        return "skip"
    return "fail"  # failed, error, and anything unknown are treated as failures


def summarize(tests: list[dict]) -> dict:
    """Count outcomes across a list of test records (pure)."""
    counts = {"pass": 0, "fail": 0, "skip": 0}
    total_duration = 0.0
    for t in tests:
        counts[normalize_status(t.get("status", "fail"))] += 1
        total_duration += float(t.get("duration_s", 0.0) or 0.0)
    total = len(tests)
    passed = counts["pass"]
    denom = passed + counts["fail"]  # pass rate excludes skips
    pass_rate = round(100.0 * passed / denom, 1) if denom else 0.0
    return {
        "total": total, "passed": passed, "failed": counts["fail"],
        "skipped": counts["skip"], "pass_rate": pass_rate,
        "sum_duration_s": round(total_duration, 2),
    }


def _by_category(tests: list[dict]) -> dict[str, dict]:
    """Group counts by the test's category (marker/layer)."""
    cats: dict[str, dict] = {}
    for t in tests:
        cat = t.get("category") or "uncategorized"
        bucket = cats.setdefault(cat, {"pass": 0, "fail": 0, "skip": 0})
        bucket[normalize_status(t.get("status", "fail"))] += 1
    return dict(sorted(cats.items()))


def _donut(summary: dict) -> str:
    """A pure-CSS conic-gradient donut for pass/fail/skip proportions."""
    p, f, s = summary["passed"], summary["failed"], summary["skipped"]
    total = max(1, p + f + s)
    p_deg = 360 * p / total
    f_deg = 360 * (p + f) / total
    grad = (f"conic-gradient(#22c55e 0deg {p_deg:.1f}deg,"
            f"#ef4444 {p_deg:.1f}deg {f_deg:.1f}deg,"
            f"#f59e0b {f_deg:.1f}deg 360deg)")
    return (
        f"<div class='donut' style=\"background:{grad}\">"
        f"<div class='donut-hole'><span class='donut-pct'>{summary['pass_rate']:.0f}%</span>"
        f"<span class='donut-lbl'>pass</span></div></div>"
    )


def _test_card(t: dict) -> str:
    status = normalize_status(t.get("status", "fail"))
    colour, label = _STATUS[status]
    name = html.escape(str(t.get("name") or t.get("nodeid") or "test"))
    cat = html.escape(str(t.get("category") or "—"))
    dur = float(t.get("duration_s", 0.0) or 0.0)
    msg = str(t.get("message") or "")
    msg_html = (f"<pre class='msg'>{html.escape(msg)}</pre>" if msg else "")
    return (
        f"<div class='tcard' data-status='{status}'>"
        f"<div class='tcard-h'>"
        f"<span class='pill' style=\"background:{colour}\">{label}</span>"
        f"<span class='tname'>{name}</span>"
        f"<span class='tmeta'>{cat} · {dur:.2f}s</span>"
        f"</div>{msg_html}</div>"
    )


def build_html(tests: list[dict], meta: Optional[dict] = None) -> str:
    """Return a complete, self-contained HTML report string (pure function)."""
    meta = meta or {}
    summary = summarize(tests)
    title = html.escape(str(meta.get("title", "Shreyzen — Extent Report")))
    generated = html.escape(str(meta.get("generated_at", "")))

    cards = "".join(_test_card(t) for t in tests) or \
        "<div class='empty'>No tests recorded.</div>"

    cat_rows = "".join(
        f"<tr><td>{html.escape(cat)}</td><td>{c['pass']}</td>"
        f"<td>{c['fail']}</td><td>{c['skip']}</td></tr>"
        for cat, c in _by_category(tests).items()
    ) or "<tr><td colspan='4' class='empty'>—</td></tr>"

    context = meta.get("context") or {}
    ctx_rows = "".join(
        f"<tr><th>{html.escape(str(k))}</th><td>{html.escape(str(v))}</td></tr>"
        for k, v in context.items()
    )
    ctx_block = (f"<table class='ctx'>{ctx_rows}</table>" if ctx_rows else "")

    dur = summary["sum_duration_s"]
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:#f0f4f8;color:#1e293b;padding:24px}}
h1{{font-size:20px;margin-bottom:2px}}.sub{{color:#64748b;font-size:13px;margin-bottom:18px}}
.top{{display:flex;gap:24px;flex-wrap:wrap;align-items:center;background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:20px 24px;margin-bottom:18px}}
.cards{{display:flex;gap:14px;flex-wrap:wrap}}
.stat{{background:#fff;border:1px solid #e2e8f0;border-radius:12px;padding:14px 20px;min-width:96px}}
.stat .n{{font-size:26px;font-weight:800}}.stat .l{{font-size:12px;color:#64748b}}
.donut{{width:120px;height:120px;border-radius:50%;display:flex;align-items:center;justify-content:center}}
.donut-hole{{width:82px;height:82px;background:#fff;border-radius:50%;display:flex;flex-direction:column;align-items:center;justify-content:center}}
.donut-pct{{font-size:22px;font-weight:800}}.donut-lbl{{font-size:11px;color:#64748b}}
table{{border-collapse:collapse;width:100%;background:#fff;border:1px solid #e2e8f0;border-radius:12px;overflow:hidden;font-size:13px}}
th,td{{text-align:left;padding:8px 12px;border-bottom:1px solid #f1f5f9}}
th{{color:#64748b;font-weight:600}}
.ctx{{max-width:520px}}.ctx th{{white-space:nowrap;width:180px}}
.section{{margin:20px 0 8px;font-weight:700;font-size:14px}}
.filters{{margin:10px 0}}.fbtn{{font-size:12px;font-weight:600;padding:5px 12px;border:1px solid #e2e8f0;background:#fff;border-radius:8px;cursor:pointer;margin-right:6px}}
.fbtn.on{{border-color:#3b82f6;color:#3b82f6}}
.tcard{{background:#fff;border:1px solid #e2e8f0;border-left:4px solid #cbd5e1;border-radius:10px;padding:10px 14px;margin-bottom:8px}}
.tcard[data-status=pass]{{border-left-color:#22c55e}}.tcard[data-status=fail]{{border-left-color:#ef4444}}.tcard[data-status=skip]{{border-left-color:#f59e0b}}
.tcard-h{{display:flex;align-items:center;gap:10px}}
.pill{{color:#fff;font-size:10px;font-weight:800;padding:2px 8px;border-radius:6px}}
.tname{{font-weight:600;font-size:13px;word-break:break-all}}.tmeta{{margin-left:auto;color:#64748b;font-size:12px;white-space:nowrap}}
.msg{{margin-top:8px;background:#fef2f2;color:#991b1b;font-size:12px;padding:8px 10px;border-radius:8px;white-space:pre-wrap;overflow-x:auto}}
.empty{{color:#94a3b8;padding:12px}}
</style></head><body>
<h1>🎯 {title}</h1><div class="sub">Generated {generated}</div>
<div class="top">
  {_donut(summary)}
  <div class="cards">
    <div class="stat"><div class="n">{summary['total']}</div><div class="l">Total</div></div>
    <div class="stat"><div class="n" style="color:#22c55e">{summary['passed']}</div><div class="l">Passed</div></div>
    <div class="stat"><div class="n" style="color:#ef4444">{summary['failed']}</div><div class="l">Failed</div></div>
    <div class="stat"><div class="n" style="color:#f59e0b">{summary['skipped']}</div><div class="l">Skipped</div></div>
    <div class="stat"><div class="n">{dur:.1f}s</div><div class="l">Duration</div></div>
  </div>
  {ctx_block}
</div>
<div class="section">Category breakdown</div>
<table><thead><tr><th>Category</th><th>Pass</th><th>Fail</th><th>Skip</th></tr></thead>
<tbody>{cat_rows}</tbody></table>
<div class="section">Tests</div>
<div class="filters">
  <button class="fbtn on" onclick="filt(this,'all')">All</button>
  <button class="fbtn" onclick="filt(this,'pass')">Passed</button>
  <button class="fbtn" onclick="filt(this,'fail')">Failed</button>
  <button class="fbtn" onclick="filt(this,'skip')">Skipped</button>
</div>
<div id="tests">{cards}</div>
<script>
function filt(btn,s){{
  document.querySelectorAll('.fbtn').forEach(b=>b.classList.remove('on'));btn.classList.add('on');
  document.querySelectorAll('.tcard').forEach(c=>{{
    c.style.display=(s==='all'||c.dataset.status===s)?'block':'none';
  }});
}}
</script>
</body></html>"""


def write_report(out_path, tests: list[dict], meta: Optional[dict] = None) -> Path:
    """Render the report and write it to *out_path*; returns the path."""
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_html(tests, meta), encoding="utf-8")
    return path
