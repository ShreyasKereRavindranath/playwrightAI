"""
Regression detector — Capability: trend-aware alerting.

The dashboard already charts trends; this module turns those trends into
*decisions*. It compares the latest run against a baseline (the median of the
prior N runs) and flags statistically meaningful regressions:

  * pass_rate    dropped by more than REGRESSION_PASS_RATE_DROP percentage points
  * duration     rose by more than REGRESSION_DURATION_PCT percent
  * perf (LCP / load_time)  rose by more than REGRESSION_PERF_PCT percent

Baseline = median (robust to a single outlier run). A regression needs at least
REGRESSION_MIN_HISTORY prior runs, otherwise there isn't enough signal and we
stay silent rather than cry wolf.

Data sources mirror the dashboard:
  logs_and_reports/runs/*.json      → pass_rate, duration_s
  logs_and_reports/flakiness.db     → perf_results (avg lcp / load_time per run)
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import Optional

from utils.logger import get_logger

logger = get_logger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
_RUNS = _ROOT / "logs_and_reports" / "runs"
_DB = _ROOT / "logs_and_reports" / "flakiness.db"


@dataclass
class Regression:
    metric: str          # "pass_rate" | "duration" | "lcp" | "load_time"
    latest: float
    baseline: float
    delta: float         # signed change (latest - baseline)
    unit: str            # "pp" | "%" | "ms"
    severity: str        # "warning" | "critical"
    message: str


@dataclass
class RegressionReport:
    latest_run: Optional[str] = None
    baseline_runs: int = 0
    regressions: list[Regression] = field(default_factory=list)

    @property
    def has_regressions(self) -> bool:
        return bool(self.regressions)

    def as_dict(self) -> dict:
        return {
            "latest_run": self.latest_run,
            "baseline_runs": self.baseline_runs,
            "has_regressions": self.has_regressions,
            "regressions": [
                {
                    "metric": r.metric, "latest": r.latest, "baseline": r.baseline,
                    "delta": round(r.delta, 2), "unit": r.unit,
                    "severity": r.severity, "message": r.message,
                }
                for r in self.regressions
            ],
        }


def _load_runs() -> list[dict]:
    if not _RUNS.exists():
        return []
    runs = []
    for f in sorted(_RUNS.glob("run_*.json")):
        try:
            data = json.loads(f.read_text())
            if data.get("total"):  # skip empty/no-test runs
                runs.append(data)
        except Exception:
            continue
    runs.sort(key=lambda r: r.get("run_ts", ""))
    return runs


def _perf_by_run() -> list[dict]:
    if not _DB.exists():
        return []
    try:
        conn = sqlite3.connect(str(_DB), timeout=5)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT run_ts,
                   ROUND(AVG(lcp), 0)       AS avg_lcp,
                   ROUND(AVG(load_time), 0) AS avg_load_time
            FROM perf_results GROUP BY run_ts ORDER BY run_ts
        """).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.debug("perf_by_run failed: %s", exc)
        return []


def detect_regressions(
    *,
    pass_rate_drop: float = 5.0,
    duration_pct: float = 25.0,
    perf_pct: float = 25.0,
    min_history: int = 3,
) -> RegressionReport:
    """
    Compare the most recent run to the median of the runs before it.

    Thresholds fall back to Config when called via detect_with_config(); here
    they're plain args so the detector is easy to unit-test.
    """
    report = RegressionReport()
    runs = _load_runs()
    if len(runs) < min_history + 1:
        report.baseline_runs = max(0, len(runs) - 1)
        return report  # not enough history — stay silent

    latest = runs[-1]
    baseline = runs[-(min_history + 1):-1]  # the N runs immediately before latest
    report.latest_run = latest.get("run_ts")
    report.baseline_runs = len(baseline)

    # ── pass_rate (higher is better; regression = drop) ───────────────────────
    base_pr = median(r.get("pass_rate", 0) for r in baseline)
    cur_pr = latest.get("pass_rate", 0)
    if base_pr - cur_pr >= pass_rate_drop:
        drop = base_pr - cur_pr
        report.regressions.append(Regression(
            metric="pass_rate", latest=cur_pr, baseline=round(base_pr, 1),
            delta=cur_pr - base_pr, unit="pp",
            severity="critical" if drop >= 2 * pass_rate_drop else "warning",
            message=f"Pass rate dropped {drop:.1f}pp ({base_pr:.1f}% → {cur_pr:.1f}%)",
        ))

    # ── duration (lower is better; regression = rise) ─────────────────────────
    base_dur = median(r.get("duration_s", 0) for r in baseline)
    cur_dur = latest.get("duration_s", 0)
    if base_dur > 0 and cur_dur > base_dur * (1 + duration_pct / 100):
        rise = (cur_dur - base_dur) / base_dur * 100
        report.regressions.append(Regression(
            metric="duration", latest=cur_dur, baseline=round(base_dur, 1),
            delta=cur_dur - base_dur, unit="%",
            severity="critical" if rise >= 2 * duration_pct else "warning",
            message=f"Suite {rise:.0f}% slower ({base_dur:.1f}s → {cur_dur:.1f}s)",
        ))

    # ── perf: LCP + load_time (lower is better; regression = rise) ────────────
    perf = _perf_by_run()
    if len(perf) >= min_history + 1:
        p_latest = perf[-1]
        p_base = perf[-(min_history + 1):-1]
        for key, label in (("avg_lcp", "lcp"), ("avg_load_time", "load_time")):
            base_v = median(p.get(key, 0) for p in p_base)
            cur_v = p_latest.get(key, 0)
            if base_v > 0 and cur_v > base_v * (1 + perf_pct / 100):
                rise = (cur_v - base_v) / base_v * 100
                report.regressions.append(Regression(
                    metric=label, latest=cur_v, baseline=round(base_v, 0),
                    delta=cur_v - base_v, unit="ms",
                    severity="critical" if rise >= 2 * perf_pct else "warning",
                    message=f"{label.upper()} {rise:.0f}% worse "
                            f"({base_v:.0f}ms → {cur_v:.0f}ms)",
                ))

    if report.regressions:
        logger.warning("Detected %d regression(s) in run %s",
                       len(report.regressions), report.latest_run)
    return report


def detect_with_config() -> RegressionReport:
    """detect_regressions() using thresholds from Config/.env."""
    from config.config import Config
    return detect_regressions(
        pass_rate_drop=Config.REGRESSION_PASS_RATE_DROP,
        duration_pct=Config.REGRESSION_DURATION_PCT,
        perf_pct=Config.REGRESSION_PERF_PCT,
        min_history=Config.REGRESSION_MIN_HISTORY,
    )
