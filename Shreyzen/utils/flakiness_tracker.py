"""
Flakiness Tracker — Capability #2

Records every test outcome to SQLite and surfaces tests with a flake rate
above the configured threshold. No external dependencies beyond stdlib.

DB location: logs_and_reports/flakiness.db
"""

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DB_PATH = Path("logs_and_reports/flakiness.db")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS test_results (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    test_id   TEXT    NOT NULL,
    outcome   TEXT    NOT NULL,  -- PASSED | FAILED | ERROR | SKIPPED
    duration  REAL    DEFAULT 0,
    run_ts    TEXT    NOT NULL,
    browser   TEXT    DEFAULT '',
    recorded  TEXT    NOT NULL   -- ISO timestamp of this record
);
CREATE INDEX IF NOT EXISTS idx_test_id ON test_results (test_id);
CREATE INDEX IF NOT EXISTS idx_run_ts  ON test_results (run_ts);
"""


class FlakinessTracker:
    """Thread-safe SQLite-backed test result recorder and flakiness analyser."""

    def __init__(self, db_path: Optional[Path] = None):
        self._db = db_path or _DB_PATH
        self._db.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ── DB bootstrap ──────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    # ── Write ─────────────────────────────────────────────────────────────────

    def record(
        self,
        test_id: str,
        outcome: str,
        duration: float = 0.0,
        run_ts: str = "",
        browser: str = "",
    ) -> None:
        """Persist a single test result."""
        try:
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO test_results (test_id, outcome, duration, run_ts, browser, recorded) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (test_id, outcome.upper(), duration, run_ts, browser,
                     datetime.now().isoformat()),
                )
        except Exception as exc:
            logger.warning("FlakinessTracker.record failed: %s", exc)

    # ── Read ──────────────────────────────────────────────────────────────────

    def get_flaky_tests(self, window: Optional[int] = None, threshold: Optional[float] = None):
        """Return list of dicts for tests exceeding the flake threshold.

        A test is flaky when it has BOTH passed and failed within the last
        `window` runs AND its failure rate exceeds `threshold`.
        """
        from config.config import Config
        w = window or Config.FLAKINESS_WINDOW
        t = threshold or Config.FLAKINESS_THRESHOLD

        sql = """
            WITH recent AS (
                SELECT test_id, outcome
                FROM (
                    SELECT test_id, outcome,
                           ROW_NUMBER() OVER (PARTITION BY test_id ORDER BY id DESC) AS rn
                    FROM test_results
                ) ranked
                WHERE rn <= ?
            ),
            stats AS (
                SELECT test_id,
                       COUNT(*) AS total,
                       SUM(CASE WHEN outcome IN ('FAILED','ERROR') THEN 1 ELSE 0 END) AS failures,
                       SUM(CASE WHEN outcome = 'PASSED' THEN 1 ELSE 0 END) AS passes
                FROM recent
                GROUP BY test_id
            )
            SELECT test_id,
                   total,
                   failures,
                   passes,
                   ROUND(CAST(failures AS REAL) / total, 3) AS flake_rate
            FROM stats
            WHERE passes > 0 AND failures > 0
              AND CAST(failures AS REAL) / total >= ?
            ORDER BY flake_rate DESC
        """
        try:
            with self._conn() as conn:
                rows = conn.execute(sql, (w, t)).fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            logger.warning("FlakinessTracker.get_flaky_tests failed: %s", exc)
            return []

    def get_stats(self, test_id: str) -> dict:
        """Return pass/fail/skip/avg_duration stats for a single test."""
        sql = """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN outcome = 'PASSED' THEN 1 ELSE 0 END) AS passed,
                SUM(CASE WHEN outcome IN ('FAILED','ERROR') THEN 1 ELSE 0 END) AS failed,
                SUM(CASE WHEN outcome = 'SKIPPED' THEN 1 ELSE 0 END) AS skipped,
                ROUND(AVG(duration), 2) AS avg_duration_s,
                MAX(recorded) AS last_run
            FROM test_results WHERE test_id = ?
        """
        try:
            with self._conn() as conn:
                row = conn.execute(sql, (test_id,)).fetchone()
            return dict(row) if row else {}
        except Exception as exc:
            logger.warning("FlakinessTracker.get_stats failed: %s", exc)
            return {}

    def get_run_summary(self, run_ts: str) -> dict:
        """Return aggregate pass/fail/skip counts for a specific run timestamp."""
        sql = """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN outcome = 'PASSED'  THEN 1 ELSE 0 END) AS passed,
                SUM(CASE WHEN outcome IN ('FAILED','ERROR') THEN 1 ELSE 0 END) AS failed,
                SUM(CASE WHEN outcome = 'SKIPPED' THEN 1 ELSE 0 END) AS skipped,
                ROUND(SUM(duration), 2) AS total_duration_s
            FROM test_results WHERE run_ts = ?
        """
        try:
            with self._conn() as conn:
                row = conn.execute(sql, (run_ts,)).fetchone()
            return dict(row) if row else {}
        except Exception as exc:
            logger.warning("FlakinessTracker.get_run_summary failed: %s", exc)
            return {}

    def get_recent_runs(self, limit: int = 10) -> list:
        """Return the most recent unique run timestamps with their summary stats."""
        sql = """
            SELECT run_ts,
                   COUNT(*) AS total,
                   SUM(CASE WHEN outcome IN ('FAILED','ERROR') THEN 1 ELSE 0 END) AS failed
            FROM test_results
            GROUP BY run_ts
            ORDER BY run_ts DESC
            LIMIT ?
        """
        try:
            with self._conn() as conn:
                rows = conn.execute(sql, (limit,)).fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            logger.warning("FlakinessTracker.get_recent_runs failed: %s", exc)
            return []
