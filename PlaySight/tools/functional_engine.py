"""
Functional test engine for the PlaySight runner.

Runs the pytest suites (api / web / mobile) as subprocesses so the launcher UI can
offer a unified "pick any test, run against any target" experience alongside the
Locust load scenarios. Mirrors load/engine.py's threaded design:

  * discover_tests()      → the selectable test tree, grouped by layer
  * FunctionalRunner      → threaded run with a live pass/fail snapshot for the UI
  * list_runs()           → past functional runs for the History view

Live progress is parsed from pytest's verbose stdout (``… PASSED [ 42%]``); the
authoritative per-test result set is parsed from the JUnit XML once the run ends.
Every run writes html + junit + json + allure to
logs_and_reports/functional_runs/<id>/.
"""

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Optional

from load.engine import ensure_target  # reuse the mock-API auto-start helper
from utils import process_registry, run_context

_ROOT = Path(__file__).resolve().parent.parent
_RUNS_DIR = _ROOT / "logs_and_reports" / "functional_runs"

LAYERS = ("api", "web", "mobile")
# Human-facing prefix token per layer, used in report titles and the run history.
_LAYER_PREFIX = {"api": "API_", "web": "WEB_", "mobile": "MOBILE_"}
_STATUS_RE = re.compile(r"\b(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\b")
_PCT_RE = re.compile(r"\[\s*(\d+)%\]")
_RUN_TIMEOUT_S = 1800  # hard cap so a hung browser test can't run forever


def layers_for(selection: list[str]) -> list[str]:
    """The layers (api/web/mobile) a selection touches, in canonical order."""
    return [layer for layer in LAYERS
            if any(n.startswith(f"tests/{layer}") for n in selection)]


def type_prefix(selection: list[str]) -> str:
    """Concatenated type prefix for a selection, e.g. 'WEB_API_' (or '' if none)."""
    return "".join(_LAYER_PREFIX[layer] for layer in layers_for(selection))


# ── Discovery ────────────────────────────────────────────────────────────────

def discover_tests() -> dict:
    """Collect the suite and return selectable node ids grouped by layer."""
    groups: dict[str, list] = {layer: [] for layer in LAYERS}
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "tests", "--collect-only", "-q",
             "-p", "no:cacheprovider", "-o", "addopts="],
            cwd=str(_ROOT), capture_output=True, text=True, timeout=120,
        )
        lines = proc.stdout.splitlines()
    except (subprocess.SubprocessError, OSError):
        return groups

    for line in lines:
        line = line.strip()
        if "::" not in line:
            continue
        for layer in LAYERS:
            if line.startswith(f"tests/{layer}/") or line.startswith(f"tests/{layer}::"):
                groups[layer].append({"nodeid": line, "name": line.split("::", 1)[1]})
                break
    return groups


# ── Target → environment mapping ─────────────────────────────────────────────

def _build_env(selection: list[str], target: dict, log) -> tuple[dict, Optional[subprocess.Popen]]:
    """Translate the chosen target/spec into environment variables for pytest."""
    env = os.environ.copy()
    env["HEADLESS"] = "true" if target.get("headless", True) else "false"
    env["BROWSER"] = target.get("browser") or "chromium"
    env["RECORD_VIDEO"] = "false"
    # Config.validate() requires these; real UI creds come from data/*.json.
    env.setdefault("TEST_USER_EMAIL", "runner@example.com")
    env.setdefault("TEST_USER_PASSWORD", "runner-password")

    touches = {layer for layer in LAYERS if any(n.startswith(f"tests/{layer}") for n in selection)}

    if touches & {"web", "mobile"} and target.get("base_url"):
        env["BASE_URL"] = target["base_url"]
    if "mobile" in touches and target.get("mobile_device"):
        env["MOBILE_DEVICE"] = target["mobile_device"]

    mock = None
    if "api" in touches:
        api_target = target.get("api_target", "mock")
        if api_target == "mock":
            host = "http://127.0.0.1:8765"
            env["API_BASE_URL"] = host
            # Loopback must bypass any inherited HTTP(S) proxy, or the mock is unreachable.
            env["NO_PROXY"] = "127.0.0.1,localhost," + env.get("NO_PROXY", "")
            env["no_proxy"] = "127.0.0.1,localhost," + env.get("no_proxy", "")
            mock = ensure_target(host, log)
        elif api_target == "custom" and target.get("api_url"):
            env["API_BASE_URL"] = target["api_url"]
        # "public" → leave API_BASE_URL unset → conftest default (restful-booker)
    return env, mock


