"""
Load-run engine — launches Locust headless, streams live stats, writes reports.

Used by both entrypoints in `tools/load_runner.py`:
  * the launcher UI (via the threaded `LoadRunner`, which exposes a live snapshot)
  * the CI/CLI path (via `run_blocking`, which returns an exit-code-friendly result)

Locust is driven entirely through the CLI + `SHREYZEN_*` env (see load/shapes.py),
and its `--csv`/`--csv-full-history`/`--html` output is the single source of truth:
  <run>/stats_stats.csv          → final per-endpoint numbers (→ junit/json/allure)
  <run>/stats_stats_history.csv  → 1 Hz aggregated time series (→ live charts)
  <run>/report.html              → Locust's native HTML report
"""

import csv
import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import requests

from load import reporting
from load.catalog import SCENARIOS, resolve_params, RunParams
from utils import process_registry, run_context

_ROOT = Path(__file__).resolve().parent.parent
_RUNS_DIR = _ROOT / "logs_and_reports" / "load_runs"
_LOCUSTFILE = _ROOT / "load" / "locustfile.py"
_MOCK_SERVER = _ROOT / "tools" / "mock_api_server.py"

_WATCHDOG_GRACE_S = 45  # kill the subprocess this long after its planned end


# ── CSV parsing helpers (tolerant of header naming across Locust versions) ───

