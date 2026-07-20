#!/usr/bin/env python3
"""
PlaySight Analytics Dashboard — Beautiful PowerBI-style web dashboard.

Reads data from:
  logs_and_reports/flakiness.db   → test_results + perf_results tables
  logs_and_reports/runs/*.json    → per-run summaries written by conftest.py

Usage:
    python tools/dashboard.py             # default port 8766
    python tools/dashboard.py --port 9000
    # then open http://localhost:8766
"""

import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import uvicorn
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, JSONResponse, Response
except ImportError:
    print("Missing dependencies. Run: pip install fastapi uvicorn[standard]")
    sys.exit(1)

_DB   = Path("logs_and_reports/flakiness.db")
_RUNS = Path("logs_and_reports/runs")
_LOAD_RUNS = Path("logs_and_reports/load_runs")

app = FastAPI(title="PlaySight Dashboard")


# ── DB helper ──────────────────────────────────────────────────────────────

def _conn():
    if not _DB.exists():
        return None
    c = sqlite3.connect(str(_DB), timeout=5)
    c.row_factory = sqlite3.Row
    return c


def _q(sql: str, params=()):
    c = _conn()
    if c is None:
        return []
    try:
        rows = c.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        c.close()


# ── Data endpoints ─────────────────────────────────────────────────────────

@app.get("/api/summary")
def api_summary():
    runs = _load_run_jsons()
    if not runs:
        return {"pass_rate": 0, "total_runs": 0, "total_tests": 0,
                "avg_duration_s": 0, "flaky_count": 0}
    total_tests = sum(r.get("total", 0) for r in runs)
    total_passed = sum(r.get("passed", 0) for r in runs)
    pass_rate = round(total_passed / max(total_tests, 1) * 100, 1)
    avg_dur = round(sum(r.get("duration_s", 0) for r in runs) / len(runs), 1)
    flaky = _q("""
        WITH recent AS (
            SELECT test_id, outcome,
                   ROW_NUMBER() OVER (PARTITION BY test_id ORDER BY id DESC) AS rn
            FROM test_results
        ),
        stats AS (
            SELECT test_id,
                   COUNT(*) AS total,
                   SUM(CASE WHEN outcome IN ('FAILED','ERROR') THEN 1 ELSE 0 END) AS failures,
                   SUM(CASE WHEN outcome='PASSED' THEN 1 ELSE 0 END) AS passes
            FROM recent WHERE rn <= 20 GROUP BY test_id
        )
        SELECT COUNT(*) AS cnt FROM stats WHERE passes>0 AND failures>0 AND CAST(failures AS REAL)/total>=0.15
    """)
    flaky_count = flaky[0]["cnt"] if flaky else 0
    return {"pass_rate": pass_rate, "total_runs": len(runs),
            "total_tests": total_tests, "avg_duration_s": avg_dur,
            "flaky_count": flaky_count}


@app.get("/api/trend")
def api_trend():
    runs = sorted(_load_run_jsons(), key=lambda r: r.get("run_ts", ""))[-15:]
    return runs


@app.get("/api/runs")
def api_runs():
    return sorted(_load_run_jsons(), key=lambda r: r.get("run_ts", ""), reverse=True)


@app.get("/api/flaky")
def api_flaky():
    return _q("""
        WITH recent AS (
            SELECT test_id, outcome,
                   ROW_NUMBER() OVER (PARTITION BY test_id ORDER BY id DESC) AS rn
            FROM test_results
        ),
        stats AS (
            SELECT test_id, COUNT(*) AS total,
                   SUM(CASE WHEN outcome IN ('FAILED','ERROR') THEN 1 ELSE 0 END) AS failures,
                   SUM(CASE WHEN outcome='PASSED' THEN 1 ELSE 0 END) AS passes
            FROM recent WHERE rn<=20 GROUP BY test_id
        )
        SELECT test_id, total, failures, passes,
               ROUND(CAST(failures AS REAL)/total, 3) AS flake_rate
        FROM stats WHERE passes>0 AND failures>0 AND CAST(failures AS REAL)/total>=0.10
        ORDER BY flake_rate DESC LIMIT 15
    """)


@app.get("/api/browsers")
def api_browsers():
    return _q("""
        SELECT COALESCE(NULLIF(browser,''),'chromium') AS browser, COUNT(*) AS count
        FROM test_results GROUP BY browser ORDER BY count DESC
    """)


@app.get("/api/durations")
def api_durations():
    return _q("""
        SELECT test_id, ROUND(AVG(duration),2) AS avg_duration
        FROM test_results WHERE duration > 0
        GROUP BY test_id ORDER BY avg_duration DESC LIMIT 10
    """)


@app.get("/api/performance")
def api_performance():
    return _q("""
        SELECT run_ts,
               ROUND(AVG(lcp),0)        AS avg_lcp,
               ROUND(AVG(load_time),0)  AS avg_load_time,
               ROUND(AVG(ttfb),0)       AS avg_ttfb,
               ROUND(AVG(dom_loaded),0) AS avg_dom_loaded
        FROM perf_results
        GROUP BY run_ts ORDER BY run_ts DESC LIMIT 15
    """)


@app.get("/api/perf_tests")
def api_perf_tests():
    return _q("""
        SELECT test_id,
               ROUND(AVG(lcp),0)        AS avg_lcp,
               ROUND(AVG(load_time),0)  AS avg_load_time,
               ROUND(AVG(ttfb),0)       AS avg_ttfb,
               ROUND(AVG(cls),3)        AS avg_cls
        FROM perf_results GROUP BY test_id
        ORDER BY avg_load_time DESC LIMIT 15
    """)


@app.get("/api/contracts")
def api_contracts():
    """Read latest API test results from runs/*.json if present."""
    runs = sorted(_load_run_jsons(), key=lambda r: r.get("run_ts", ""), reverse=True)
    for run in runs:
        if run.get("api_results"):
            return {"results": run["api_results"]}
    return {"results": []}


# ── Load-test endpoints ──────────────────────────────────────────────────────

@app.get("/api/load")
def api_load():
    """Load-run summaries written by the Load Runner (logs_and_reports/load_runs/)."""
    try:
        from load.engine import list_runs
        return list_runs(limit=50)
    except Exception:
        return []


def _safe_load_dir(run_id: str):
    """Resolve a load-run directory, guarding against path traversal."""
    base = _LOAD_RUNS.resolve()
    run_dir = (_LOAD_RUNS / run_id).resolve()
    return run_dir if str(run_dir).startswith(str(base)) else None


@app.get("/load_report/{run_id}", response_class=HTMLResponse)
def load_report(run_id: str):
    run_dir = _safe_load_dir(run_id)
    path = run_dir / "report.html" if run_dir else None
    if not path or not path.exists():
        return HTMLResponse("<h3>Load report not found</h3>", status_code=404)
    return HTMLResponse(path.read_text())


