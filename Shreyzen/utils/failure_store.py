"""
Failure store — durable per-failure records for root-cause clustering.

The flakiness tracker records *outcomes*; this records the *why* — each failed
test's message + traceback — so `utils/failure_cluster.py` can group recurring
failures and triage them. Backed by a `test_failures` table in the same
SQLite file as flakiness (logs_and_reports/flakiness.db), stdlib-only.

Written from tests/conftest.py when FAILURE_TRACKING is on (default). The
`db_path` is injectable so it's unit-testable against a temp file.
"""

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DB_PATH = Path("logs_and_reports/flakiness.db")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS test_failures (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    test_id     TEXT    NOT NULL,
    run_ts      TEXT    NOT NULL,
    browser     TEXT    DEFAULT '',
    message     TEXT    DEFAULT '',   -- one-line failure summary
    traceback   TEXT    DEFAULT '',   -- full longrepr (truncated)
    diagnostics TEXT    DEFAULT '',   -- browser console/JS/network signal (see browser_diagnostics)
    recorded    TEXT    NOT NULL      -- ISO timestamp of this record
);
CREATE INDEX IF NOT EXISTS idx_fail_test ON test_failures (test_id);
CREATE INDEX IF NOT EXISTS idx_fail_run  ON test_failures (run_ts);
"""

_MAX_TRACEBACK = 4000
_MAX_DIAGNOSTICS = 2000


class FailureStore:
    """SQLite-backed store of individual test failures (message + traceback)."""

    def __init__(self, db_path: Optional[Path] = None):
        self._db = db_path or _DB_PATH
        self._db.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(_SCHEMA)
            self._migrate(conn)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Add columns introduced after the first release to pre-existing DBs."""
        try:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(test_failures)")}
            if "diagnostics" not in cols:
                conn.execute("ALTER TABLE test_failures ADD COLUMN diagnostics TEXT DEFAULT ''")
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("FailureStore migration skipped: %s", exc)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def record(self, test_id: str, message: str = "", traceback: str = "",
               run_ts: str = "", browser: str = "", diagnostics: str = "") -> None:
        """Persist one failure. Never raises into the test run."""
        try:
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO test_failures "
                    "(test_id, run_ts, browser, message, traceback, diagnostics, recorded) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (test_id, run_ts, browser, (message or "")[:500],
                     (traceback or "")[:_MAX_TRACEBACK],
                     (diagnostics or "")[:_MAX_DIAGNOSTICS], datetime.now().isoformat()),
                )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("FailureStore.record failed: %s", exc)

    def recent(self, limit: int = 200) -> list:
        """Most recent failures (newest first), as dicts."""
        try:
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT test_id, run_ts, browser, message, traceback, diagnostics, recorded "
                    "FROM test_failures ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("FailureStore.recent failed: %s", exc)
            return []

    def for_run(self, run_ts: str) -> list:
        """All failures recorded for a given run timestamp."""
        try:
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT test_id, run_ts, browser, message, traceback, diagnostics, recorded "
                    "FROM test_failures WHERE run_ts = ? ORDER BY id DESC", (run_ts,)
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("FailureStore.for_run failed: %s", exc)
            return []