def _f(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _pick(row: dict, *candidates: str, default=""):
    for c in candidates:
        if c in row and row[c] not in (None, ""):
            return row[c]
    return default


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        with path.open(newline="") as fh:
            return list(csv.DictReader(fh))
    except Exception:
        return []


def parse_final_stats(prefix: Path) -> list[dict]:
    """Read <prefix>_stats.csv into normalized rows for report generation."""
    rows = []
    for r in _read_csv(Path(f"{prefix}_stats.csv")):
        name = _pick(r, "Name")
        if not name:
            continue
        rows.append(reporting.normalize_row(
            name=name,
            method=_pick(r, "Type"),
            num_requests=int(_f(_pick(r, "Request Count"))),
            num_failures=int(_f(_pick(r, "Failure Count"))),
            median_ms=_f(_pick(r, "Median Response Time", "50%")),
            avg_ms=_f(_pick(r, "Average Response Time")),
            p95_ms=_f(_pick(r, "95%", "95%ile")),
            rps=_f(_pick(r, "Requests/s")),
            min_ms=_f(_pick(r, "Min Response Time")),
            max_ms=_f(_pick(r, "Max Response Time")),
        ))
    return rows


def read_history_series(prefix: Path, first_ts: Optional[float] = None) -> list[dict]:
    """Read the aggregated rows of <prefix>_stats_history.csv as a time series."""
    rows = _read_csv(Path(f"{prefix}_stats_history.csv"))
    agg = [r for r in rows if _pick(r, "Name") == reporting.AGGREGATED]
    if not agg:
        return []
    base = first_ts if first_ts is not None else _f(_pick(agg[0], "Timestamp"))
    series = []
    for r in agg:
        ts = _f(_pick(r, "Timestamp"))
        series.append({
            "t": round(max(0.0, ts - base), 1),
            "users": int(_f(_pick(r, "User Count"))),
            "rps": round(_f(_pick(r, "Requests/s")), 2),
            "fps": round(_f(_pick(r, "Failures/s")), 2),
            "p95": round(_f(_pick(r, "95%", "95%ile")), 1),
            "avg": round(_f(_pick(r, "Total Average Response Time")), 1),
            "total_requests": int(_f(_pick(r, "Total Request Count"))),
            "total_failures": int(_f(_pick(r, "Total Failure Count"))),
        })
    return series


def live_endpoints(prefix: Path) -> list[dict]:
    """Current cumulative per-endpoint rows (Locust rewrites _stats.csv each tick)."""
    return [r for r in parse_final_stats(prefix) if r["name"] != reporting.AGGREGATED]


# ── Mock target auto-start ───────────────────────────────────────────────────

def _is_local(host: str) -> Optional[int]:
    """Return the port if `host` points at localhost, else None."""
    p = urlparse(host)
    if p.hostname in ("127.0.0.1", "localhost", "0.0.0.0"):
        return p.port or (443 if p.scheme == "https" else 80)
    return None


def _reachable(host: str) -> bool:
    try:
        requests.get(f"{host.rstrip('/')}/ping", timeout=1.5)
        return True
    except requests.RequestException:
        return False


def ensure_target(host: str, log) -> Optional[subprocess.Popen]:
    """
    If `host` is local and not already answering, start the mock API server.
    Returns the spawned process (so the caller can stop it), or None.
    """
    port = _is_local(host)
    if port is None or _reachable(host):
        return None
    log(f"Target {host} not reachable — starting mock API server on :{port}")
    proc = subprocess.Popen(
        [sys.executable, str(_MOCK_SERVER), "--port", str(port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    process_registry.register(proc, f"mock-api:{port}")
    for _ in range(20):  # up to ~10s
        if _reachable(host):
            log("Mock API server is up.")
            return proc
        time.sleep(0.5)
    log("WARNING: mock API server did not come up in time.")
    return proc


# ── Command building ─────────────────────────────────────────────────────────

def build_command(params: RunParams, run_dir: Path) -> tuple[list[str], dict]:
    user_class = SCENARIOS[params.scenario].user_class
    prefix = run_dir / "stats"
    cmd = [
        sys.executable, "-m", "locust",
        "-f", str(_LOCUSTFILE),
        "--headless",
        "--host", params.host,
        "--html", str(run_dir / "report.html"),
        "--csv", str(prefix),
        "--csv-full-history",
        "--only-summary",
        user_class,
    ]
    env = os.environ.copy()
    env.update({
        "SHREYZEN_PROFILE": params.profile,
        "SHREYZEN_USERS": str(params.users),
        "SHREYZEN_DURATION": str(params.duration),
        "SHREYZEN_SPAWN_RATE": str(params.spawn_rate),
        "PYTHONPATH": str(_ROOT) + os.pathsep + env.get("PYTHONPATH", ""),
    })
    return cmd, env


def _new_run_id(params: RunParams) -> str:
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return f"{stamp}__{params.scenario}__{params.profile}"


# ── Threaded runner for the UI ───────────────────────────────────────────────

class LoadRunner:
    """Owns at most one active load run; exposes a JSON-serializable snapshot."""

    def __init__(self):
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._mock: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.state: dict = {"status": "idle"}

    # -- public API ----------------------------------------------------------

    def is_active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, params: RunParams) -> str:
        if self.is_active():
            raise RuntimeError("A load run is already in progress.")
        run_id = _new_run_id(params)
        run_dir = _RUNS_DIR / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        self._stop.clear()
        self.state = {
            "run_id": run_id,
            "status": "starting",
            "scenario": params.scenario,
            "profile": params.profile,
            "params": params.as_dict(),
            "host": params.host,
            "started_at": time.time(),
            "ended_at": None,
            "series": [],
            "endpoints": [],
            "verdict": None,
            "passed": None,
            "reports": {},
            "log": [],
            "error": None,
        }
        self._thread = threading.Thread(target=self._run, args=(params, run_dir), daemon=True)
        self._thread.start()
        return run_id

    def stop(self):
        self._stop.set()
        self._signal(signal.SIGINT)  # let Locust flush its reports

    def snapshot(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self.state, default=str))

    # -- internals -----------------------------------------------------------

    def _log(self, msg: str):
        with self._lock:
            self.state.setdefault("log", []).append(
                f"{datetime.now().strftime('%H:%M:%S')}  {msg}"
            )
            self.state["log"] = self.state["log"][-200:]

    def _set(self, **kw):
        with self._lock:
            self.state.update(kw)

    def _signal(self, sig):
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.send_signal(sig)
            except Exception:
                pass

    def _run(self, params: RunParams, run_dir: Path):
        prefix = run_dir / "stats"
        try:
            self._mock = ensure_target(params.host, self._log)
            cmd, env = build_command(params, run_dir)
            self._log(f"Launching Locust: {params.scenario} · {params.profile} · "
                      f"peak {params.users} VUs · {params.duration}s")
            self._proc = subprocess.Popen(
                cmd, cwd=str(_ROOT), env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            process_registry.register(self._proc, f"load:{self.state['run_id']}")
            self._set(status="running")

            deadline = time.time() + params.duration + _WATCHDOG_GRACE_S
            first_ts = None
            while self._proc.poll() is None:
                if time.time() > deadline:
                    self._log("Watchdog: run exceeded planned duration — stopping.")
                    self._signal(signal.SIGINT)
                    break
                time.sleep(1.5)
                series = read_history_series(prefix, first_ts)
                if series and first_ts is None:
                    first_ts = None  # base already applied inside helper
                self._set(series=series, endpoints=live_endpoints(prefix))
            self._proc.wait(timeout=_WATCHDOG_GRACE_S)

            self._finalize(params, run_dir, prefix)
        except Exception as exc:  # pragma: no cover - defensive
            self._log(f"ERROR: {exc}")
            self._set(status="failed", error=str(exc), ended_at=time.time())
        finally:
            if self._proc is not None:
                process_registry.unregister(self._proc)
            self._cleanup_mock()

    def _finalize(self, params: RunParams, run_dir: Path, prefix: Path):
        self._set(status="finalizing")
        rows = parse_final_stats(prefix)
        started = self.state.get("started_at", time.time())
        ended = time.time()
        meta = {
            "run_id": self.state["run_id"],
            "scenario": params.scenario,
            "profile": params.profile,
            "users": params.users,
            "duration_s": round(ended - started, 1),
            "planned_duration_s": params.duration,
            "spawn_rate": params.spawn_rate,
            "host": params.host,
            "start_epoch_ms": int(started * 1000),
            "stop_epoch_ms": int(ended * 1000),
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "context": run_context.capture({
                "scenario": params.scenario, "profile": params.profile,
                "target": params.host,
            }),
        }
        if not rows:
            self._log("No stats produced — the run may have failed to start.")
            self._set(status="failed", passed=False, ended_at=ended,
                      error="Locust produced no statistics.")
            return
        verdict = reporting.write_reports(run_dir, meta, rows)
        # Persist a compact row to the central results DB.
        try:
            from utils import results_db
            results_db.record_load(meta, verdict)
        except Exception:
            pass
        self._set(
            status="completed",
            ended_at=ended,
            verdict=verdict,
            passed=verdict["passed"],
            series=read_history_series(prefix),
            endpoints=[r for r in rows if r["name"] != reporting.AGGREGATED],
            reports={
                "html": str((run_dir / "report.html").relative_to(_ROOT)),
                "json": str((run_dir / "results.json").relative_to(_ROOT)),
                "junit": str((run_dir / "junit.xml").relative_to(_ROOT)),
                "allure": str((run_dir / "allure-results").relative_to(_ROOT)),
            },
        )
        self._log(f"Done — verdict: {'PASS' if verdict['passed'] else 'FAIL'}")
        # Enforce retention caps so run artifacts don't grow unbounded.
        from utils.retention import auto_prune
        auto_prune(log=self._log)

    def _cleanup_mock(self):
        if self._mock and self._mock.poll() is None:
            self._mock.terminate()
        if self._mock is not None:
            process_registry.unregister(self._mock)
            self._mock = None


# ── Blocking runner for CLI / CI ─────────────────────────────────────────────

def run_blocking(params: RunParams, *, quiet: bool = False) -> dict:
    """
    Run a load test to completion in the foreground and return its result dict
    (including `passed`). Used by `tools/load_runner.py run` for CI gating.
    """
    def log(msg):
        if not quiet:
            print(f"[shreyzen-load] {msg}")

    run_id = _new_run_id(params)
    run_dir = _RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    prefix = run_dir / "stats"

    mock = ensure_target(params.host, log)
    started = time.time()
    proc = None
    try:
        cmd, env = build_command(params, run_dir)
        log(f"Locust: {params.scenario} · {params.profile} · peak {params.users} VUs "
            f"· {params.duration}s · {params.host}")
        proc = subprocess.Popen(cmd, cwd=str(_ROOT), env=env)
        process_registry.register(proc, f"load:{run_id}")
        try:
            proc.wait(timeout=params.duration + _WATCHDOG_GRACE_S)
        except subprocess.TimeoutExpired:
            log("Watchdog: stopping over-running Locust process.")
            proc.send_signal(signal.SIGINT)
            proc.wait(timeout=_WATCHDOG_GRACE_S)
    finally:
        if proc is not None:
            process_registry.unregister(proc)
        if mock and mock.poll() is None:
            mock.terminate()
        if mock is not None:
            process_registry.unregister(mock)

    ended = time.time()
    rows = parse_final_stats(prefix)
    meta = {
        "run_id": run_id, "scenario": params.scenario, "profile": params.profile,
        "users": params.users, "duration_s": round(ended - started, 1),
        "planned_duration_s": params.duration, "spawn_rate": params.spawn_rate,
        "host": params.host, "start_epoch_ms": int(started * 1000),
        "stop_epoch_ms": int(ended * 1000),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "context": run_context.capture({
            "scenario": params.scenario, "profile": params.profile, "target": params.host,
        }),
    }
    if not rows:
        log("ERROR: no statistics produced.")
        return {"run_id": run_id, "passed": False, "run_dir": str(run_dir),
                "error": "no statistics"}
    verdict = reporting.write_reports(run_dir, meta, rows)
    agg = next((r for r in rows if r["name"] == reporting.AGGREGATED), {})
    log(f"Requests={agg.get('num_requests', 0)} "
        f"Failures={agg.get('num_failures', 0)} "
        f"p95={agg.get('p95_ms', 0)}ms → {'PASS' if verdict['passed'] else 'FAIL'}")
    log(f"Reports in {run_dir}")
    return {"run_id": run_id, "passed": verdict["passed"],
            "run_dir": str(run_dir), "verdict": verdict, "meta": meta}


# ── Run history (for the UI) ─────────────────────────────────────────────────

def list_runs(limit: int = 50) -> list[dict]:
    if not _RUNS_DIR.exists():
        return []
    out = []
    for d in sorted(_RUNS_DIR.iterdir(), reverse=True):
        rj = d / "results.json"
        if not rj.exists():
            continue
        try:
            data = json.loads(rj.read_text())
        except Exception:
            continue
        out.append({
            "run_id": data.get("meta", {}).get("run_id", d.name),
            "scenario": data.get("meta", {}).get("scenario"),
            "profile": data.get("meta", {}).get("profile"),
            "passed": data.get("passed"),
            "summary": data.get("summary", {}),
            "context": data.get("meta", {}).get("context", {}),
            "timestamp": data.get("meta", {}).get("timestamp"),
        })
        if len(out) >= limit:
            break
    return out