# ── JUnit parsing ────────────────────────────────────────────────────────────

def _parse_junit(path: Path) -> dict:
    if not path.exists():
        return {"tests": 0, "passed": 0, "failed": 0, "skipped": 0, "errors": 0, "cases": []}
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return {"tests": 0, "passed": 0, "failed": 0, "skipped": 0, "errors": 0, "cases": []}

    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    cases, failed, skipped, errors = [], 0, 0, 0
    for suite in suites:
        for tc in suite.findall("testcase"):
            if tc.find("error") is not None:
                status, errors = "error", errors + 1
            elif tc.find("failure") is not None:
                status, failed = "failed", failed + 1
            elif tc.find("skipped") is not None:
                status, skipped = "skipped", skipped + 1
            else:
                status = "passed"
            node = tc.find("failure") if status == "failed" else tc.find("error")
            cases.append({
                "name": tc.get("name", "?"),
                "classname": tc.get("classname", ""),
                "status": status,
                "time": round(float(tc.get("time", 0) or 0), 2),
                "message": (node.get("message", "") if node is not None else "")[:300],
            })
    total = len(cases)
    return {"tests": total, "passed": total - failed - skipped - errors,
            "failed": failed, "skipped": skipped, "errors": errors, "cases": cases}


def _new_run_id() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + "__functional"


# ── Threaded runner ──────────────────────────────────────────────────────────