@app.get("/load_file/{run_id}/{kind}")
def load_file(run_id: str, kind: str):
    mapping = {"json": ("results.json", "application/json"),
               "junit": ("junit.xml", "application/xml")}
    if kind not in mapping:
        return JSONResponse({"error": "unknown file kind"}, status_code=400)
    filename, media = mapping[kind]
    run_dir = _safe_load_dir(run_id)
    path = run_dir / filename if run_dir else None
    if not path or not path.exists():
        return JSONResponse({"error": "file not found"}, status_code=404)
    return Response(content=path.read_text(), media_type=media)


@app.get("/load_allure/{run_id}", response_class=HTMLResponse)
def load_allure(run_id: str, refresh: int = 0):
    """
    One-click Allure report: generate a self-contained HTML report from the
    run's allure-results on first view (cached thereafter; ?refresh=1 rebuilds),
    then serve it inline. Requires the `allure` CLI on PATH.
    """
    run_dir = _safe_load_dir(run_id)
    if not run_dir:
        return HTMLResponse("<h3>Invalid run id</h3>", status_code=400)
    results = run_dir / "allure-results"
    if not results.exists() or not any(results.glob("*-result.json")):
        return HTMLResponse("<h3>No Allure results for this run</h3>", status_code=404)

    report = run_dir / "allure-report" / "index.html"
    if refresh or not report.exists():
        allure_bin = shutil.which("allure")
        if not allure_bin:
            return HTMLResponse(_allure_missing_html(run_id, results), status_code=503)
        try:
            subprocess.run(
                [allure_bin, "generate", str(results), "--single-file", "--clean",
                 "-o", str(run_dir / "allure-report")],
                check=True, capture_output=True, timeout=120,
            )
        except subprocess.TimeoutExpired:
            return HTMLResponse("<h3>Allure generation timed out</h3>", status_code=504)
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or b"").decode(errors="replace")[:1000]
            return HTMLResponse(
                f"<h3>Allure generation failed</h3><pre>{detail}</pre>", status_code=500)
    if not report.exists():
        return HTMLResponse("<h3>Allure report was not produced</h3>", status_code=500)
    return HTMLResponse(report.read_text())


def _allure_missing_html(run_id: str, results: Path) -> str:
    return (
        "<div style='font-family:system-ui;padding:40px;max-width:640px;margin:auto'>"
        "<h2>⚠️ Allure CLI not found</h2>"
        "<p>Install the Allure command-line tool to view reports in the browser:</p>"
        "<pre>brew install allure        # macOS\n"
        "# or: npm install -g allure-commandline</pre>"
        "<p>Or view it directly without installing globally:</p>"
        f"<pre>allure serve {results}</pre>"
        f"<p>Then reload <a href='/load_allure/{run_id}'>this page</a>.</p></div>"
    )


# ── Run JSON loader ────────────────────────────────────────────────────────

def _load_run_jsons() -> list:
    if not _RUNS.exists():
        return []
    runs = []
    for f in sorted(_RUNS.glob("run_*.json")):
        try:
            runs.append(json.loads(f.read_text()))
        except Exception:
            pass
    return runs


