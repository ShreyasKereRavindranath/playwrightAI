"""
Central results database — Capability: durable, queryable run history.

Run summaries live as JSON files under logs_and_reports/{functional_runs,
load_runs}/ (and are swept by retention). This module additionally persists a
compact row per run into SQLite so History / Compare / Analytics can query one
place instead of scanning the filesystem — and so history survives artifact
pruning.

Shares logs_and_reports/flakiness.db with the flakiness + perf tables (one DB,
different tables). SQLite comfortably holds millions of rows; a run summary row
is ~1 KB, so this never becomes the capacity constraint (raw videos/HTML do —
that's what retention handles).

Tables:
  run_summaries   one row per functional/load run (counts, verdict, context)

Populated automatically from the engines' finalize step; also backfillable from
existing JSON via `backfill_from_files()`.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
_DB_PATH = _ROOT / "logs_and_reports" / "flakiness.db"
_FUNC_RUNS = _ROOT / "logs_and_reports" / "functional_runs"
_LOAD_RUNS = _ROOT / "logs_and_reports" / "load_runs"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS run_summaries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL UNIQUE,
    kind        TEXT NOT NULL,              -- 'functional' | 'load'
    passed      INTEGER NOT NULL DEFAULT 0, -- 1/0 overall verdict
    total       INTEGER DEFAULT 0,
    failures    INTEGER DEFAULT 0,
    duration_s  REAL DEFAULT 0,
    scenario    TEXT DEFAULT '',            -- load: scenario; functional: type_prefix
    profile     TEXT DEFAULT '',            -- load only
    browser     TEXT DEFAULT '',
    llm_model   TEXT DEFAULT '',
    user        TEXT DEFAULT '',
    host        TEXT DEFAULT '',
    timestamp   TEXT DEFAULT '',
    payload     TEXT DEFAULT '{}',          -- full summary JSON for detail views
    recorded    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runsum_kind ON run_summaries (kind);
CREATE INDEX IF NOT EXISTS idx_runsum_ts   ON run_summaries (timestamp);
"""


def _conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _conn() as c:
        c.executescript(_SCHEMA)


def _now_iso() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


def record_functional(summary: dict) -> None:
    """Persist a functional run summary (the dict written to summary.json)."""
    ctx = summary.get("context", {}) or {}
    counts = summary.get("counts", {}) or {}
    _upsert({
        "run_id": summary.get("run_id", ""),
        "kind": "functional",
        "passed": 1 if summary.get("passed") else 0,
        "total": counts.get("tests", 0),
        "failures": counts.get("failed", 0) + counts.get("errors", 0),
        "duration_s": summary.get("duration_s", 0),
        "scenario": summary.get("type_prefix", ""),
        "profile": "",
        "browser": ctx.get("browser", ""),
        "llm_model": ctx.get("llm_model", ""),
        "user": ctx.get("user", ""),
        "host": (summary.get("target", {}) or {}).get("base_url", ""),
        "timestamp": summary.get("timestamp", ""),
        "payload": json.dumps(summary),
    })


def record_load(meta: dict, verdict: dict) -> None:
    """Persist a load run summary from the engine's meta + verdict dicts."""
    ctx = meta.get("context", {}) or {}
    # The engine's verdict (from reporting.evaluate) carries per-endpoint rows,
    # not a pre-aggregated summary; the JSON backfill path carries `summary`.
    # Support both shapes.
    summ = verdict.get("summary") or {}
    if summ:
        total = summ.get("total_requests", 0)
        failures = summ.get("total_failures", 0)
    else:
        endpoints = verdict.get("endpoints", []) or []
        total = sum(e.get("num_requests", 0) for e in endpoints)
        failures = sum(e.get("num_failures", 0) for e in endpoints)
    _upsert({
        "run_id": meta.get("run_id", ""),
        "kind": "load",
        "passed": 1 if verdict.get("passed") else 0,
        "total": total,
        "failures": failures,
        "duration_s": meta.get("duration_s", 0),
        "scenario": meta.get("scenario", ""),
        "profile": meta.get("profile", ""),
        "browser": ctx.get("browser", ""),
        "llm_model": ctx.get("llm_model", ""),
        "user": ctx.get("user", ""),
        "host": meta.get("host", ""),
        "timestamp": meta.get("timestamp", ""),
        "payload": json.dumps({"meta": meta, "verdict": verdict}),
    })