class FunctionalRunner:
    """Owns at most one active functional (pytest) run; exposes a JSON snapshot."""

    def __init__(self):
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._mock: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.state: dict = {"status": "idle"}

    def is_active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, selection: list[str], target: dict) -> str:
        if self.is_active():
            raise RuntimeError("A functional run is already in progress.")
        if not selection:
            raise ValueError("No tests selected.")
        run_id = _new_run_id()
        run_dir = _RUNS_DIR / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        self._stop.clear()
        self.state = {
            "run_id": run_id, "status": "starting",
            "selection": selection, "target": target,
            "started_at": time.time(), "ended_at": None,
            "counts": {"tests": 0, "passed": 0, "failed": 0, "skipped": 0, "errors": 0},
            "percent": 0, "cases": [], "passed_overall": None,
            "reports": {}, "log": [], "error": None,
        }
        self._thread = threading.Thread(target=self._run, args=(selection, target, run_dir), daemon=True)
        self._thread.start()
        return run_id

    def stop(self):
        self._stop.set()
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.send_signal(signal.SIGINT)
            except Exception:
                pass

    def snapshot(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self.state, default=str))

    # -- internals --

    def _log(self, msg: str):
        with self._lock:
            self.state.setdefault("log", []).append(msg.rstrip())
            self.state["log"] = self.state["log"][-400:]

    def _set(self, **kw):
        with self._lock:
            self.state.update(kw)

    def _run(self, selection: list[str], target: dict, run_dir: Path):
        try:
            env, self._mock = _build_env(selection, target, self._log)
            # Type prefix (WEB_/API_/MOBILE_) drives the report title + run history.
            prefix = type_prefix(selection)
            report_title = f"{prefix}Functional Report — {self.state['run_id']}"
            env["PLAYSIGHT_REPORT_TITLE"] = report_title
            # Capture who/where/when + the user's selections for the reports.
            ctx = run_context.capture({
                "browser": target.get("browser"),
                "device": target.get("mobile_device") if any(s.startswith("tests/mobile") for s in selection) else None,
                "target": target.get("base_url"),
                "markers": target.get("markers") or "(all)",
                "selection": f"{len(selection)} test(s)",
            })
            (run_dir / "context.json").write_text(json.dumps(ctx, indent=2))
            cmd = [
                sys.executable, "-m", "pytest", *selection,
                "-v", "-p", "no:cacheprovider",
                "--html", str(run_dir / "report.html"), "--self-contained-html",
                "--junitxml", str(run_dir / "junit.xml"),
                "--alluredir", str(run_dir / "allure-results"),
            ]
            for key, val in ctx.items():  # pytest-metadata → pytest-html "Environment" table
                cmd += ["--metadata", str(key), str(val)]
            markers = (target.get("markers") or "").strip()
            if markers:
                cmd += ["-m", markers]
            self._log(f"$ pytest {' '.join(selection)}"
                      + (f" -m {markers}" if markers else ""))
            self._set(status="running", context=ctx)
            self._proc = subprocess.Popen(
                cmd, cwd=str(_ROOT), env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
            process_registry.register(self._proc, f"functional:{run_dir.name}")

            counts = {"passed": 0, "failed": 0, "skipped": 0, "errors": 0}
            deadline = time.time() + _RUN_TIMEOUT_S
            for line in self._proc.stdout:  # streams live
                self._log(line)
                m = _STATUS_RE.search(line)
                if m and "::" in line:
                    tok = m.group(1)
                    if tok in ("PASSED", "XPASS"):
                        counts["passed"] += 1
                    elif tok in ("FAILED",):
                        counts["failed"] += 1
                    elif tok == "ERROR":
                        counts["errors"] += 1
                    elif tok in ("SKIPPED", "XFAIL"):
                        counts["skipped"] += 1
                    pct = _PCT_RE.search(line)
                    self._set(
                        counts={"tests": sum(counts.values()), **counts},
                        percent=int(pct.group(1)) if pct else self.state.get("percent", 0),
                    )
                if time.time() > deadline:
                    self._log("Watchdog: run exceeded time limit — stopping.")
                    self.stop()
                    break
            self._proc.wait(timeout=60)
            self._finalize(target, run_dir)
        except Exception as exc:  # pragma: no cover - defensive
            self._log(f"ERROR: {exc}")
            self._set(status="failed", error=str(exc), ended_at=time.time())
        finally:
            if self._proc is not None:
                process_registry.unregister(self._proc)
            if self._mock and self._mock.poll() is None:
                self._mock.terminate()
            if self._mock is not None:
                process_registry.unregister(self._mock)
                self._mock = None

    def _finalize(self, target: dict, run_dir: Path):
        self._set(status="finalizing")
        junit = _parse_junit(run_dir / "junit.xml")
        ended = time.time()
        passed_overall = junit["tests"] > 0 and junit["failed"] == 0 and junit["errors"] == 0
        summary = {
            "run_id": self.state["run_id"],
            "selection": self.state["selection"],
            "types": layers_for(self.state["selection"]),
            "type_prefix": type_prefix(self.state["selection"]),
            "target": target,
            "context": self.state.get("context", {}),
            "counts": {k: junit[k] for k in ("tests", "passed", "failed", "skipped", "errors")},
            "passed": passed_overall,
            "duration_s": round(ended - self.state.get("started_at", ended), 1),
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }
        (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
        self._set(
            status="completed" if junit["tests"] else "failed",
            ended_at=ended,
            counts=summary["counts"],
            cases=junit["cases"],
            passed_overall=passed_overall,
            percent=100,
            reports={
                "html": (run_dir / "report.html").exists(),
                "junit": (run_dir / "junit.xml").exists(),
                "allure": (run_dir / "allure-results").exists(),
            },
        )
        if not junit["tests"]:
            self._set(error="No tests ran — check the selection/target.")
        self._log(f"Done — {summary['counts']['passed']} passed, "
                  f"{summary['counts']['failed']} failed, "
                  f"{summary['counts']['skipped']} skipped.")


def list_runs(limit: int = 50) -> list[dict]:
    if not _RUNS_DIR.exists():
        return []
    out = []
    for d in sorted(_RUNS_DIR.iterdir(), reverse=True):
        sj = d / "summary.json"
        if not sj.exists():
            continue
        try:
            out.append(json.loads(sj.read_text()))
        except Exception:
            continue
        if len(out) >= limit:
            break
    return out