# ── Dashboard HTML ─────────────────────────────────────────────────────────

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PlaySight — Analytics Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --sb:#0f172a;--sb-hover:#1e293b;--sb-border:rgba(255,255,255,.06);
  --active:#3b82f6;--active-bg:rgba(59,130,246,.15);
  --bg:#f0f4f8;--card:#fff;--text:#1e293b;--muted:#64748b;--border:#e2e8f0;
  --pass:#22c55e;--fail:#ef4444;--skip:#f59e0b;--blue:#3b82f6;
  --purple:#8b5cf6;--cyan:#06b6d4;--pink:#ec4899;
  --r:12px;--sh:0 1px 4px rgba(0,0,0,.06),0 1px 2px rgba(0,0,0,.04);
  --sh-md:0 4px 20px rgba(0,0,0,.09);
}
body{font-family:'Inter',sans-serif;background:var(--bg);color:var(--text);display:flex;height:100vh;overflow:hidden}
/* ── SIDEBAR ── */
.sb{width:240px;min-width:240px;background:var(--sb);display:flex;flex-direction:column;z-index:10}
.brand{padding:22px 20px 18px;border-bottom:1px solid var(--sb-border)}
.brand-logo{font-size:22px;font-weight:800;color:#fff;letter-spacing:-.5px}
.brand-sub{font-size:10px;color:rgba(255,255,255,.35);margin-top:3px;letter-spacing:1px;text-transform:uppercase}
.nav{padding:10px 0;flex:1}
.nav-sec{padding:14px 20px 4px;font-size:10px;font-weight:600;letter-spacing:1.2px;text-transform:uppercase;color:rgba(255,255,255,.28)}
.nav-item{display:flex;align-items:center;gap:10px;padding:9px 20px;color:rgba(255,255,255,.55);cursor:pointer;font-size:13.5px;font-weight:500;border-left:3px solid transparent;transition:all .15s;user-select:none}
.nav-item:hover{background:var(--sb-hover);color:rgba(255,255,255,.9)}
.nav-item.active{background:var(--active-bg);color:#fff;border-left-color:var(--active)}
.nav-icon{width:20px;text-align:center;font-size:15px}
.sb-footer{padding:14px 20px;border-top:1px solid var(--sb-border);font-size:11px;color:rgba(255,255,255,.2)}
/* ── MAIN ── */
.main{flex:1;display:flex;flex-direction:column;overflow:hidden;min-width:0}
.topbar{background:var(--card);border-bottom:1px solid var(--border);padding:0 28px;height:58px;display:flex;align-items:center;justify-content:space-between;flex-shrink:0;box-shadow:0 1px 0 var(--border)}
.top-left{display:flex;align-items:center;gap:12px}
.top-title{font-size:15px;font-weight:600}
.top-badge{font-size:11px;padding:3px 9px;border-radius:20px;background:rgba(59,130,246,.1);color:var(--blue);font-weight:600}
.top-right{display:flex;align-items:center;gap:10px}
.updated{font-size:11.5px;color:var(--muted)}
.refresh-btn{background:var(--active);color:#fff;border:none;padding:7px 15px;border-radius:8px;font-size:13px;font-weight:500;cursor:pointer;font-family:inherit;transition:background .15s;display:flex;align-items:center;gap:6px}
.refresh-btn:hover{background:#2563eb}
/* ── CONTENT ── */
.page{flex:1;overflow-y:auto;padding:24px 28px;display:none}
.page.active{display:block}
/* ── KPI CARDS ── */
.kpi-row{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:22px}
.kpi{background:var(--card);border-radius:var(--r);padding:18px 20px;box-shadow:var(--sh);display:flex;align-items:center;gap:14px;border-top:3px solid transparent;transition:box-shadow .2s,transform .15s}
.kpi:hover{box-shadow:var(--sh-md);transform:translateY(-1px)}
.kpi.green{border-top-color:var(--pass)}.kpi.red{border-top-color:var(--fail)}
.kpi.blue{border-top-color:var(--blue)}.kpi.amber{border-top-color:var(--skip)}
.kpi.purple{border-top-color:var(--purple)}
.kpi-icon{width:46px;height:46px;border-radius:11px;display:flex;align-items:center;justify-content:center;font-size:21px;flex-shrink:0}
.kpi.green  .kpi-icon{background:rgba(34,197,94,.1);color:var(--pass)}
.kpi.red    .kpi-icon{background:rgba(239,68,68,.1);color:var(--fail)}
.kpi.blue   .kpi-icon{background:rgba(59,130,246,.1);color:var(--blue)}
.kpi.amber  .kpi-icon{background:rgba(245,158,11,.1);color:var(--skip)}
.kpi.purple .kpi-icon{background:rgba(139,92,246,.1);color:var(--purple)}
.kv{font-size:26px;font-weight:700;line-height:1.1}
.kl{font-size:11.5px;color:var(--muted);margin-top:3px;font-weight:500}
.kt{font-size:11px;margin-top:3px}
.kt.up{color:var(--pass)}.kt.down{color:var(--fail)}.kt.neutral{color:var(--muted)}
/* ── CHARTS ── */
.grid-2{display:grid;grid-template-columns:2fr 1fr;gap:16px;margin-bottom:16px}
.grid-3{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:16px}
.grid-1{display:grid;grid-template-columns:1fr;gap:16px;margin-bottom:16px}
.chart-card{background:var(--card);border-radius:var(--r);padding:20px;box-shadow:var(--sh)}
.ch{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:14px}
.ct{font-size:14px;font-weight:600}.cs{font-size:11.5px;color:var(--muted);margin-top:2px}
.cb{font-size:11px;font-weight:600;padding:3px 9px;border-radius:20px;background:rgba(59,130,246,.1);color:var(--blue)}
.cw{position:relative}
/* ── TABLE ── */
.table-card{background:var(--card);border-radius:var(--r);box-shadow:var(--sh);overflow:hidden;margin-bottom:16px}
.th{padding:14px 20px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center}
.tt{font-size:14px;font-weight:600}
table{width:100%;border-collapse:collapse}
th{padding:9px 16px;text-align:left;font-size:10.5px;font-weight:600;text-transform:uppercase;letter-spacing:.6px;color:var(--muted);background:#f8fafc;border-bottom:1px solid var(--border)}
td{padding:11px 16px;font-size:13px;border-bottom:1px solid var(--border);color:var(--text)}
tr:last-child td{border-bottom:none}
tr:hover td{background:#fafbfd}
/* ── BADGES ── */
.badge{display:inline-block;padding:3px 9px;border-radius:20px;font-size:11px;font-weight:600}
.bp{background:rgba(34,197,94,.12);color:#16a34a}
.bf{background:rgba(239,68,68,.12);color:#dc2626}
.bs{background:rgba(245,158,11,.12);color:#d97706}
.bfl{background:rgba(245,158,11,.1);color:#b45309}
.bb{background:rgba(59,130,246,.1);color:#1d4ed8}
/* ── MISC ── */
.empty{text-align:center;padding:60px 20px;color:var(--muted)}
.empty-icon{font-size:44px;margin-bottom:14px}
.empty-text{font-size:15px;font-weight:600;color:var(--text);margin-bottom:6px}
.empty-sub{font-size:13px}
.spin{width:22px;height:22px;border:3px solid var(--border);border-top-color:var(--active);border-radius:50%;animation:spin .7s linear infinite;margin:40px auto}
@keyframes spin{to{transform:rotate(360deg)}}
.divider{height:1px;background:var(--border);margin:16px 0}
code{background:#f1f5f9;padding:2px 6px;border-radius:4px;font-size:12px;color:#374151}
.info-box{background:linear-gradient(135deg,rgba(59,130,246,.07),rgba(139,92,246,.05));border:1px solid rgba(59,130,246,.15);border-radius:var(--r);padding:14px 18px;font-size:13px;color:var(--text);line-height:1.6}
::-webkit-scrollbar{width:5px}::-webkit-scrollbar-track{background:transparent}::-webkit-scrollbar-thumb{background:var(--border);border-radius:3px}
</style>
</head>
<body>

<aside class="sb">
  <div class="brand">
    <div class="brand-logo">🎭 PlaySight</div>
    <div class="brand-sub">Analytics Dashboard</div>
  </div>
  <nav class="nav">
    <div class="nav-sec">Main</div>
    <div class="nav-item active" onclick="go('overview')" id="n-overview"><span class="nav-icon">📊</span>Overview</div>
    <div class="nav-item" onclick="go('flaky')"    id="n-flaky">   <span class="nav-icon">🐞</span>Flakiness</div>
    <div class="nav-item" onclick="go('perf')"     id="n-perf">    <span class="nav-icon">⚡</span>Performance</div>
    <div class="nav-sec">Data</div>
    <div class="nav-item" onclick="go('runs')"     id="n-runs">    <span class="nav-icon">🔄</span>Run History</div>
    <div class="nav-item" onclick="go('api')"      id="n-api">     <span class="nav-icon">🔌</span>API Contracts</div>
    <div class="nav-item" onclick="go('load')"     id="n-load">    <span class="nav-icon">🚀</span>Load Tests</div>
  </nav>
  <div class="sb-footer">PlaySight v1.0 · Playwright + Python</div>
</aside>

<div class="main">
  <div class="topbar">
    <div class="top-left">
      <div class="top-title" id="page-title">Overview</div>
      <div class="top-badge" id="top-badge">Live</div>
    </div>
    <div class="top-right">
      <span class="updated" id="last-upd">Loading…</span>
      <button class="refresh-btn" onclick="loadAll()">↻ Refresh</button>
    </div>
  </div>

  <!-- ═══════════════════════ OVERVIEW ═══════════════════════ -->
  <div class="page active" id="p-overview">
    <div class="kpi-row" id="kpi-row"><div class="spin"></div></div>

    <div class="grid-2">
      <div class="chart-card">
        <div class="ch">
          <div><div class="ct">Test Run Trend</div><div class="cs">Pass · Fail · Skip across last 15 runs</div></div>
          <span class="cb" id="run-badge">0 runs</span>
        </div>
        <div class="cw" style="height:220px"><canvas id="cTrend"></canvas></div>
      </div>
      <div class="chart-card">
        <div class="ch"><div><div class="ct">Pass Rate Trend</div><div class="cs">% passing per run</div></div></div>
        <div class="cw" style="height:220px"><canvas id="cRate"></canvas></div>
      </div>
    </div>

    <div class="grid-3">
      <div class="chart-card">
        <div class="ch"><div><div class="ct">Top Flaky Tests</div><div class="cs">By failure rate</div></div></div>
        <div class="cw" style="height:210px"><canvas id="cFlaky"></canvas></div>
      </div>
      <div class="chart-card">
        <div class="ch"><div><div class="ct">Browser Distribution</div><div class="cs">Tests by engine</div></div></div>
        <div class="cw" style="height:210px"><canvas id="cBrowser"></canvas></div>
      </div>
      <div class="chart-card">
        <div class="ch"><div><div class="ct">Slowest Tests</div><div class="cs">Avg duration (s)</div></div></div>
        <div class="cw" style="height:210px"><canvas id="cDuration"></canvas></div>
      </div>
    </div>

    <div class="table-card">
      <div class="th"><div class="tt">Recent Runs</div></div>
      <table><thead><tr><th>Run</th><th>Total</th><th>Passed</th><th>Failed</th><th>Skipped</th><th>Pass Rate</th><th>Duration</th><th>Status</th></tr></thead>
      <tbody id="tb-recent"><tr><td colspan="8" style="text-align:center;padding:28px;color:var(--muted)"><div class="spin"></div></td></tr></tbody></table>
    </div>
  </div>

  <!-- ═══════════════════════ FLAKINESS ═══════════════════════ -->
  <div class="page" id="p-flaky">
    <div class="grid-2">
      <div class="chart-card">
        <div class="ch"><div><div class="ct">Flaky Test Failure Rates</div><div class="cs">Tests with inconsistent outcomes in last 20 runs</div></div></div>
        <div class="cw" style="height:300px"><canvas id="cFlakyDetail"></canvas></div>
      </div>
      <div class="chart-card">
        <div class="ch"><div><div class="ct">Flakiness Summary</div></div></div>
        <div id="flaky-info" style="padding:4px 0"></div>
      </div>
    </div>
    <div class="table-card">
      <div class="th"><div class="tt">Flaky Tests Detail</div></div>
      <table><thead><tr><th>Test</th><th>Flake Rate</th><th>Failures</th><th>Passes</th><th>Total Runs</th><th>Risk Level</th></tr></thead>
      <tbody id="tb-flaky"></tbody></table>
    </div>
  </div>

  <!-- ═══════════════════════ PERFORMANCE ═══════════════════════ -->
  <div class="page" id="p-perf">
    <div class="grid-2">
      <div class="chart-card">
        <div class="ch"><div><div class="ct">Load Time & LCP per Run</div><div class="cs">Milliseconds (avg across tests)</div></div></div>
        <div class="cw" style="height:260px"><canvas id="cPerfLoad"></canvas></div>
      </div>
      <div class="chart-card">
        <div class="ch"><div><div class="ct">TTFB & DOM Loaded per Run</div><div class="cs">Milliseconds (avg)</div></div></div>
        <div class="cw" style="height:260px"><canvas id="cPerfTtfb"></canvas></div>
      </div>
    </div>
    <div class="info-box" style="margin-bottom:16px">
      ⚡ <strong>Budgets:</strong> LCP ≤ 2500 ms &nbsp;|&nbsp; Full Load ≤ 5000 ms &nbsp;|&nbsp;
      Enable with <code>PERFORMANCE_METRICS=true</code> in <code>config/.env</code>
    </div>
    <div class="table-card">
      <div class="th"><div class="tt">Per-Test Performance Breakdown</div></div>
      <table><thead><tr><th>Test</th><th>Avg LCP (ms)</th><th>Avg Load (ms)</th><th>Avg TTFB (ms)</th><th>Avg CLS</th><th>Status</th></tr></thead>
      <tbody id="tb-perf"></tbody></table>
    </div>
  </div>

  <!-- ═══════════════════════ RUNS ═══════════════════════ -->
  <div class="page" id="p-runs">
    <div class="table-card">
      <div class="th"><div class="tt">Complete Run History</div></div>
      <table><thead><tr><th>Timestamp</th><th>Total</th><th>Passed</th><th>Failed</th><th>Skipped</th><th>Pass Rate</th><th>Duration</th><th>Failed Tests</th><th>Status</th></tr></thead>
      <tbody id="tb-allruns"></tbody></table>
    </div>
  </div>

  <!-- ═══════════════════════ API CONTRACTS ═══════════════════════ -->
  <div class="page" id="p-api">
    <div class="kpi-row" id="api-kpis" style="grid-template-columns:repeat(3,1fr)"></div>
    <div class="info-box" style="margin-bottom:16px">
      🔌 Run <code>pytest tests/api/ -v</code> to populate ·
      Start mock server: <code>python tools/mock_api_server.py</code> then set
      <code>API_BASE_URL=http://localhost:8765</code> in <code>config/.env</code>
    </div>
    <div class="table-card">
      <div class="th"><div class="tt">API Contract Test Results (Latest Run)</div></div>
      <table><thead><tr><th>Contract Test</th><th>Status</th><th>Duration</th><th>Notes</th></tr></thead>
      <tbody id="tb-api"></tbody></table>
    </div>
  </div>

  <!-- ═══════════════════════ LOAD TESTS ═══════════════════════ -->
  <div class="page" id="p-load">
    <div class="kpi-row" id="load-kpis"><div class="spin"></div></div>
    <div class="grid-2">
      <div class="chart-card">
        <div class="ch"><div><div class="ct">Throughput per Run</div><div class="cs">Requests/s · last 15 load runs</div></div></div>
        <div class="cw" style="height:240px"><canvas id="cLoadRps"></canvas></div>
      </div>
      <div class="chart-card">
        <div class="ch"><div><div class="ct">p95 Response Time per Run</div><div class="cs">Milliseconds · last 15 load runs</div></div></div>
        <div class="cw" style="height:240px"><canvas id="cLoadP95"></canvas></div>
      </div>
    </div>
    <div class="grid-3">
      <div class="chart-card">
        <div class="ch"><div><div class="ct">Runs by Profile</div><div class="cs">smoke · load · stress · …</div></div></div>
        <div class="cw" style="height:210px"><canvas id="cLoadProfile"></canvas></div>
      </div>
      <div class="chart-card" style="grid-column:span 2">
        <div class="info-box" style="height:100%;display:flex;align-items:center">
          🚀 Load runs are produced by the <strong>Load Runner</strong>:
          <code>python tools/load_runner.py serve</code> (UI) or
          <code>python tools/load_runner.py run --scenario crud --profile smoke</code> (CI).
          Every run writes HTML · JUnit · JSON · Allure — all one click away below
          (Allure needs the <code>allure</code> CLI). See <code>LOAD_TESTING.md</code>.
        </div>
      </div>
    </div>
    <div class="table-card">
      <div class="th"><div class="tt">Load Run History</div></div>
      <table><thead><tr><th>Run</th><th>Scenario</th><th>Profile</th><th>Requests</th><th>Fail %</th><th>Avg (ms)</th><th>p95 (ms)</th><th>RPS</th><th>Verdict</th><th>Reports</th></tr></thead>
      <tbody id="tb-load"><tr><td colspan="10" style="text-align:center;padding:28px;color:var(--muted)"><div class="spin"></div></td></tr></tbody></table>
    </div>
  </div>
</div>

<script>
const CH = {};
const TITLES = {overview:'Overview',flaky:'Flakiness Tracker',perf:'Performance Metrics',runs:'Run History',api:'API Contracts',load:'Load & Performance'};

function go(page) {
  document.querySelectorAll('.page').forEach(e => e.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(e => e.classList.remove('active'));
  document.getElementById('p-' + page).classList.add('active');
  document.getElementById('n-' + page).classList.add('active');
  document.getElementById('page-title').textContent = TITLES[page];
  if (page === 'flaky') loadFlakyPage();
  if (page === 'perf')  loadPerfPage();
  if (page === 'runs')  loadRunsPage();
  if (page === 'api')   loadApiPage();
  if (page === 'load')  loadLoadPage();
}

function mkChart(id, cfg) {
  if (CH[id]) CH[id].destroy();
  const el = document.getElementById(id);
  if (!el) return;
  CH[id] = new Chart(el, cfg);
}

function fmtTs(ts) {
  if (!ts) return '-';
  return ts.replace('_',' ').replace(/-/g,'/').slice(0,16);
}

function rateBadge(r) {
  const cls = r>=90?'bp':r>=70?'bs':'bf';
  return `<span class="badge ${cls}">${r.toFixed(1)}%</span>`;
}

const CHART_OPTS = {
  responsive:true, maintainAspectRatio:false,
  plugins:{legend:{position:'top',labels:{font:{size:11},boxWidth:12,padding:12}}},
};

// ── Overview ──────────────────────────────────────────────────────────────
async function loadSummary() {
  try {
    const d = await fetch('/api/summary').then(r=>r.json());
    const pr = d.pass_rate??0;
    const col = pr>=90?'#22c55e':pr>=70?'#f59e0b':'#ef4444';
    document.getElementById('kpi-row').innerHTML = `
      <div class="kpi green"><div class="kpi-icon">✅</div><div><div class="kv" style="color:${col}">${pr.toFixed(1)}%</div><div class="kl">Overall Pass Rate</div></div></div>
      <div class="kpi blue"><div class="kpi-icon">🔄</div><div><div class="kv">${d.total_runs??0}</div><div class="kl">Total Runs</div></div></div>
      <div class="kpi blue"><div class="kpi-icon">🧪</div><div><div class="kv">${d.total_tests??0}</div><div class="kl">Tests Executed</div></div></div>
      <div class="kpi ${(d.flaky_count??0)>0?'amber':'green'}"><div class="kpi-icon">${(d.flaky_count??0)>0?'⚠️':'🏆'}</div><div><div class="kv">${d.flaky_count??0}</div><div class="kl">Flaky Tests</div><div class="kt ${(d.flaky_count??0)===0?'up':'down'}">${(d.flaky_count??0)===0?'✓ None detected':'↑ Needs attention'}</div></div></div>
    `;
  } catch {
    document.getElementById('kpi-row').innerHTML = `<div style="grid-column:1/-1"><div class="empty"><div class="empty-icon">📭</div><div class="empty-text">No run data yet</div><div class="empty-sub">Run <code>pytest tests/</code> to populate</div></div></div>`;
  }
}

async function loadTrend() {
  try {
    const runs = await fetch('/api/trend').then(r=>r.json());
    if (!runs.length) return;
    document.getElementById('run-badge').textContent = runs.length + ' runs';
    const L = runs.map(r=>fmtTs(r.run_ts));
    mkChart('cTrend',{
      type:'bar', data:{labels:L, datasets:[
        {label:'Passed', data:runs.map(r=>r.passed||0), backgroundColor:'rgba(34,197,94,.8)',  borderRadius:4},
        {label:'Failed', data:runs.map(r=>r.failed||0), backgroundColor:'rgba(239,68,68,.8)',  borderRadius:4},
        {label:'Skipped',data:runs.map(r=>r.skipped||0),backgroundColor:'rgba(245,158,11,.65)',borderRadius:4},
      ]},
      options:{...CHART_OPTS,
        plugins:{...CHART_OPTS.plugins},
        scales:{x:{stacked:true,ticks:{font:{size:10},maxRotation:45},grid:{display:false}},
                y:{stacked:true,ticks:{font:{size:11}},grid:{color:'#f0f4f8'}}}
      }
    });
    const rates = runs.map(r=>r.total>0?(r.passed/r.total*100):0);
    mkChart('cRate',{
      type:'line', data:{labels:L, datasets:[{
        label:'Pass Rate %', data:rates,
        borderColor:'#3b82f6', backgroundColor:'rgba(59,130,246,.07)',
        fill:true, tension:.4,
        pointBackgroundColor:rates.map(r=>r>=90?'#22c55e':r>=70?'#f59e0b':'#ef4444'),
        pointRadius:5, pointHoverRadius:7,
      }]},
      options:{...CHART_OPTS,
        plugins:{legend:{display:false}},
        scales:{x:{ticks:{font:{size:10},maxRotation:45},grid:{display:false}},
                y:{min:0,max:100,ticks:{callback:v=>v+'%',font:{size:11}},grid:{color:'#f0f4f8'}}}
      }
    });
  } catch(e){console.error(e)}
}

async function loadFlakyMini() {
  try {
    const data = await fetch('/api/flaky').then(r=>r.json());
    const el = document.getElementById('cFlaky');
    if (!data.length) { el.parentElement.innerHTML='<div style="text-align:center;padding:40px;color:var(--muted)">🎉 No flaky tests</div>'; return; }
    const top = data.slice(0,6);
    const labels = top.map(t=>t.test_id.split('::').pop().split('[')[0].slice(0,28));
    const rates  = top.map(t=>(t.flake_rate*100).toFixed(1));
    const colors = rates.map(r=>+r>=50?'rgba(239,68,68,.8)':+r>=25?'rgba(245,158,11,.8)':'rgba(251,146,60,.8)');
    mkChart('cFlaky',{type:'bar',data:{labels,datasets:[{label:'Flake %',data:rates,backgroundColor:colors,borderRadius:4}]},
      options:{...CHART_OPTS,indexAxis:'y',plugins:{legend:{display:false}},
        scales:{x:{max:100,ticks:{callback:v=>v+'%',font:{size:10}},grid:{color:'#f0f4f8'}},
                y:{ticks:{font:{size:10}},grid:{display:false}}}}
    });
  } catch(e){console.error(e)}
}

async function loadBrowsers() {
  try {
    const data = await fetch('/api/browsers').then(r=>r.json());
    if (!data.length) return;
    const COLS=['#3b82f6','#8b5cf6','#06b6d4','#22c55e','#f59e0b'];
    mkChart('cBrowser',{type:'doughnut',
      data:{labels:data.map(d=>d.browser||'unknown'),datasets:[{data:data.map(d=>d.count),backgroundColor:COLS.slice(0,data.length),borderWidth:2,borderColor:'#fff'}]},
      options:{responsive:true,maintainAspectRatio:false,cutout:'62%',
        plugins:{legend:{position:'bottom',labels:{font:{size:11},boxWidth:10,padding:10}}}}
    });
  } catch(e){console.error(e)}
}

async function loadDurations() {
  try {
    const data = await fetch('/api/durations').then(r=>r.json());
    if (!data.length) return;
    const labels = data.map(d=>d.test_id.split('::').pop().split('[')[0].slice(0,24));
    mkChart('cDuration',{type:'bar',
      data:{labels,datasets:[{label:'Avg (s)',data:data.map(d=>d.avg_duration),backgroundColor:'rgba(139,92,246,.75)',borderRadius:4}]},
      options:{...CHART_OPTS,indexAxis:'y',plugins:{legend:{display:false}},
        scales:{x:{ticks:{font:{size:10}},grid:{color:'#f0f4f8'}},
                y:{ticks:{font:{size:10}},grid:{display:false}}}}
    });
  } catch(e){console.error(e)}
}

async function loadRecentRuns() {
  try {
    const runs = await fetch('/api/runs').then(r=>r.json());
    const rows = runs.slice(0,8).map(r=>{
      const rate = r.pass_rate!==undefined ? r.pass_rate : (r.total>0?r.passed/r.total*100:0);
      const status = r.failed>0?'<span class="badge bf">FAILED</span>':'<span class="badge bp">PASSED</span>';
      return `<tr>
        <td style="font-family:monospace;font-size:12px">${fmtTs(r.run_ts)}</td>
        <td>${r.total||0}</td>
        <td style="color:var(--pass);font-weight:600">${r.passed||0}</td>
        <td style="color:var(--fail);font-weight:600">${r.failed||0}</td>
        <td style="color:var(--skip)">${r.skipped||0}</td>
        <td>${rateBadge(+rate)}</td>
        <td>${r.duration_s?r.duration_s.toFixed(1)+'s':'-'}</td>
        <td>${status}</td>
      </tr>`;
    }).join('');
    document.getElementById('tb-recent').innerHTML = rows || `<tr><td colspan="8" style="text-align:center;padding:28px;color:var(--muted)"><div class="empty-icon">📭</div><div>No runs yet — run <code>pytest tests/</code></div></td></tr>`;
  } catch(e){console.error(e)}
}

// ── Flakiness page ────────────────────────────────────────────────────────
async function loadFlakyPage() {
  try {
    const data = await fetch('/api/flaky').then(r=>r.json());
    const el = document.getElementById('cFlakyDetail');
    if (!data.length) {
      el.parentElement.innerHTML='<div class="empty"><div class="empty-icon">🎉</div><div class="empty-text">No flaky tests detected!</div><div class="empty-sub">Your suite is stable.</div></div>';
      document.getElementById('flaky-info').innerHTML='<div class="empty"><div class="empty-sub">Run tests multiple times to detect flakiness.</div></div>';
      document.getElementById('tb-flaky').innerHTML='<tr><td colspan="6" style="text-align:center;padding:24px;color:var(--muted)">No data</td></tr>';
      return;
    }
    const labels = data.map(t=>t.test_id.split('::').pop().split('[')[0].slice(0,35));
    const rates  = data.map(t=>(t.flake_rate*100).toFixed(1));
    const colors = rates.map(r=>+r>=50?'rgba(239,68,68,.8)':+r>=25?'rgba(245,158,11,.8)':'rgba(251,146,60,.8)');
    mkChart('cFlakyDetail',{type:'bar',data:{labels,datasets:[{label:'Flake Rate %',data:rates,backgroundColor:colors,borderRadius:4}]},
      options:{...CHART_OPTS,indexAxis:'y',plugins:{legend:{display:false}},
        scales:{x:{max:100,ticks:{callback:v=>v+'%',font:{size:11}},grid:{color:'#f0f4f8'}},
                y:{ticks:{font:{size:11}},grid:{display:false}}}}
    });
    const high = data.filter(t=>t.flake_rate>=.5).length;
    const med  = data.filter(t=>t.flake_rate>=.25&&t.flake_rate<.5).length;
    document.getElementById('flaky-info').innerHTML=`
      <div style="display:flex;flex-direction:column;gap:10px;padding:4px 0">
        <div style="display:flex;justify-content:space-between;padding:12px 14px;background:#f8fafc;border-radius:8px">
          <span style="font-size:13px;color:var(--muted)">Total Flaky</span><span style="font-weight:700">${data.length}</span>
        </div>
        <div style="display:flex;justify-content:space-between;padding:12px 14px;background:rgba(239,68,68,.05);border-radius:8px">
          <span style="font-size:13px;color:var(--muted)">High Risk (≥50%)</span><span style="font-weight:700;color:#ef4444">${high}</span>
        </div>
        <div style="display:flex;justify-content:space-between;padding:12px 14px;background:rgba(245,158,11,.05);border-radius:8px">
          <span style="font-size:13px;color:var(--muted)">Medium Risk (25–50%)</span><span style="font-weight:700;color:#f59e0b">${med}</span>
        </div>
        <div class="info-box" style="font-size:12px">
          <strong>Tip:</strong> Tune <code>FLAKINESS_THRESHOLD</code> and
          <code>FLAKINESS_WINDOW</code> in <code>config/.env</code>
        </div>
      </div>`;
    document.getElementById('tb-flaky').innerHTML = data.map(t=>{
      const rate=(t.flake_rate*100).toFixed(1);
      const risk = t.flake_rate>=.5?'<span class="badge bf">High</span>':t.flake_rate>=.25?'<span class="badge bs">Medium</span>':'<span class="badge bfl">Low</span>';
      const name = t.test_id.split('::').pop().split('[')[0];
      return `<tr><td style="max-width:380px;word-break:break-all;font-size:12px">${name}</td><td><strong>${rate}%</strong></td><td>${t.failures}</td><td>${t.passes}</td><td>${t.total}</td><td>${risk}</td></tr>`;
    }).join('');
  } catch(e){console.error(e)}
}

// ── Performance page ──────────────────────────────────────────────────────
async function loadPerfPage() {
  try {
    const data = await fetch('/api/performance').then(r=>r.json());
    if (!data.length) {
      ['cPerfLoad','cPerfTtfb'].forEach(id=>{
        const el=document.getElementById(id);
        if(el) el.parentElement.innerHTML='<div class="empty"><div class="empty-icon">⚡</div><div class="empty-text">No performance data yet</div><div class="empty-sub">Set <code>PERFORMANCE_METRICS=true</code> in config/.env and re-run tests</div></div>';
      });
      document.getElementById('tb-perf').innerHTML='<tr><td colspan="6" style="text-align:center;padding:24px;color:var(--muted)">No data</td></tr>';
      return;
    }
    const L = data.map(d=>fmtTs(d.run_ts)).reverse();
    const rev = [...data].reverse();
    mkChart('cPerfLoad',{type:'line',data:{labels:L,datasets:[
      {label:'Load Time (ms)',data:rev.map(d=>d.avg_load_time||0),borderColor:'#3b82f6',backgroundColor:'rgba(59,130,246,.07)',fill:true,tension:.4},
      {label:'LCP (ms)',data:rev.map(d=>d.avg_lcp||0),borderColor:'#8b5cf6',backgroundColor:'rgba(139,92,246,.05)',fill:true,tension:.4},
    ]},options:{...CHART_OPTS,scales:{x:{ticks:{font:{size:10},maxRotation:45},grid:{display:false}},y:{ticks:{callback:v=>v+'ms',font:{size:11}},grid:{color:'#f0f4f8'}}}}});
    mkChart('cPerfTtfb',{type:'line',data:{labels:L,datasets:[
      {label:'TTFB (ms)',data:rev.map(d=>d.avg_ttfb||0),borderColor:'#06b6d4',backgroundColor:'rgba(6,182,212,.07)',fill:true,tension:.4},
      {label:'DOM Loaded (ms)',data:rev.map(d=>d.avg_dom_loaded||0),borderColor:'#22c55e',backgroundColor:'rgba(34,197,94,.06)',fill:true,tension:.4},
    ]},options:{...CHART_OPTS,scales:{x:{ticks:{font:{size:10},maxRotation:45},grid:{display:false}},y:{ticks:{callback:v=>v+'ms',font:{size:11}},grid:{color:'#f0f4f8'}}}}});
    const tests = await fetch('/api/perf_tests').then(r=>r.json());
    document.getElementById('tb-perf').innerHTML = tests.length ? tests.map(t=>{
      const name = t.test_id.split('::').pop().split('[')[0].slice(0,45);
      const lcpFlag = (t.avg_lcp||0)>2500?'🔴 ':(t.avg_lcp||0)>1500?'🟡 ':'🟢 ';
      const loadFlag= (t.avg_load_time||0)>5000?'<span class="badge bf">Over budget</span>':'<span class="badge bp">OK</span>';
      return `<tr><td style="font-size:12px">${name}</td><td>${lcpFlag}${Math.round(t.avg_lcp||0)}</td><td>${Math.round(t.avg_load_time||0)}</td><td>${Math.round(t.avg_ttfb||0)}</td><td>${(t.avg_cls||0).toFixed(3)}</td><td>${loadFlag}</td></tr>`;
    }).join('') : '<tr><td colspan="6" style="text-align:center;padding:24px;color:var(--muted)">No data</td></tr>';
  } catch(e){console.error(e)}
}

// ── Run history page ──────────────────────────────────────────────────────
async function loadRunsPage() {
  try {
    const runs = await fetch('/api/runs').then(r=>r.json());
    document.getElementById('tb-allruns').innerHTML = runs.length ? runs.map(r=>{
      const rate = r.pass_rate!==undefined?r.pass_rate:(r.total>0?r.passed/r.total*100:0);
      const status = r.failed>0?'<span class="badge bf">FAILED</span>':'<span class="badge bp">PASSED</span>';
      const failed = (r.failed_tests||[]).slice(0,2).map(t=>t.split('::').pop().split('[')[0]).join(', ');
      return `<tr>
        <td style="font-family:monospace;font-size:12px">${fmtTs(r.run_ts)}</td>
        <td>${r.total||0}</td>
        <td style="color:var(--pass);font-weight:600">${r.passed||0}</td>
        <td style="color:var(--fail);font-weight:600">${r.failed||0}</td>
        <td style="color:var(--skip)">${r.skipped||0}</td>
        <td>${rateBadge(+rate)}</td>
        <td>${r.duration_s?r.duration_s.toFixed(1)+'s':'-'}</td>
        <td style="font-size:11px;color:var(--muted);max-width:200px;overflow:hidden;text-overflow:ellipsis">${failed||'—'}</td>
        <td>${status}</td>
      </tr>`;
    }).join('') : '<tr><td colspan="9" style="text-align:center;padding:40px;color:var(--muted)"><div class="empty-icon">📭</div><div>No runs yet</div></td></tr>';
  } catch(e){console.error(e)}
}

// ── API contracts page ────────────────────────────────────────────────────
async function loadApiPage() {
  try {
    const {results=[]} = await fetch('/api/contracts').then(r=>r.json());
    const passed = results.filter(t=>t.status==='PASSED').length;
    const failed = results.length - passed;
    document.getElementById('api-kpis').innerHTML = `
      <div class="kpi green"><div class="kpi-icon">✅</div><div><div class="kv">${passed}</div><div class="kl">Contracts Passing</div></div></div>
      <div class="kpi ${failed>0?'red':'green'}"><div class="kpi-icon">${failed>0?'❌':'🏆'}</div><div><div class="kv">${failed}</div><div class="kl">Contracts Failing</div></div></div>
      <div class="kpi blue"><div class="kpi-icon">🔌</div><div><div class="kv">${results.length||'—'}</div><div class="kl">Total Contracts</div></div></div>
    `;
    document.getElementById('tb-api').innerHTML = results.length ? results.map(t=>{
      const badge = t.status==='PASSED'?'<span class="badge bp">PASSED</span>':'<span class="badge bf">FAILED</span>';
      return `<tr><td style="font-size:12.5px">${t.name}</td><td>${badge}</td><td>${t.duration_s?t.duration_s.toFixed(2)+'s':'-'}</td><td style="font-size:12px;color:var(--muted)">${t.note||''}</td></tr>`;
    }).join('') : '<tr><td colspan="4" style="text-align:center;padding:28px;color:var(--muted)">Run <code>pytest tests/api/ -v</code> to see results</td></tr>';
  } catch(e){console.error(e)}
}

// ── Load tests page ───────────────────────────────────────────────────────
function loadLabel(r){
  const parts = (r.run_id||'').split('__');
  const ts = (parts[0]||'').replace('_',' ').slice(5,16);  // MM-DD HH:MM
  return `${ts} ${r.profile||''}`.trim();
}

async function loadLoadPage() {
  try {
    const runs = await fetch('/api/load').then(r=>r.json());
    if (!runs.length) {
      document.getElementById('load-kpis').innerHTML =
        `<div style="grid-column:1/-1"><div class="empty"><div class="empty-icon">🚀</div><div class="empty-text">No load runs yet</div><div class="empty-sub">Run <code>python tools/load_runner.py serve</code> and launch a test</div></div></div>`;
      ['cLoadRps','cLoadP95','cLoadProfile'].forEach(id=>{const el=document.getElementById(id);if(el)el.parentElement.style.display='none';});
      document.getElementById('tb-load').innerHTML='<tr><td colspan="10" style="text-align:center;padding:28px;color:var(--muted)">No runs yet</td></tr>';
      return;
    }
    const total = runs.length;
    const passed = runs.filter(r=>r.passed).length;
    const passRate = (passed/total*100);
    const totalReq = runs.reduce((a,r)=>a+((r.summary||{}).total_requests||0),0);
    const p95s = runs.map(r=>(r.summary||{}).p95_ms||0).filter(x=>x>0);
    const avgP95 = p95s.length ? Math.round(p95s.reduce((a,b)=>a+b,0)/p95s.length) : 0;
    const col = passRate>=90?'#22c55e':passRate>=70?'#f59e0b':'#ef4444';
    document.getElementById('load-kpis').innerHTML = `
      <div class="kpi purple"><div class="kpi-icon">🚀</div><div><div class="kv">${total}</div><div class="kl">Load Runs</div></div></div>
      <div class="kpi green"><div class="kpi-icon">${passRate>=90?'🏆':'✅'}</div><div><div class="kv" style="color:${col}">${passRate.toFixed(1)}%</div><div class="kl">Pass Rate</div></div></div>
      <div class="kpi blue"><div class="kpi-icon">📨</div><div><div class="kv">${totalReq.toLocaleString()}</div><div class="kl">Total Requests</div></div></div>
      <div class="kpi amber"><div class="kpi-icon">⏱️</div><div><div class="kv">${avgP95}<span style="font-size:14px"> ms</span></div><div class="kl">Avg p95</div></div></div>
    `;

    // Chronological (oldest→newest) for the trend charts.
    const chron = [...runs].reverse().slice(-15);
    const L = chron.map(loadLabel);
    ['cLoadRps','cLoadP95','cLoadProfile'].forEach(id=>{const el=document.getElementById(id);if(el)el.parentElement.style.display='';});
    mkChart('cLoadRps',{type:'bar',
      data:{labels:L,datasets:[{label:'Requests/s',data:chron.map(r=>(r.summary||{}).rps||0),backgroundColor:'rgba(59,130,246,.8)',borderRadius:4}]},
      options:{...CHART_OPTS,plugins:{legend:{display:false}},
        scales:{x:{ticks:{font:{size:9},maxRotation:45},grid:{display:false}},y:{ticks:{font:{size:11}},grid:{color:'#f0f4f8'}}}}});
    mkChart('cLoadP95',{type:'line',
      data:{labels:L,datasets:[{label:'p95 (ms)',data:chron.map(r=>(r.summary||{}).p95_ms||0),borderColor:'#f59e0b',backgroundColor:'rgba(245,158,11,.08)',fill:true,tension:.4,pointRadius:4}]},
      options:{...CHART_OPTS,plugins:{legend:{display:false}},
        scales:{x:{ticks:{font:{size:9},maxRotation:45},grid:{display:false}},y:{ticks:{callback:v=>v+'ms',font:{size:11}},grid:{color:'#f0f4f8'}}}}});

    const byProfile = {};
    runs.forEach(r=>{byProfile[r.profile||'?']=(byProfile[r.profile||'?']||0)+1;});
    const COLS=['#3b82f6','#8b5cf6','#06b6d4','#22c55e','#f59e0b','#ec4899','#ef4444'];
    mkChart('cLoadProfile',{type:'doughnut',
      data:{labels:Object.keys(byProfile),datasets:[{data:Object.values(byProfile),backgroundColor:COLS.slice(0,Object.keys(byProfile).length),borderWidth:2,borderColor:'#fff'}]},
      options:{responsive:true,maintainAspectRatio:false,cutout:'62%',plugins:{legend:{position:'bottom',labels:{font:{size:11},boxWidth:10,padding:10}}}}});

    document.getElementById('tb-load').innerHTML = runs.map(r=>{
      const su = r.summary||{};
      const fr = su.fail_ratio!=null ? (su.fail_ratio*100) : 0;
      const frCls = fr<=1?'bp':fr<=5?'bs':'bf';
      const verdict = r.passed ? '<span class="badge bp">PASS</span>' : '<span class="badge bf">FAIL</span>';
      return `<tr>
        <td style="font-family:monospace;font-size:11.5px">${r.run_id||'-'}</td>
        <td>${r.scenario||'-'}</td>
        <td><span class="badge bb">${r.profile||'-'}</span></td>
        <td>${(su.total_requests||0).toLocaleString()}</td>
        <td><span class="badge ${frCls}">${fr.toFixed(1)}%</span></td>
        <td>${su.avg_ms??'-'}</td>
        <td>${su.p95_ms??'-'}</td>
        <td>${su.rps??'-'}</td>
        <td>${verdict}</td>
        <td style="white-space:nowrap">
          <a href="/load_report/${r.run_id}" target="_blank" style="color:var(--blue);text-decoration:none;font-weight:600">HTML</a> ·
          <a href="/load_file/${r.run_id}/json" target="_blank" style="color:var(--blue);text-decoration:none;font-weight:600">JSON</a> ·
          <a href="/load_file/${r.run_id}/junit" target="_blank" style="color:var(--blue);text-decoration:none;font-weight:600">JUnit</a> ·
          <a href="/load_allure/${r.run_id}" target="_blank" style="color:var(--purple);text-decoration:none;font-weight:600">Allure</a>
        </td></tr>`;
    }).join('');
  } catch(e){console.error(e)}
}

// ── Bootstrap ─────────────────────────────────────────────────────────────
async function loadAll() {
  document.getElementById('last-upd').textContent = 'Refreshing…';
  await Promise.all([loadSummary(), loadTrend(), loadFlakyMini(), loadBrowsers(), loadDurations(), loadRecentRuns()]);
  document.getElementById('last-upd').textContent = 'Updated ' + new Date().toLocaleTimeString();
}

loadAll();
setInterval(loadAll, 60000);
</script>
</body></html>"""


@app.get("/", response_class=HTMLResponse)
def dashboard():
    return _HTML


# ── Entry point ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="PlaySight Analytics Dashboard")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    print(f"\n🎭 PlaySight Dashboard running at http://{args.host}:{args.port}")
    print(f"   Open that URL in your browser\n")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
