"""
Performance Metrics Collector — Capability #11

Collects Core Web Vitals and Navigation Timing from the live browser after
each navigation. Stores results in SQLite and flags tests that exceed budgets.

Metrics collected:
  ttfb          — Time to First Byte (ms)
  dom_loaded    — DOMContentLoaded (ms)
  load_time     — Full page load (ms)
  lcp           — Largest Contentful Paint (ms)  requires init_script
  cls           — Cumulative Layout Shift score   requires init_script

DB: logs_and_reports/flakiness.db  (shared table, different schema)
"""

import logging
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DB_PATH = Path("logs_and_reports/flakiness.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS perf_results (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    test_id   TEXT NOT NULL,
    page_url  TEXT NOT NULL,
    ttfb      INTEGER DEFAULT 0,
    dom_loaded INTEGER DEFAULT 0,
    load_time INTEGER DEFAULT 0,
    lcp       INTEGER DEFAULT 0,
    cls       REAL    DEFAULT 0,
    run_ts    TEXT NOT NULL,
    recorded  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_perf_test ON perf_results (test_id);
"""

# JavaScript injected via add_init_script() — runs before every page load
PERF_INIT_SCRIPT = """
window.__fw_perf = { lcp: 0, cls: 0 };
try {
    new PerformanceObserver(list => {
        for (const e of list.getEntries()) window.__fw_perf.lcp = e.startTime;
    }).observe({ type: 'largest-contentful-paint', buffered: true });
    new PerformanceObserver(list => {
        for (const e of list.getEntries())
            if (!e.hadRecentInput) window.__fw_perf.cls += e.value;
    }).observe({ type: 'layout-shift', buffered: true });
} catch(e) {}
"""

_COLLECT_JS = """
() => {
    const nav = performance.getEntriesByType('navigation')[0] || {};
    const p   = window.__fw_perf || {};
    return {
        ttfb:       Math.round(nav.responseStart          || 0),
        dom_loaded: Math.round(nav.domContentLoadedEventEnd || 0),
        load_time:  Math.round(nav.loadEventEnd           || 0),
        lcp:        Math.round(p.lcp || 0),
        cls:        Math.round((p.cls || 0) * 1000) / 1000
    };
}
"""


class PerformanceCollector:
    """Collect, store, and analyse page performance metrics."""

    def __init__(self):
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with self._conn() as c:
            c.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(_DB_PATH), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    # ── Public API ────────────────────────────────────────────────────────────

    def collect(self, page, test_id: str, run_ts: str) -> dict:
        """Evaluate navigation timing from the live page and persist it.

        Returns the metrics dict. Call this from the makereport hook BEFORE
        page teardown (when="call" phase).
        """
        from config.config import Config
        from datetime import datetime

        try:
            metrics = page.evaluate(_COLLECT_JS)
        except Exception as exc:
            logger.debug("Perf collect failed for %s: %s", test_id, exc)
            return {}

        metrics["page_url"] = page.url

        # Budget checks
        budgets = {}
        if metrics["lcp"] and metrics["lcp"] > Config.PERFORMANCE_LCP_BUDGET_MS:
            budgets["lcp"] = {
                "value": metrics["lcp"], "budget": Config.PERFORMANCE_LCP_BUDGET_MS
            }
        if metrics["load_time"] and metrics["load_time"] > Config.PERFORMANCE_LOAD_BUDGET_MS:
            budgets["load_time"] = {
                "value": metrics["load_time"], "budget": Config.PERFORMANCE_LOAD_BUDGET_MS
            }

        if budgets:
            logger.warning("Perf BUDGET exceeded [%s]: %s", test_id, budgets)

        metrics["budgets_exceeded"] = budgets

        try:
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO perf_results "
                    "(test_id, page_url, ttfb, dom_loaded, load_time, lcp, cls, run_ts, recorded) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        test_id,
                        metrics.get("page_url", ""),
                        metrics.get("ttfb", 0),
                        metrics.get("dom_loaded", 0),
                        metrics.get("load_time", 0),
                        metrics.get("lcp", 0),
                        metrics.get("cls", 0.0),
                        run_ts,
                        datetime.now().isoformat(),
                    ),
                )
            logger.info(
                "Perf [%s]: LCP=%dms, Load=%dms, TTFB=%dms, CLS=%.3f",
                test_id,
                metrics.get("lcp", 0),
                metrics.get("load_time", 0),
                metrics.get("ttfb", 0),
                metrics.get("cls", 0),
            )
        except Exception as exc:
            logger.warning("Perf persist failed: %s", exc)

        return metrics

    def get_trends(self, test_id: str, window: int = 10) -> list:
        """Return last N perf records for a test for trend analysis."""
        sql = """
            SELECT ttfb, dom_loaded, load_time, lcp, cls, run_ts
            FROM perf_results
            WHERE test_id = ?
            ORDER BY id DESC LIMIT ?
        """
        try:
            with self._conn() as conn:
                rows = conn.execute(sql, (test_id, window)).fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            logger.warning("Perf.get_trends failed: %s", exc)
            return []

    def get_run_summary(self, run_ts: str) -> list:
        """Return all perf records for a given run."""
        sql = """
            SELECT test_id, page_url, ttfb, dom_loaded, load_time, lcp, cls
            FROM perf_results WHERE run_ts = ?
            ORDER BY load_time DESC
        """
        try:
            with self._conn() as conn:
                rows = conn.execute(sql, (run_ts,)).fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            logger.warning("Perf.get_run_summary failed: %s", exc)
            return []