def _upsert(row: dict) -> None:
    if not row.get("run_id"):
        return
    try:
        init_db()
        with _conn() as c:
            c.execute("""
                INSERT INTO run_summaries
                    (run_id, kind, passed, total, failures, duration_s, scenario,
                     profile, browser, llm_model, user, host, timestamp, payload, recorded)
                VALUES
                    (:run_id, :kind, :passed, :total, :failures, :duration_s, :scenario,
                     :profile, :browser, :llm_model, :user, :host, :timestamp, :payload, :recorded)
                ON CONFLICT(run_id) DO UPDATE SET
                    passed=excluded.passed, total=excluded.total, failures=excluded.failures,
                    duration_s=excluded.duration_s, scenario=excluded.scenario,
                    profile=excluded.profile, browser=excluded.browser,
                    llm_model=excluded.llm_model, user=excluded.user, host=excluded.host,
                    timestamp=excluded.timestamp, payload=excluded.payload, recorded=excluded.recorded
            """, {**row, "recorded": _now_iso()})
        logger.debug("run_summaries: recorded %s", row["run_id"])
    except Exception as exc:
        logger.warning("results_db upsert failed for %s: %s", row.get("run_id"), exc)


def list_runs(kind: Optional[str] = None, limit: int = 100) -> list[dict]:
    """Return run summary rows (newest first), optionally filtered by kind."""
    try:
        init_db()
        with _conn() as c:
            if kind:
                rows = c.execute(
                    "SELECT * FROM run_summaries WHERE kind=? ORDER BY timestamp DESC, id DESC LIMIT ?",
                    (kind, limit)).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM run_summaries ORDER BY timestamp DESC, id DESC LIMIT ?",
                    (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d.pop("payload", None)  # keep list responses light
            out.append(d)
        return out
    except Exception as exc:
        logger.warning("results_db list_runs failed: %s", exc)
        return []


def get_run(run_id: str) -> Optional[dict]:
    """Return the full stored summary (including payload) for one run."""
    try:
        init_db()
        with _conn() as c:
            r = c.execute("SELECT * FROM run_summaries WHERE run_id=?", (run_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        try:
            d["payload"] = json.loads(d.get("payload") or "{}")
        except Exception:
            d["payload"] = {}
        return d
    except Exception as exc:
        logger.warning("results_db get_run failed: %s", exc)
        return None


def stats() -> dict:
    """Aggregate counts for a quick health view."""
    try:
        init_db()
        with _conn() as c:
            total = c.execute("SELECT COUNT(*) FROM run_summaries").fetchone()[0]
            by_kind = {r["kind"]: r["n"] for r in c.execute(
                "SELECT kind, COUNT(*) AS n FROM run_summaries GROUP BY kind").fetchall()}
            pass_rate = c.execute(
                "SELECT ROUND(AVG(passed)*100,1) FROM run_summaries").fetchone()[0]
        return {"total_runs": total, "by_kind": by_kind, "pass_rate": pass_rate or 0}
    except Exception as exc:
        logger.warning("results_db stats failed: %s", exc)
        return {"total_runs": 0, "by_kind": {}, "pass_rate": 0}


def backfill_from_files() -> dict:
    """
    Import any run summaries on disk that aren't in the DB yet. Idempotent
    (uses run_id upsert). Returns counts of what was imported.
    """
    imported = {"functional": 0, "load": 0}
    # Functional: functional_runs/<id>/summary.json
    if _FUNC_RUNS.exists():
        for d in _FUNC_RUNS.iterdir():
            sj = d / "summary.json"
            if sj.exists():
                try:
                    record_functional(json.loads(sj.read_text()))
                    imported["functional"] += 1
                except Exception:
                    continue
    # Load: load_runs/<id>/results.json (engine's results file)
    if _LOAD_RUNS.exists():
        for d in _LOAD_RUNS.iterdir():
            rj = d / "results.json"
            if rj.exists():
                try:
                    data = json.loads(rj.read_text())
                    meta = data.get("meta", {})
                    verdict = {"passed": data.get("passed"),
                               "summary": data.get("summary", {})}
                    if meta.get("run_id"):
                        record_load(meta, verdict)
                        imported["load"] += 1
                except Exception:
                    continue
    logger.info("results_db backfill: %s", imported)
    return imported
