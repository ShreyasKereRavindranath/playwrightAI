#!/usr/bin/env python3
"""
Shreyzen Studio — a Cypress-style unified runner: functional + load tests,
analytics, and LLM-provider config, from one UI.

ONE command, even on a fresh checkout — everything else happens from the UI:

    python tools/studio.py serve                 # → http://127.0.0.1:8770

On first launch it installs requirements.txt automatically; the Playwright
browser auto-installs on the first web/mobile run; the mock API server
auto-starts whenever a run needs it; and all background processes stop cleanly
on exit. Then, in the browser:
  • Functional — pick api/web/mobile pytest tests, choose the target, Run.
  • Load — pick a scenario + one of 6 profiles, set the VUs, Run.
  • Analytics / Compare — trends across runs; AI Provider — pick the LLM.

The same engine also runs headless for CI (PR checks, manual runs, nightly soaks):

    python tools/studio.py run --scenario crud --profile smoke
    python tools/studio.py run --scenario journey --profile custom \
        --users 100 --duration 300 --host http://127.0.0.1:8765

`run` exits non-zero if the profile's thresholds are breached, so it gates CI.
Every run writes html + junit + json + allure (with run context) under logs_and_reports/.
(`tools/load_runner.py` remains as a backward-compatible alias.)
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _ensure_dependencies():
    """
    First-run bootstrap so a fresh checkout needs only ONE command.

    If the core third-party deps aren't importable, install requirements.txt into
    the current interpreter and re-exec. Playwright browsers and the mock API
    server install/start themselves later, on demand, from the UI — so the user
    never runs anything but `python tools/load_runner.py serve`.
    """
    try:
        import fastapi, uvicorn, pydantic, requests  # noqa: F401
        return
    except ImportError:
        pass
    if __name__ != "__main__":
        return  # imported as a library — let the normal ImportError surface below
    if os.environ.get("SHREYZEN_BOOTSTRAPPED") == "1":
        print("Dependencies still missing after install. "
              "Run manually: pip install -r requirements.txt", file=sys.stderr)
        sys.exit(1)
    req = Path(__file__).resolve().parent.parent / "requirements.txt"
    print("📦 First run — installing dependencies from requirements.txt "
          "(this happens once)…\n")
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(req)], check=True)
    except subprocess.CalledProcessError:
        print("\nCould not install dependencies automatically. "
              "Run: pip install -r requirements.txt", file=sys.stderr)
        sys.exit(1)
    os.environ["SHREYZEN_BOOTSTRAPPED"] = "1"
    print("\n✅ Dependencies installed — starting…\n")
    os.execv(sys.executable, [sys.executable, *sys.argv])  # restart with deps available


_ensure_dependencies()

try:
    import uvicorn
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
    from pydantic import BaseModel
except ImportError:
    print("Missing dependencies. Run: pip install -r requirements.txt")
    sys.exit(1)

# config.config calls load_dotenv() at import time, loading config/.env into the
# environment. Without this the Studio process never sees API keys from .env, so
# every LLM provider reports "needs config" and the status badge never changes
# when a key is added. Must run before get_service(). Config also exposes tunables
# used below (e.g. AI_MAX_TOKENS, NL_REPAIR_*).
from config.config import Config

from load.catalog import PROFILES, SCENARIOS, API_ENDPOINTS, resolve_params
from load.engine import LoadRunner, list_runs, run_blocking
from tools import functional_engine as fe
from utils import process_registry

_ROOT = Path(__file__).resolve().parent.parent
_RUNS_DIR = _ROOT / "logs_and_reports" / "load_runs"
_FUNC_RUNS = _ROOT / "logs_and_reports" / "functional_runs"
_DEFAULT_HOST = "http://127.0.0.1:8765"
_DEFAULT_WEB_URL = "https://www.saucedemo.com"

# Curated Playwright device list for the mobile-device dropdown.
MOBILE_DEVICES = ["Pixel 5", "Pixel 7", "Galaxy S9+", "Galaxy Tab S4",
                  "iPhone 13", "iPhone 14 Pro", "iPhone SE", "iPad (gen 7)", "iPad Pro 11"]
# Markers offered in the functional target dropdown (mirror pytest.ini).
MARKERS = ["", "smoke", "regression", "e2e", "negative", "accessibility"]

from contextlib import asynccontextmanager


@asynccontextmanager
async def _lifespan():
    """On server shutdown, stop every background process (nothing left hanging)."""
    yield
    _stop_everything()


app = FastAPI(title="Shreyzen Studio", lifespan=lambda _app: _lifespan())
_runner = LoadRunner()
_func_runner = fe.FunctionalRunner()


class _Recorder:
    """Owns at most one active codegen recording; exposes a JSON snapshot."""

    def __init__(self):
        import threading
        self._lock = threading.Lock()
        self._thread = None
        self.state = {"status": "idle"}

    def is_active(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, url: str, page_name: str, with_test: bool) -> None:
        import threading
        if self.is_active():
            raise RuntimeError("A recording is already in progress.")
        self.state = {"status": "recording", "url": url, "page_name": page_name,
                      "written": [], "errors": [], "page_object": "", "test": ""}
        self._thread = threading.Thread(
            target=self._run, args=(url, page_name, with_test), daemon=True)
        self._thread.start()

    def _run(self, url, page_name, with_test):
        from tools import record_generate as rg
        try:
            raw = rg.launch_codegen(url)
            with self._lock:
                self.state["status"] = "generating"
            out = rg.convert_recording(raw, page_name, with_test=with_test, write=True)
            with self._lock:
                self.state.update({
                    "status": "completed", "page_object": out["page_object"],
                    "test": out.get("test", ""), "written": out.get("written", []),
                    "errors": out.get("errors", []),
                    "page_path": out.get("page_path"), "test_path": out.get("test_path"),
                    "validation": out.get("validation"),
                })
        except Exception as exc:
            with self._lock:
                self.state.update({"status": "failed", "errors": [str(exc)]})

    def snapshot(self) -> dict:
        import json as _json
        with self._lock:
            return _json.loads(_json.dumps(self.state, default=str))


_recorder = _Recorder()


# ── API models ───────────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    scenario: str
    profile: str
    users: int | None = None
    duration: int | None = None
    spawn_rate: float | None = None
    host: str = _DEFAULT_HOST
    endpoints: list[str] | None = None


class FunctionalRequest(BaseModel):
    selection: list[str]
    base_url: str = _DEFAULT_WEB_URL
    api_target: str = "mock"          # mock | public | custom
    api_url: str | None = None
    mobile_device: str = "Pixel 5"
    browser: str = "chromium"
    headless: bool = True
    markers: str | None = None


class LLMSelectRequest(BaseModel):
    name: str


class RecordRequest(BaseModel):
    url: str
    page_name: str
    with_test: bool = True


class GenerateRequest(BaseModel):
    scenario: str
    page: str | None = None
    feature: str | None = None
    test_only: bool = False
    write: bool = True


# ── API endpoints ────────────────────────────────────────────────────────────

@app.get("/api/catalog")
def api_catalog():
    return {
        "scenarios": [
            {"key": s.key, "label": s.label, "icon": s.icon, "blurb": s.blurb}
            for s in SCENARIOS.values()
        ],
        "profiles": [
            {"key": p.key, "label": p.label, "icon": p.icon, "blurb": p.blurb,
             "default_users": p.default_users, "default_duration": p.default_duration,
             "default_spawn_rate": p.default_spawn_rate, "long_running": p.long_running,
             "max_fail_ratio": p.max_fail_ratio, "p95_budget_ms": p.p95_budget_ms}
            for p in PROFILES.values()
        ],
        "endpoints": [
            {"key": e.key, "label": e.label, "method": e.method, "path": e.path,
             "weight": e.weight}
            for e in API_ENDPOINTS.values()
        ],
        "defaults": {"host": _DEFAULT_HOST},
    }


@app.post("/api/run")
def api_run(req: RunRequest):
    if _runner.is_active():
        raise HTTPException(status_code=409, detail="A load run is already in progress.")
    try:
        params = resolve_params(
            req.scenario, req.profile,
            users=req.users, duration=req.duration,
            spawn_rate=req.spawn_rate, host=req.host,
            endpoints=req.endpoints,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    run_id = _runner.start(params)
    return {"run_id": run_id, "params": params.as_dict()}


@app.post("/api/stop")
def api_stop():
    _runner.stop()
    return {"stopped": True}


@app.get("/api/state")
def api_state():
    return _runner.snapshot()


@app.get("/api/runs")
def api_runs():
    return list_runs()


@app.get("/report/{run_id}", response_class=HTMLResponse)
def report(run_id: str):
    path = _safe_run_file(run_id, "report.html")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Report not found")
    return HTMLResponse(path.read_text())


@app.get("/file/{run_id}/{kind}")
def run_file(run_id: str, kind: str):
    mapping = {"json": "results.json", "junit": "junit.xml"}
    if kind not in mapping:
        raise HTTPException(status_code=400, detail="Unknown file kind")
    path = _safe_run_file(run_id, mapping[kind])
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    media = "application/json" if kind == "json" else "application/xml"
    return FileResponse(str(path), media_type=media)


def _safe_run_file(run_id: str, filename: str) -> Path:
    """Resolve a file inside a run dir, guarding against path traversal."""
    run_dir = (_RUNS_DIR / run_id).resolve()
    if not str(run_dir).startswith(str(_RUNS_DIR.resolve())):
        raise HTTPException(status_code=400, detail="Invalid run id")
    return run_dir / filename


@app.get("/allure/{run_id}", response_class=HTMLResponse)
def allure_report(run_id: str, refresh: int = 0):
    """One-click Allure report for a load run."""
    return _serve_allure(_RUNS_DIR, run_id, "/allure", refresh)


def _serve_allure(base: Path, run_id: str, link_prefix: str, refresh: int):
    """
    Generate a self-contained Allure report from a run's allure-results on first
    view (cached; ?refresh=1 rebuilds), then serve it inline. Requires the
    `allure` CLI on PATH. Shared by load runs and functional runs.
    """
    run_dir = (base / run_id).resolve()
    if not str(run_dir).startswith(str(base.resolve())):
        raise HTTPException(status_code=400, detail="Invalid run id")
    results = run_dir / "allure-results"
    if not results.exists() or not any(results.glob("*-result.json")):
        return HTMLResponse("<h3>No Allure results for this run</h3>", status_code=404)

    report = run_dir / "allure-report" / "index.html"
    if refresh or not report.exists():
        allure_bin = shutil.which("allure")
        if not allure_bin:
            return HTMLResponse(_allure_missing_html(f"{link_prefix}/{run_id}", results),
                                status_code=503)
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


def _allure_missing_html(self_link: str, results: Path) -> str:
    return (
        "<div style='font-family:system-ui;padding:40px;max-width:640px;margin:auto'>"
        "<h2>⚠️ Allure CLI not found</h2>"
        "<p>Install the Allure command-line tool to view reports in the browser:</p>"
        "<pre>brew install allure        # macOS\n"
        "# or: npm install -g allure-commandline</pre>"
        "<p>Or view it directly without installing globally:</p>"
        f"<pre>allure serve {results}</pre>"
        f"<p>Then reload <a href='{self_link}'>this page</a>.</p></div>"
    )


# ── Functional (pytest) endpoints ────────────────────────────────────────────

@app.get("/api/tests")
def api_tests():
    """The selectable functional test tree, grouped by layer (api/web/mobile)."""
    return {"groups": fe.discover_tests(),
            "defaults": {"web_url": _DEFAULT_WEB_URL, "api_host": _DEFAULT_HOST}}


@app.get("/api/impact")
def api_impact(base: str = "HEAD"):
    """Which test files are impacted by the current git changes (import graph)."""
    from utils.test_impact import analyze_impact
    return analyze_impact(base).as_dict()


@app.post("/api/record/start")
def api_record_start(req: RecordRequest):
    """Launch Playwright codegen; on close, generate a POM (+ smoke test)."""
    name = "".join(c for c in req.page_name.strip().lower().replace(" ", "_")
                   if c.isalnum() or c == "_")
    if not name:
        raise HTTPException(status_code=400, detail="A valid page name is required.")
    if not req.url.strip():
        raise HTTPException(status_code=400, detail="A URL is required.")
    try:
        _recorder.start(req.url.strip(), name, req.with_test)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"status": "recording", "page_name": name}


@app.get("/api/record/state")
def api_record_state():
    """Live snapshot of the current/last recording + generation."""
    return _recorder.snapshot()


@app.get("/api/db/runs")
def api_db_runs(kind: str | None = None, limit: int = 100):
    """Run history from the central results DB (survives artifact pruning)."""
    from utils import results_db
    return {"runs": results_db.list_runs(kind=kind, limit=limit),
            "stats": results_db.stats()}


@app.get("/api/db/run/{run_id}")
def api_db_run(run_id: str):
    from utils import results_db
    run = results_db.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found in DB")
    return run


@app.post("/api/db/backfill")
def api_db_backfill():
    from utils import results_db
    return results_db.backfill_from_files()


@app.post("/api/generate")
def api_generate(req: GenerateRequest):
    """Generate a Page Object (+ test) from a plain-English scenario."""
    scenario = (req.scenario or "").strip()
    if not scenario:
        raise HTTPException(status_code=400, detail="A scenario description is required.")
    from utils.llm_client import LLMClient
    llm = LLMClient()
    if not llm.available:
        raise HTTPException(status_code=503, detail="No LLM provider configured.")
    from tools import generate_test as gt
    page = (req.page or gt._infer_page(scenario)).strip()
    # Feature drives the test file name (tests/web/test_{feature}.py). When the
    # user leaves it blank, name the file after the scenario itself — not the
    # inferred page — so each described test gets its own readable file instead
    # of all landing in a generic default like test_checkout.py.
    feature = (req.feature or gt._to_snake(scenario) or page).strip()
    fixture = f"{page}_page" if page else "page"
    po = ""
    if not req.test_only:
        po = llm.complete(
            prompt=gt._PAGE_OBJECT_PROMPT.format(scenario=scenario, page_name=page),
            system=gt._SYSTEM, max_tokens=Config.AI_MAX_TOKENS)
    test = llm.complete(
        prompt=gt._TEST_PROMPT.format(scenario=scenario, page_fixture=fixture,
                                      feature=feature, scenario_snake_case=gt._to_snake(scenario)),
        system=gt._SYSTEM, max_tokens=Config.AI_MAX_TOKENS)

    def _strip(code):
        t = (code or "").strip()
        if t.startswith("```"):
            t = "\n".join(l for l in t.splitlines() if not l.strip().startswith("```")).strip()
        return t

    po, test = _strip(po), _strip(test)
    written = []
    validation = None
    if req.write:
        from pathlib import Path as _P
        from utils.generation_validator import GenFile
        gen_files = []
        if po:
            po_dest = _ROOT / f"pages/{page}_page.py"
            if not po_dest.exists():
                po_dest.write_text(po); written.append(f"pages/{page}_page.py")
                gen_files.append(GenFile(path=f"pages/{page}_page.py", code=po, kind="page"))
        if test:
            t_dest = _ROOT / f"tests/web/test_{feature}.py"
            if not t_dest.exists():
                t_dest.parent.mkdir(parents=True, exist_ok=True)
                t_dest.write_text(test); written.append(f"tests/web/test_{feature}.py")
                gen_files.append(GenFile(path=f"tests/web/test_{feature}.py", code=test, kind="test"))

        # Validate & repair the freshly-written files (pytest --collect-only →
        # LLM self-correct). Best-effort; never fails the request.
        if gen_files and Config.NL_REPAIR_ENABLED:
            try:
                from utils import generation_validator as gv
                outcome = gv.repair_generation(
                    gen_files, gv.make_llm_repair_fn(llm),
                    max_attempts=Config.NL_REPAIR_ATTEMPTS)
                # Reflect any repaired code back into the response.
                for f in gen_files:
                    if f.kind == "page":
                        po = f.code
                    elif f.kind == "test":
                        test = f.code
                validation = {"ok": outcome.ok, "repairs": outcome.repairs,
                              "error": outcome.last_error if not outcome.ok else ""}
            except Exception as exc:  # pragma: no cover - defensive
                validation = {"ok": None, "repairs": 0, "error": f"validation skipped: {exc}"}
    return {"page_object": po, "test": test, "page": page, "feature": feature,
            "written": written, "validation": validation}


@app.post("/api/functional/run")
def api_functional_run(req: FunctionalRequest):
    if _func_runner.is_active():
        raise HTTPException(status_code=409, detail="A functional run is already in progress.")
    target = {
        "base_url": req.base_url, "api_target": req.api_target, "api_url": req.api_url,
        "mobile_device": req.mobile_device, "browser": req.browser,
        "headless": req.headless, "markers": req.markers,
    }
    try:
        run_id = _func_runner.start(req.selection, target)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"run_id": run_id}


@app.post("/api/functional/stop")
def api_functional_stop():
    _func_runner.stop()
    return {"stopped": True}


@app.get("/api/functional/state")
def api_functional_state():
    return _func_runner.snapshot()


@app.get("/api/functional/runs")
def api_functional_runs():
    return fe.list_runs()


@app.get("/freport/{run_id}", response_class=HTMLResponse)
def functional_report(run_id: str):
    path = _safe_func_file(run_id, "report.html")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Report not found")
    return HTMLResponse(path.read_text())


@app.get("/ffile/{run_id}/{kind}")
def functional_file(run_id: str, kind: str):
    mapping = {"junit": "junit.xml", "summary": "summary.json"}
    if kind not in mapping:
        raise HTTPException(status_code=400, detail="Unknown file kind")
    path = _safe_func_file(run_id, mapping[kind])
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    media = "application/json" if kind == "summary" else "application/xml"
    return FileResponse(str(path), media_type=media)


@app.get("/fallure/{run_id}", response_class=HTMLResponse)
def functional_allure(run_id: str, refresh: int = 0):
    return _serve_allure(_FUNC_RUNS, run_id, "/fallure", refresh)


def _safe_func_file(run_id: str, filename: str) -> Path:
    run_dir = (_FUNC_RUNS / run_id).resolve()
    if not str(run_dir).startswith(str(_FUNC_RUNS.resolve())):
        raise HTTPException(status_code=400, detail="Invalid run id")
    return run_dir / filename


# ── Trace / video artifacts (inline debugging) ──────────────────────────────

@app.get("/api/functional/artifacts/{run_id}")
def api_functional_artifacts(run_id: str):
    """List Playwright traces + videos captured for a functional run."""
    art_dir = _safe_func_file(run_id, "artifacts")
    traces, videos = [], []
    if art_dir.exists():
        for f in sorted(art_dir.iterdir()):
            if f.name.endswith("__trace.zip"):
                traces.append({"name": f.name,
                               "test": f.name[:-len("__trace.zip")],
                               "size_kb": round(f.stat().st_size / 1024, 1)})
            elif f.suffix.lower() in (".webm", ".mp4"):
                videos.append({"name": f.name,
                               "size_kb": round(f.stat().st_size / 1024, 1)})
    return {"run_id": run_id, "traces": traces, "videos": videos}


def _safe_artifact(run_id: str, name: str) -> Path:
    """Resolve an artifact file, guarding against path traversal."""
    art_dir = _safe_func_file(run_id, "artifacts").resolve()
    path = (art_dir / name).resolve()
    if not str(path).startswith(str(art_dir)):
        raise HTTPException(status_code=400, detail="Invalid artifact name")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Artifact not found")
    return path


@app.get("/fartifact/{run_id}/{name}")
def functional_artifact(run_id: str, name: str):
    """Serve a trace.zip (download) or video (inline playback)."""
    path = _safe_artifact(run_id, name)
    if name.endswith(".zip"):
        return FileResponse(str(path), media_type="application/zip", filename=name)
    media = "video/webm" if path.suffix.lower() == ".webm" else "video/mp4"
    return FileResponse(str(path), media_type=media)


@app.get("/ftrace/{run_id}/{name}", response_class=HTMLResponse)
def functional_trace_viewer(run_id: str, name: str):
    """
    Open a trace in the Playwright Trace Viewer. Tries the local viewer
    (`playwright show-trace`) which opens a full-featured window on the host;
    falls back to instructions if Playwright's CLI isn't available.
    """
    path = _safe_artifact(run_id, name)
    launched = False
    try:
        subprocess.Popen([sys.executable, "-m", "playwright", "show-trace", str(path)],
                         cwd=str(_ROOT))
        launched = True
    except Exception:
        launched = False
    dl = f"/fartifact/{run_id}/{name}"
    if launched:
        body = ("<h3>Trace Viewer opening…</h3>"
                "<p>The Playwright Trace Viewer is launching in a native window "
                "on the machine running Studio.</p>"
                f"<p>If it didn't appear, <a href='{dl}'>download the trace</a> and "
                "drop it on <a href='https://trace.playwright.dev' target='_blank'>"
                "trace.playwright.dev</a>.</p>")
    else:
        body = ("<h3>Open this trace</h3>"
                f"<p><a href='{dl}'>Download {name}</a>, then either run "
                f"<code>playwright show-trace &lt;file&gt;</code> locally or drop it on "
                "<a href='https://trace.playwright.dev' target='_blank'>"
                "trace.playwright.dev</a>.</p>")
    return HTMLResponse(f"<body style='font-family:system-ui;padding:2rem'>{body}</body>")


# ── LLM provider endpoints ─────────────────────────────────────────────────

@app.get("/api/llm/providers")
def api_llm_providers():
    from llm.service import get_service
    svc = get_service()
    return {"providers": svc.list_providers(), "current": svc.current_provider_name()}


@app.post("/api/llm/select")
def api_llm_select(req: LLMSelectRequest):
    from llm.service import get_service
    try:
        result = get_service().select_provider(req.name)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": result.ok, "detail": result.detail}


@app.get("/api/llm/status")
def api_llm_status():
    from llm.service import get_service
    svc = get_service()
    v = svc.validate()
    try:
        h = svc.health()
        health = {"availability": h.availability.value, "detail": h.detail, "models": h.models[:10]}
    except Exception as exc:
        health = {"availability": "error", "detail": str(exc), "models": []}
    return {"provider": svc.current_provider_name(), "valid": v.ok, "detail": v.detail, "health": health}


# ── Top-bar status, analytics, downloads, shutdown ─────────────────────────

@app.get("/api/status")
def api_status():
    """Aggregate live status for the top bar: runs, LLM, background processes."""
    load_state = _runner.snapshot()
    func_state = _func_runner.snapshot()
    llm = {"provider": "?", "availability": "unknown", "detail": "", "model": ""}
    try:
        from llm.service import get_service
        svc = get_service()
        v = svc.validate()
        llm = {"provider": svc.current_provider_name(),
               "availability": "available" if v.ok else "needs_config",
               "detail": v.detail, "model": svc.current_model()}
    except Exception as exc:
        llm = {"provider": "?", "availability": "error", "detail": str(exc), "model": ""}
    agents_ready = llm.get("availability") == "available"
    # Every agent shares the same LLM; when it's unavailable they still work via
    # their deterministic offline fallback (degraded, not broken → warn dot).
    agent_names = ["planner", "generator", "healer"]
    agent_mode = "llm" if agents_ready else "offline"
    return {
        "load": {"status": load_state.get("status", "idle"), "run_id": load_state.get("run_id"),
                 "error": load_state.get("error")},
        "functional": {"status": func_state.get("status", "idle"),
                       "run_id": func_state.get("run_id"), "error": func_state.get("error")},
        "llm": llm,
        "agents": {"ready": agents_ready, "mode": agent_mode, "names": agent_names,
                   "items": [{"name": n, "ready": agents_ready, "mode": agent_mode}
                             for n in agent_names]},
        "processes": process_registry.active(),
    }


@app.get("/api/llm/models")
def api_llm_models():
    from llm.service import get_service
    svc = get_service()
    return {"current": svc.current_model(), "available": svc.available_models()}


@app.post("/api/llm/model")
def api_llm_model(req: LLMSelectRequest):
    from llm.service import get_service
    get_service().set_model(req.name)
    return {"ok": True, "model": req.name}


@app.get("/license", response_class=PlainTextResponse)
def license_text():
    for p in (_ROOT / "LICENSE", _ROOT.parent / "LICENSE"):
        if p.exists():
            return PlainTextResponse(p.read_text())
    return PlainTextResponse("MIT License — see the LICENSE file.", status_code=404)


def _dashboard():
    from tools import dashboard as d
    return d


@app.get("/api/analytics/{name}")
def api_analytics(name: str):
    """Proxy the analytics dashboard's data queries (merged view)."""
    d = _dashboard()
    fn = {
        "summary": d.api_summary, "trend": d.api_trend, "runs": d.api_runs,
        "flaky": d.api_flaky, "browsers": d.api_browsers, "durations": d.api_durations,
        "performance": d.api_performance,
    }.get(name)
    if fn is None:
        raise HTTPException(status_code=404, detail="Unknown analytics view")
    return fn()


@app.get("/api/compare")
def api_compare():
    """Unified run list (load + functional) for the compare view."""
    runs = []
    for r in list_runs():
        s = r.get("summary", {})
        ctx = r.get("context", {})
        runs.append({"type": "load", "run_id": r.get("run_id"), "label": r.get("run_id"),
                     "passed": r.get("passed"),
                     "metrics": {"scenario": r.get("scenario"), "profile": r.get("profile"),
                                 "requests": s.get("total_requests"), "fail_ratio": s.get("fail_ratio"),
                                 "p95_ms": s.get("p95_ms"), "rps": s.get("rps"),
                                 "llm_model": ctx.get("llm_model")}})
    for r in fe.list_runs():
        c = r.get("counts", {})
        ctx = r.get("context", {})
        runs.append({"type": "functional", "run_id": r.get("run_id"), "label": r.get("run_id"),
                     "passed": r.get("passed"),
                     "metrics": {"tests": c.get("tests"), "passed": c.get("passed"),
                                 "failed": c.get("failed"), "skipped": c.get("skipped"),
                                 "duration_s": r.get("duration_s"),
                                 "browser": ctx.get("browser"), "llm_model": ctx.get("llm_model")}})
    return {"runs": runs}


@app.get("/api/retention/preview")
def api_retention_preview():
    """Show what auto-pruning would drop right now (deletes nothing)."""
    from utils.retention import prune_reports
    return prune_reports(dry_run=True).as_dict()


@app.post("/api/retention/prune")
def api_retention_prune():
    """Enforce retention caps now and report what was freed."""
    from utils.retention import prune_reports
    return prune_reports(dry_run=False).as_dict()



def download(scope: str, run_id: str, kind: str):
    """Download a report artifact as an attachment."""
    base = _RUNS_DIR if scope == "load" else _FUNC_RUNS if scope == "functional" else None
    if base is None:
        raise HTTPException(status_code=400, detail="scope must be 'load' or 'functional'")
    run_dir = (base / run_id).resolve()
    if not str(run_dir).startswith(str(base.resolve())):
        raise HTTPException(status_code=400, detail="Invalid run id")

    files = {"html": "report.html", "junit": "junit.xml",
             "json": "results.json" if scope == "load" else "summary.json"}
    if kind == "allure":
        import io, zipfile
        allure_dir = run_dir / "allure-results"
        if not allure_dir.exists():
            raise HTTPException(status_code=404, detail="No allure-results")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in allure_dir.rglob("*"):
                if f.is_file():
                    zf.write(f, f.relative_to(allure_dir))
        buf.seek(0)
        from fastapi.responses import Response
        return Response(buf.read(), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{run_id}-allure.zip"'})
    if kind not in files:
        raise HTTPException(status_code=400, detail="Unknown file kind")
    path = run_dir / files[kind]
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(path), filename=f"{run_id}-{files[kind]}")


@app.post("/api/shutdown")
def api_shutdown():
    """Stop active runs and all background processes (called on UI close)."""
    _stop_everything()
    return {"stopped": True}


def _stop_everything() -> None:
    for runner in (_runner, _func_runner):
        try:
            if runner.is_active():
                runner.stop()
        except Exception:
            pass
    process_registry.shutdown_all()


@app.get("/", response_class=HTMLResponse)
def index():
    return _HTML


# ── CLI ──────────────────────────────────────────────────────────────────────

def _cmd_serve(args):
    process_registry.install_signal_handlers()
    print(f"\n🎯 Shreyzen Studio → http://{args.host}:{args.port}")
    print("   Open that URL, pick tests, hit Run — everything else is handled for you:")
    print("   • dependencies installed on first launch")
    print("   • Playwright browser auto-installs on the first web/mobile run")
    print("   • the mock API server auto-starts when a run needs it")
    print("   • all background processes stop cleanly on exit (Ctrl-C)\n")
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    finally:
        _stop_everything()


def _cmd_run(args):
    try:
        params = resolve_params(
            args.scenario, args.profile,
            users=args.users, duration=args.duration,
            spawn_rate=args.spawn_rate, host=args.host,
            endpoints=args.endpoints,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
    result = run_blocking(params)
    sys.exit(0 if result.get("passed") else 1)


def main():
    parser = argparse.ArgumentParser(description="Shreyzen Studio — unified test runner")
    sub = parser.add_subparsers(dest="command", required=True)

    p_serve = sub.add_parser("serve", help="launch the launcher dashboard UI")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8770)
    p_serve.set_defaults(func=_cmd_serve)

    p_run = sub.add_parser("run", help="run a load test headless (for CI)")
    p_run.add_argument("--scenario", required=True, choices=list(SCENARIOS))
    p_run.add_argument("--profile", required=True, choices=list(PROFILES))
    p_run.add_argument("--users", type=int, default=None, help="peak virtual users")
    p_run.add_argument("--duration", type=int, default=None, help="seconds")
    p_run.add_argument("--spawn-rate", type=float, default=None, dest="spawn_rate")
    p_run.add_argument("--host", default=_DEFAULT_HOST)
    p_run.add_argument(
        "--endpoints", type=lambda s: [x.strip() for x in s.split(",") if x.strip()],
        default=None,
        help="comma-separated API endpoint keys for the api_select scenario "
             f"(choices: {', '.join(API_ENDPOINTS)})")
    p_run.set_defaults(func=_cmd_run)

    # `init` and `doctor` are thin passthroughs to their own modules so they're
    # reachable via ./run.sh (init|doctor) as well as python -m tools.init/doctor.
    from tools import init as _init_mod, doctor as _doctor_mod
    _init_mod.build_parser(sub.add_parser("init", help="scaffold Shreyzen onto a new project"))
    _doctor_mod.build_parser(sub.add_parser("doctor", help="validate the environment"))

    args = parser.parse_args()
    rc = args.func(args)
    if isinstance(rc, int):
        raise SystemExit(rc)


# ── Dashboard HTML ───────────────────────────────────────────────────────────

_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Shreyzen — Load Runner</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🎯</text></svg>">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --sb:#0f172a;--sb-hover:#1e293b;--sb-border:rgba(255,255,255,.06);
  --active:#3b82f6;--active-bg:rgba(59,130,246,.15);
  --bg:#f0f4f8;--card:#fff;--text:#1e293b;--muted:#64748b;--border:#e2e8f0;--surface2:#f8fafc;
  --pass:#22c55e;--fail:#ef4444;--skip:#f59e0b;--blue:#3b82f6;--purple:#8b5cf6;
  --r:12px;--sh:0 1px 4px rgba(0,0,0,.06),0 1px 2px rgba(0,0,0,.04);--sh-md:0 4px 20px rgba(0,0,0,.09);
}
[data-theme="dark"]{
  --sb:#060a13;--sb-hover:#0f1729;
  --bg:#0b1220;--card:#111827;--text:#e5e7eb;--muted:#94a3b8;--border:#1f2937;--surface2:#0f172a;
  --sh:0 1px 4px rgba(0,0,0,.4);--sh-md:0 6px 24px rgba(0,0,0,.5);
}
/* status dots (green ok / red fail) + top-bar strip */
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;background:var(--muted);flex:none}
.dot.ok{background:var(--pass);box-shadow:0 0 0 3px rgba(34,197,94,.18)}
.dot.bad{background:var(--fail);box-shadow:0 0 0 3px rgba(239,68,68,.18)}
.dot.warn{background:var(--skip);box-shadow:0 0 0 3px rgba(245,158,11,.18)}
.dot.busy{background:var(--blue);box-shadow:0 0 0 3px rgba(59,130,246,.18);animation:pulse 1s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
.statuses{display:flex;align-items:center;gap:16px}
.statline{display:flex;align-items:center;gap:7px;font-size:12px;color:var(--muted);cursor:default}
.statline b{color:var(--text);font-weight:600}
.icon-btn{background:var(--surface2);border:1px solid var(--border);color:var(--text);border-radius:8px;padding:6px 10px;cursor:pointer;font-size:14px;font-family:inherit}
.icon-btn:hover{background:var(--border)}
/* error/info toasts */
#toasts{position:fixed;top:14px;right:14px;z-index:1000;display:flex;flex-direction:column;gap:8px;max-width:420px}
.toast{background:var(--card);border:1px solid var(--border);border-left:4px solid var(--fail);border-radius:10px;box-shadow:var(--sh-md);padding:12px 14px;font-size:13px;color:var(--text)}
.toast.ok{border-left-color:var(--pass)} .toast.info{border-left-color:var(--blue)}
.toast .tt{font-weight:700;margin-bottom:3px}
.toast .tx{color:var(--muted);word-break:break-word}
.toast .cl{float:right;cursor:pointer;color:var(--muted);margin-left:8px}
/* collapsible tree */
.tree-group{margin:4px 0}
.tree-head{display:flex;align-items:center;gap:8px;font-weight:700;font-size:13px;cursor:pointer;padding:4px 0;user-select:none}
.tree-chevron{display:inline-block;width:12px;transition:transform .15s;color:var(--muted)}
.tree-group.collapsed .tree-chevron{transform:rotate(-90deg)}
.tree-group.collapsed .tree-items{display:none}
.tree-items{padding-left:6px}
body{font-family:'Inter',sans-serif;background:var(--bg);color:var(--text);display:flex;height:100vh;overflow:hidden;margin:0}
.sb{width:250px;min-width:250px;background:var(--sb);display:flex;flex-direction:column;z-index:10}
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
.main{flex:1;display:flex;flex-direction:column;overflow:hidden;min-width:0}
.topbar{background:var(--card);border-bottom:1px solid var(--border);padding:0 28px;height:58px;display:flex;align-items:center;justify-content:space-between;flex-shrink:0}
.top-title{font-size:15px;font-weight:600}
.top-right{display:flex;align-items:center;gap:12px}
.status-pill{font-size:12px;font-weight:600;padding:5px 12px;border-radius:20px;background:var(--surface2);color:var(--muted)}
.status-pill.running{background:rgba(59,130,246,.12);color:var(--blue)}
.status-pill.completed{background:rgba(34,197,94,.14);color:#16a34a}
.status-pill.failed{background:rgba(239,68,68,.12);color:#dc2626}
.status-pill.starting,.status-pill.finalizing{background:rgba(245,158,11,.14);color:#b45309}
.page{flex:1;overflow-y:auto;overflow-x:hidden;padding:24px 28px;display:none}
.page.active{display:block}
.section-title{font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.7px;color:var(--muted);margin:4px 0 12px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:14px;margin-bottom:22px}
.card{background:var(--card);border:2px solid transparent;border-radius:var(--r);padding:16px;box-shadow:var(--sh);cursor:pointer;transition:all .15s}
.card:hover{box-shadow:var(--sh-md);transform:translateY(-1px)}
.card.sel{border-color:var(--active);background:linear-gradient(180deg,rgba(59,130,246,.05),#fff)}
.card-h{display:flex;align-items:center;gap:9px;margin-bottom:7px}
.card-icon{font-size:20px}
.card-title{font-size:14.5px;font-weight:700}
.card-blurb{font-size:12px;color:var(--muted);line-height:1.5}
.card-meta{font-size:11px;color:var(--blue);margin-top:9px;font-weight:600}
.long-tag{display:inline-block;font-size:10px;font-weight:700;color:#b45309;background:rgba(245,158,11,.14);padding:2px 7px;border-radius:10px;margin-left:6px}
.ep-list{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:8px}
.ep-item{display:flex;align-items:center;gap:8px;padding:8px 12px;background:var(--card);border:1px solid var(--border);border-radius:10px;cursor:pointer;font-size:13px;color:var(--text)}
.ep-item:hover{border-color:var(--active)}
.ep-item input{cursor:pointer}
.ep-method{font-size:10px;font-weight:800;padding:2px 6px;border-radius:6px;background:var(--surface2);color:var(--muted);min-width:52px;text-align:center}
.ep-GET{color:#0369a1;background:rgba(3,105,161,.12)}.ep-POST{color:#15803d;background:rgba(21,128,61,.12)}
.ep-PUT{color:#b45309;background:rgba(180,83,9,.12)}.ep-PATCH{color:#7c3aed;background:rgba(124,58,237,.12)}
.ep-DELETE{color:#b91c1c;background:rgba(185,28,28,.12)}
.mini-btn{font-size:11px;font-weight:600;padding:4px 10px;border:1px solid var(--border);background:var(--card);color:var(--muted);border-radius:8px;cursor:pointer;margin-right:6px}
.mini-btn:hover{border-color:var(--active);color:var(--active)}
.controls{background:var(--card);border-radius:var(--r);box-shadow:var(--sh);padding:20px;margin-bottom:20px}
.ctrl-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:18px;align-items:start}
.field{min-width:0}
.field label{display:block;font-size:11.5px;font-weight:600;color:var(--muted);margin-bottom:6px}
.field input,.field select{width:100%;max-width:100%;height:38px;padding:8px 11px;border:1px solid var(--border);border-radius:8px;font-size:13.5px;font-family:inherit;color:var(--text);background:var(--card);box-sizing:border-box}
.cbox{display:inline-flex;align-items:center;gap:8px;font-size:13.5px;font-weight:500;color:var(--text);cursor:pointer}
.cbox input{width:auto}
.field input:focus{outline:none;border-color:var(--active)}
.slider-row{display:flex;align-items:center;gap:10px}
.slider-row input[type=range]{flex:1}
.slider-val{min-width:48px;text-align:right;font-weight:700;font-size:14px;color:var(--blue)}
.btn{border:none;border-radius:9px;font-size:14px;font-weight:600;cursor:pointer;font-family:inherit;padding:11px 22px;transition:all .15s}
.btn-run{background:var(--active);color:#fff}.btn-run:hover{background:#2563eb}
.btn-run:disabled{background:#cbd5e1;cursor:not-allowed}
.btn-stop{background:var(--fail);color:#fff}.btn-stop:hover{background:#dc2626}
.btn-ghost{background:var(--surface2);color:var(--text)}
.actions{display:flex;gap:10px;margin-top:18px;align-items:center;flex-wrap:wrap}
.hint{font-size:12px;color:var(--muted)}
.info{display:inline-flex;align-items:center;justify-content:center;width:14px;height:14px;border-radius:50%;background:#e2e8f0;color:#64748b;font-size:9px;font-weight:700;font-style:normal;cursor:help;margin-left:5px;vertical-align:middle;user-select:none}
.info:hover{background:var(--active);color:#fff}
.kpi .kl{display:flex;align-items:center}
.kpi-row{display:grid;grid-template-columns:repeat(5,1fr);gap:14px;margin-bottom:18px}
.kpi{background:var(--card);border-radius:var(--r);padding:16px 18px;box-shadow:var(--sh);border-top:3px solid var(--blue)}
.kpi .kv{font-size:24px;font-weight:800;line-height:1.1}
.kpi .kl{font-size:11px;color:var(--muted);margin-top:4px;font-weight:600;text-transform:uppercase;letter-spacing:.5px}
.grid-2{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:18px}
.chart-card{background:var(--card);border-radius:var(--r);padding:18px;box-shadow:var(--sh)}
.ct{font-size:14px;font-weight:600;margin-bottom:12px}
.cw{position:relative;height:230px}
.table-card{background:var(--card);border-radius:var(--r);box-shadow:var(--sh);overflow:hidden;margin-bottom:18px}
/* horizontal scroll for wide tables (e.g. Compare with many run columns) */
.table-scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
.table-scroll .cmp-table{min-width:max-content}
.table-scroll .cmp-table th,.table-scroll .cmp-table td{white-space:nowrap}
.th{padding:14px 18px;border-bottom:1px solid var(--border);font-size:14px;font-weight:600}
table{width:100%;border-collapse:collapse}
th{padding:9px 16px;text-align:left;font-size:10.5px;font-weight:600;text-transform:uppercase;letter-spacing:.6px;color:var(--muted);background:var(--surface2);border-bottom:1px solid var(--border)}
td{padding:10px 16px;font-size:13px;border-bottom:1px solid var(--border)}
tr:last-child td{border-bottom:none}
.badge{display:inline-block;padding:3px 9px;border-radius:20px;font-size:11px;font-weight:600}
.bp{background:rgba(34,197,94,.12);color:#16a34a}.bf{background:rgba(239,68,68,.12);color:#dc2626}
.verdict{border-radius:var(--r);padding:16px 20px;margin-bottom:18px;font-weight:600;display:none}
.verdict.pass{background:rgba(34,197,94,.1);color:#166534;border:1px solid rgba(34,197,94,.3)}
.verdict.fail{background:rgba(239,68,68,.08);color:#991b1b;border:1px solid rgba(239,68,68,.3)}
.reports{display:flex;gap:10px;flex-wrap:wrap;margin-top:10px}
.report-link{font-size:12.5px;font-weight:600;text-decoration:none;padding:7px 13px;border-radius:8px;background:var(--surface2);color:var(--text)}
.report-link:hover{background:#e2e8f0}
.log{background:#0f172a;color:#cbd5e1;border-radius:var(--r);padding:14px 16px;font-family:ui-monospace,Menlo,monospace;font-size:12px;line-height:1.7;max-height:180px;overflow:auto;margin-bottom:18px;word-break:break-word;overflow-wrap:anywhere}
.empty{text-align:center;padding:50px 20px;color:var(--muted)}
.empty-icon{font-size:40px;margin-bottom:12px}
code{background:var(--surface2);padding:2px 6px;border-radius:4px;font-size:12px;color:var(--text)}
.bs{background:rgba(245,158,11,.14);color:#b45309}
.bb{background:rgba(59,130,246,.12);color:#1d4ed8}
.bfl{background:rgba(245,158,11,.1);color:#b45309}
/* dark-theme text contrast — lighten colored text so it's legible on dark cards */
[data-theme="dark"] .verdict.pass{color:#86efac}
[data-theme="dark"] .verdict.fail{color:#fca5a5}
[data-theme="dark"] .bp{color:#4ade80}
[data-theme="dark"] .bf{color:#f87171}
[data-theme="dark"] .bs,[data-theme="dark"] .bfl{color:#fbbf24}
[data-theme="dark"] .bb{color:#93c5fd}
[data-theme="dark"] .info{background:var(--surface2);color:var(--muted)}
[data-theme="dark"] .report-link:hover{background:var(--border)}
[data-theme="dark"] .status-pill.completed{color:#4ade80}
[data-theme="dark"] .status-pill.failed{color:#f87171}
[data-theme="dark"] .status-pill.starting,[data-theme="dark"] .status-pill.finalizing{color:#fbbf24}
/* ── AI Failure Analysis (Concept 4) ── */
.ai-toggle{margin-left:8px;border:1px solid rgba(139,92,246,.4);background:rgba(139,92,246,.1);color:var(--purple);border-radius:6px;padding:2px 9px;font-size:11px;font-weight:600;cursor:pointer;font-family:inherit;white-space:nowrap}
.ai-toggle:hover{background:rgba(139,92,246,.2)}
.ai-row td{background:linear-gradient(180deg,rgba(139,92,246,.06),transparent);padding:2px 16px 14px}
.ai-card{border:1px solid rgba(139,92,246,.3);border-left:4px solid var(--purple);border-radius:10px;padding:14px 16px;background:var(--card)}
.ai-head{font-weight:700;font-size:13px;display:flex;align-items:center;gap:8px;margin-bottom:9px}
.ai-reason{font-size:13px;color:var(--text);margin-bottom:11px;line-height:1.5}
.ai-conf{display:flex;align-items:center;gap:10px;margin-bottom:11px}
.ai-bar{flex:1;max-width:260px;height:8px;background:var(--surface2);border-radius:6px;overflow:hidden}
.ai-bar-fill{height:100%;background:linear-gradient(90deg,#22c55e,#8b5cf6);transition:width .4s}
.ai-conf-val{font-weight:700;font-size:13px;color:var(--purple)}
.ai-lbl{font-size:10.5px;font-weight:700;text-transform:uppercase;letter-spacing:.6px;color:var(--muted);margin:8px 0 4px}
.ai-meta{font-size:12px;color:var(--muted);margin:3px 0}
.ai-causes{margin:2px 0 6px 18px;font-size:12.5px;color:var(--text);line-height:1.6}
.ai-fixnote{font-size:12.5px;color:var(--muted);margin:6px 0;line-height:1.5}
.ai-fix pre{background:#0f172a;color:#cbd5e1;border-radius:8px;padding:12px 14px;font-family:ui-monospace,Menlo,monospace;font-size:12px;overflow:auto;margin-top:4px;white-space:pre-wrap;word-break:break-word}
[data-theme="dark"] .ai-fix pre{background:#060a13;border:1px solid var(--border)}
::-webkit-scrollbar{width:6px;height:6px}::-webkit-scrollbar-thumb{background:var(--border);border-radius:3px}
</style>
</head>
<body>
<aside class="sb">
  <div class="brand"><div class="brand-logo">🎯 Shreyzen</div><div class="brand-sub">Studio</div></div>
  <nav class="nav">
    <div class="nav-sec">AI</div>
    <div class="nav-item" id="n-llm" onclick="go('llm')"><span class="nav-icon">🤖</span>AI Provider</div>
    <div class="nav-sec">Functional</div>
    <div class="nav-item active" id="n-functional" onclick="go('functional')"><span class="nav-icon">🧪</span>Functional Tests</div>
    <div class="nav-sec">Load</div>
    <div class="nav-item" id="n-launch" onclick="go('launch')"><span class="nav-icon">🚀</span>Load Launcher</div>
    <div class="nav-item" id="n-live" onclick="go('live')"><span class="nav-icon">📡</span>Live Load Run</div>
    <div class="nav-sec">Data</div>
    <div class="nav-item" id="n-history" onclick="go('history')"><span class="nav-icon">🗃️</span>Load History</div>
    <div class="nav-item" id="n-analytics" onclick="go('analytics')"><span class="nav-icon">📊</span>Analytics</div>
    <div class="nav-item" id="n-compare" onclick="go('compare')"><span class="nav-icon">⚖️</span>Compare Runs</div>
  </nav>
  <div class="sb-footer">
    <a href="/license" target="_blank" style="color:inherit;text-decoration:none">⚖ MIT License</a> ·
    <a href="https://github.com" target="_blank" style="color:inherit;text-decoration:none">★ star · fork</a>
  </div>
</aside>

<div id="toasts"></div>
<div class="main">
  <div class="topbar">
    <div class="top-title" id="page-title">Functional Tests</div>
    <div class="top-right">
      <div class="statuses" id="statuses"></div>
      <button class="icon-btn" id="theme-btn" onclick="toggleTheme()" title="Toggle light/dark theme">🌙</button>
    </div>
  </div>

  <!-- FUNCTIONAL -->
  <div class="page active" id="p-functional">
    <div class="section-title">0 · Record a new test
      <span class="info" title="Opens Playwright's recorder in a real browser. Click through your flow, close the browser, and Studio generates a Page Object (+ smoke test) in the framework's style. Runs on the machine hosting Studio.">i</span>
    </div>
    <div class="controls">
      <div class="ctrl-grid">
        <div class="field"><label>Start URL</label>
          <input type="text" id="rec-url" placeholder="https://www.saucedemo.com/"></div>
        <div class="field"><label>Page name<span class="info" title="e.g. 'checkout' → pages/checkout_page.py + tests/web/test_checkout.py">i</span></label>
          <input type="text" id="rec-name" placeholder="checkout"></div>
      </div>
      <label class="cbox" style="display:flex;align-items:center;gap:8px;margin-top:8px"><input type="checkbox" id="rec-test" checked style="width:16px;height:16px;margin:0"> Also generate a smoke test</label>
      <div style="margin-top:10px;display:flex;align-items:center;gap:12px">
        <button id="rec-btn" onclick="startRecord()" style="padding:9px 16px;border:none;border-radius:8px;background:var(--active);color:#fff;cursor:pointer;font-weight:600">● Record flow</button>
        <span class="hint" id="rec-hint"></span>
      </div>
      <div id="rec-out" style="display:none;margin-top:12px"></div>
    </div>

    <div class="section-title">0b · Describe a test (natural language)
      <span class="info" title="Describe a scenario in plain English; the configured LLM generates a Page Object + pytest test in the framework's style.">i</span>
    </div>
    <div class="controls">
      <div class="field"><label>Scenario</label>
        <input type="text" id="gen-scenario" placeholder="User cannot checkout with an empty cart"></div>
      <div class="ctrl-grid" style="margin-top:8px">
        <div class="field"><label>Page name (optional)</label><input type="text" id="gen-page" placeholder="checkout"></div>
        <div class="field"><label>Feature (optional)</label><input type="text" id="gen-feature" placeholder="checkout"></div>
      </div>
      <div style="margin-top:10px;display:flex;align-items:center;gap:12px">
        <button id="gen-btn" onclick="generateTest()" style="padding:9px 16px;border:none;border-radius:8px;background:var(--active);color:#fff;cursor:pointer;font-weight:600">✨ Generate</button>
        <span class="hint" id="gen-hint"></span>
      </div>
      <div id="gen-out" style="display:none;margin-top:12px"></div>
    </div>

    <div class="section-title">1 · Pick tests (api · web · mobile)</div>
    <div class="controls" style="padding:0">
      <div style="display:flex;justify-content:space-between;align-items:center;padding:12px 16px;border-bottom:1px solid var(--border)">
        <input type="text" id="test-filter" placeholder="Filter tests…" oninput="renderTree()"
               style="flex:1;max-width:360px;padding:8px 11px;border:1px solid var(--border);border-radius:8px;font-family:inherit;font-size:13px">
        <button onclick="selectImpacted()" title="Select only tests impacted by your current git changes (import-graph analysis)"
               style="margin-left:8px;padding:8px 11px;border:1px solid var(--border);border-radius:8px;background:var(--card);cursor:pointer;font-size:12.5px">🎯 Changed only</button>
        <span class="hint" id="sel-count">0 selected</span>
      </div>
      <div id="test-tree" style="max-height:320px;overflow-y:auto;overflow-x:hidden;padding:8px 16px"><div class="spin"></div></div>
    </div>

    <div class="section-title">2 · Choose the target
      <span class="info" title="Where the selected tests run. Web/Mobile use the Target URL (BASE_URL); API tests use the API target below. Everything is editable per run.">i</span>
    </div>
    <div class="controls">
      <div class="ctrl-grid">
        <div class="field"><label>Target URL (web/mobile)<span class="info" title="BASE_URL the web & mobile tests navigate to, e.g. https://www.saucedemo.com.">i</span></label>
          <input type="text" id="f-base-url" value=""></div>
        <div class="field"><label>API target<span class="info" title="Where API tests point: Mock auto-starts the bundled server (offline), Public uses restful-booker.herokuapp.com, Custom uses the URL you enter.">i</span></label>
          <select id="f-api-target" onchange="toggleApiUrl()">
            <option value="mock">Mock (auto-start, offline)</option>
            <option value="public">Public (restful-booker)</option>
            <option value="custom">Custom URL…</option>
          </select></div>
        <div class="field" id="f-api-url-field" style="display:none"><label>Custom API URL</label>
          <input type="text" id="f-api-url" placeholder="https://api.example.com"></div>
        <div class="field"><label>Mobile device<span class="info" title="Playwright device profile emulated for mobile tests (viewport, touch, user-agent). Choose from the list.">i</span></label>
          <select id="f-mobile">
            <option>Pixel 5</option><option>Pixel 7</option><option>Galaxy S9+</option>
            <option>Galaxy Tab S4</option><option>iPhone 13</option><option>iPhone 14 Pro</option>
            <option>iPhone SE</option><option>iPad (gen 7)</option><option>iPad Pro 11</option>
          </select></div>
        <div class="field"><label>Markers<span class="info" title="Filter the selected tests by pytest marker. (all) runs everything selected.">i</span></label>
          <select id="f-markers">
            <option value="">(all)</option><option value="smoke">smoke</option>
            <option value="regression">regression</option><option value="e2e">e2e</option>
            <option value="negative">negative</option><option value="accessibility">accessibility</option>
          </select></div>
        <div class="field"><label>Browser<span class="info" title="Browser engine for web/mobile tests. Mobile emulation is a Chromium feature.">i</span></label>
          <select id="f-browser">
            <option value="chromium">chromium</option><option value="firefox">firefox</option><option value="webkit">webkit</option>
          </select></div>
        <div class="field"><label>Headless<span class="info" title="Run without a visible browser window (recommended). Uncheck to watch the browser during web/mobile tests.">i</span></label>
          <label class="cbox" style="display:flex;align-items:center;gap:8px;width:100%;height:38px;padding:0 11px;border:1px solid var(--border);border-radius:8px;background:var(--card);white-space:nowrap;overflow:hidden"><input type="checkbox" id="f-headless" checked style="flex:none;width:16px;height:16px;margin:0"> Hide browser window</label></div>
      </div>
      <div class="actions">
        <button class="btn btn-run" id="f-run-btn" onclick="startFunctional()">▶ Run selected</button>
        <button class="btn btn-stop" id="f-stop-btn" onclick="stopFunctional()" style="display:none">■ Stop</button>
        <span class="hint" id="f-hint"></span>
      </div>
    </div>

    <div class="section-title">3 · Results</div>
    <div class="verdict" id="f-verdict"></div>
    <div id="f-artifacts" style="display:none"></div>
    <div class="kpi-row" style="grid-template-columns:repeat(5,1fr)">
      <div class="kpi"><div class="kv" id="fk-total">–</div><div class="kl">Tests</div></div>
      <div class="kpi"><div class="kv" id="fk-passed" style="color:var(--pass)">–</div><div class="kl">Passed</div></div>
      <div class="kpi"><div class="kv" id="fk-failed" style="color:var(--fail)">–</div><div class="kl">Failed</div></div>
      <div class="kpi"><div class="kv" id="fk-skipped" style="color:var(--skip)">–</div><div class="kl">Skipped</div></div>
      <div class="kpi"><div class="kv" id="fk-errors" style="color:var(--fail)">–</div><div class="kl">Errors</div></div>
    </div>
    <div style="background:var(--card);border-radius:var(--r);box-shadow:var(--sh);padding:14px 16px;margin-bottom:18px">
      <div style="height:8px;background:var(--surface2);border-radius:6px;overflow:hidden">
        <div id="f-progress" style="height:100%;width:0;background:var(--active);transition:width .3s"></div>
      </div>
    </div>
    <div class="table-card">
      <div class="th">Per-test results</div>
      <table><thead><tr><th>Test</th><th>Status</th><th>Time (s)</th><th>Message</th></tr></thead>
      <tbody id="tb-ftests"><tr><td colspan="4" class="empty">Select tests and hit Run.</td></tr></tbody></table>
    </div>
    <div class="log" id="f-log"></div>
    <div class="table-card">
      <div class="th">Recent functional runs</div>
      <table><thead><tr><th>Run</th><th>Tests</th><th>Passed</th><th>Failed</th><th>Verdict</th><th>Reports</th></tr></thead>
      <tbody id="tb-fhistory"><tr><td colspan="6" class="empty">No runs yet</td></tr></tbody></table>
    </div>
  </div>

  <!-- LAUNCHER -->
  <div class="page" id="p-launch">
    <div class="section-title">1 · Pick a test</div>
    <div class="cards" id="scenario-cards"></div>
    <div id="endpoint-picker" style="display:none;margin:-4px 0 8px">
      <div class="section-title" style="font-size:13px">1b · Select API endpoints
        <span class="info" title="For the 'Selected APIs' scenario: choose exactly which endpoints the load hits. The chosen profile below decides the load shape (smoke/load/stress/…).">i</span>
      </div>
      <div id="endpoint-list" class="ep-list"></div>
      <div style="margin-top:6px">
        <button class="mini-btn" type="button" onclick="epAll(true)">Select all</button>
        <button class="mini-btn" type="button" onclick="epAll(false)">Clear</button>
      </div>
    </div>
    <div class="section-title">2 · Pick a load profile</div>
    <div class="cards" id="profile-cards"></div>
    <div class="section-title">3 · Tune &amp; run</div>
    <div class="controls">
      <div class="ctrl-grid">
        <div class="field"><label>Virtual Users (peak)<span class="info" title="The peak concurrency the selected profile ramps toward. The profile shape (smoke/load/stress/spike/soak/breakpoint) decides how VUs rise and fall across the duration; Custom holds this value flat.">i</span></label>
          <div class="slider-row">
            <input type="range" id="users" min="1" max="500" value="10" oninput="syncUsers()">
            <span class="slider-val" id="users-val">10</span>
          </div>
        </div>
        <div class="field"><label>Duration (seconds)</label><input type="number" id="duration" min="5" value="30"></div>
        <div class="field"><label>Spawn rate (VUs/s, 0=auto)<span class="info" title="How many virtual users are added per second while ramping up. Leave at 0 to let the profile pick a sensible rate (peak ÷ 10).">i</span></label><input type="number" id="spawn" min="0" step="0.5" value="0"></div>
        <div class="field"><label>Target host</label><input type="text" id="host" value=""></div>
      </div>
      <div class="actions">
        <button class="btn btn-run" id="run-btn" onclick="startRun()">▶ Run test</button>
        <button class="btn btn-stop" id="stop-btn" onclick="stopRun()" style="display:none">■ Stop</button>
        <span class="hint" id="run-hint"></span>
      </div>
    </div>
  </div>

  <!-- LIVE -->
  <div class="page" id="p-live">
    <div class="verdict" id="verdict"></div>
    <div class="kpi-row">
      <div class="kpi"><div class="kv" id="k-vus">–</div><div class="kl">Virtual Users<span class="info" title="Concurrent simulated users driving load right now. The load profile controls how this number ramps up and down over the run.">i</span></div></div>
      <div class="kpi"><div class="kv" id="k-rps">–</div><div class="kl">Requests / s<span class="info" title="Throughput — HTTP requests completed per second across all virtual users. Higher is more load on the target.">i</span></div></div>
      <div class="kpi"><div class="kv" id="k-reqs">–</div><div class="kl">Total Requests<span class="info" title="Cumulative number of requests sent since the run started.">i</span></div></div>
      <div class="kpi"><div class="kv" id="k-fails">–</div><div class="kl">Failures<span class="info" title="Total requests that failed so far (error status code or a failed response check). Compared against the profile's allowed failure ratio.">i</span></div></div>
      <div class="kpi"><div class="kv" id="k-p95">–</div><div class="kl">p95 (ms)<span class="info" title="95th-percentile response time: 95% of requests were faster than this. A robust tail-latency signal — less noisy than max, checked against the profile's p95 budget.">i</span></div></div>
    </div>
    <div class="grid-2">
      <div class="chart-card"><div class="ct">Throughput &amp; Virtual Users<span class="info" title="Requests/second (left axis) and active virtual users (right axis) plotted over elapsed time. Shows how throughput tracks the VU curve.">i</span></div><div class="cw"><canvas id="cThroughput"></canvas></div></div>
      <div class="chart-card"><div class="ct">Response time p95 &amp; failures/s<span class="info" title="95th-percentile response time (left axis) and failures per second (right axis) over elapsed time. Rising latency or failures under load reveal the breaking point.">i</span></div><div class="cw"><canvas id="cLatency"></canvas></div></div>
    </div>
    <div class="table-card">
      <div class="th">Per-endpoint (live)</div>
      <table><thead><tr><th>Endpoint</th><th>Requests</th><th>Failures</th><th>Median</th><th>p95</th><th>RPS</th></tr></thead>
      <tbody id="tb-endpoints"><tr><td colspan="6" class="empty">Waiting for data…</td></tr></tbody></table>
    </div>
    <div class="log" id="log"></div>
  </div>

  <!-- HISTORY -->
  <div class="page" id="p-history">
    <div class="table-card">
      <div class="th">Recent load runs</div>
      <table><thead><tr><th>Run</th><th>Scenario</th><th>Profile</th><th>Requests</th><th>Fail %</th><th>p95</th><th>Verdict</th><th>Reports</th></tr></thead>
      <tbody id="tb-history"><tr><td colspan="8" class="empty">No runs yet</td></tr></tbody></table>
    </div>
  </div>

  <!-- AI PROVIDER -->
  <div class="page" id="p-llm">
    <div class="section-title">Active LLM provider
      <span class="info" title="Which LLM powers the framework's AI features (self-healing, test generation, summaries, data generation). Cloud providers need an API key; local ones (Ollama, LM Studio) do not. Your choice is remembered.">i</span>
    </div>
    <div class="controls">
      <div class="ctrl-grid">
        <div class="field"><label>Provider</label>
          <select id="llm-select" onchange="selectLLM()"></select></div>
        <div class="field"><label>Model<span class="info" title="Model used for AI features. The list is live where the provider exposes it, plus common defaults. The selected model is recorded in every run's reports.">i</span></label>
          <select id="llm-model" onchange="selectModel()"></select></div>
        <div class="field"><label>Status</label>
          <div style="margin-top:8px"><span class="badge" id="llm-badge">–</span></div></div>
      </div>
      <div class="hint" id="llm-detail" style="margin-top:12px"></div>
      <div id="llm-caps" style="display:flex;gap:6px;flex-wrap:wrap;margin-top:12px"></div>
      <div class="actions">
        <button class="btn btn-ghost" onclick="refreshLLM()">↻ Re-check status</button>
      </div>
    </div>
    <div class="table-card">
      <div class="th">All providers</div>
      <table><thead><tr><th>Provider</th><th>Type</th><th>API key</th><th>Capabilities</th></tr></thead>
      <tbody id="tb-llm"><tr><td colspan="4" class="empty">Loading…</td></tr></tbody></table>
    </div>
  </div>

  <!-- ANALYTICS (merged dashboard) -->
  <div class="page" id="p-analytics">
    <div class="kpi-row" id="an-kpis" style="grid-template-columns:repeat(4,1fr)"></div>
    <div class="grid-2">
      <div class="chart-card"><div class="ct">Test-run trend (last 15)</div><div class="cw"><canvas id="anTrend"></canvas></div></div>
      <div class="chart-card"><div class="ct">Pass rate %</div><div class="cw"><canvas id="anRate"></canvas></div></div>
    </div>
    <div class="hint">Standalone analytics: <code>python tools/dashboard.py</code> → http://127.0.0.1:8766</div>
  </div>

  <!-- COMPARE RUNS -->
  <div class="page" id="p-compare">
    <div class="section-title">Compare runs
      <span class="info" title="Pick two or more runs (load or functional) to compare their key metrics side by side.">i</span>
    </div>
    <div class="controls">
      <div class="hint" style="margin-bottom:8px">Select runs to compare:</div>
      <div id="cmp-list" style="max-height:220px;overflow:auto"><div class="spin"></div></div>
    </div>
    <div class="table-card">
      <div class="th" style="display:flex;justify-content:space-between;align-items:center">
        <span>Comparison</span>
        <span style="font-weight:400;font-size:12px">
          <a href="#" onclick="downloadCompare('csv');return false" class="report-link">⬇ CSV</a>
          <a href="#" onclick="downloadCompare('json');return false" class="report-link">⬇ JSON</a>
          <a href="#" onclick="downloadCompare('html');return false" class="report-link">⬇ HTML</a>
        </span>
      </div>
      <div class="table-scroll">
        <table class="cmp-table"><thead id="cmp-head"></thead><tbody id="cmp-body"><tr><td class="empty">Select 2+ runs above</td></tr></tbody></table>
      </div>
    </div>
  </div>
</div>

<script>
let CATALOG=null, SEL_SCENARIO=null, SEL_PROFILE=null, POLL=null, CH={};
let TESTS=null, SELECTED=new Set(), FPOLL=null, CMP=new Set();
const $=id=>document.getElementById(id);

function go(page){
  document.querySelectorAll('.page').forEach(e=>e.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(e=>e.classList.remove('active'));
  $('p-'+page).classList.add('active'); $('n-'+page).classList.add('active');
  $('page-title').textContent={functional:'Functional Tests',launch:'Load Launcher',live:'Live Load Run',history:'Load History',llm:'AI Provider',analytics:'Analytics',compare:'Compare Runs'}[page];
  if(page==='history') loadHistory();
  if(page==='functional') loadFHistory();
  if(page==='llm') loadLLM();
  if(page==='analytics') loadAnalytics();
  if(page==='compare') loadCompare();
}

async function loadCatalog(){
  CATALOG=await fetch('/api/catalog').then(r=>r.json());
  $('host').value=CATALOG.defaults.host;
  $('scenario-cards').innerHTML=CATALOG.scenarios.map(s=>`
    <div class="card" id="sc-${s.key}" onclick="pickScenario('${s.key}')">
      <div class="card-h"><span class="card-icon">${s.icon}</span><span class="card-title">${s.label}</span></div>
      <div class="card-blurb">${s.blurb}</div></div>`).join('');
  $('profile-cards').innerHTML=CATALOG.profiles.map(p=>`
    <div class="card" id="pf-${p.key}" onclick="pickProfile('${p.key}')">
      <div class="card-h"><span class="card-icon">${p.icon}</span><span class="card-title">${p.label}${p.long_running?'<span class="long-tag">LONG</span>':''}</span></div>
      <div class="card-blurb">${p.blurb}</div>
      <div class="card-meta">${p.default_users} VUs · ${p.default_duration}s · ≤${(p.max_fail_ratio*100).toFixed(0)}% fail · p95≤${p.p95_budget_ms}ms</div></div>`).join('');
  $('endpoint-list').innerHTML=(CATALOG.endpoints||[]).map(e=>`
    <label class="ep-item"><input type="checkbox" class="ep-cb" value="${e.key}" checked>
      <span class="ep-method ep-${e.method}">${e.method}</span> ${e.path}</label>`).join('');
  pickScenario('crud'); pickProfile('smoke');
}

function epAll(on){document.querySelectorAll('.ep-cb').forEach(c=>c.checked=on);}
function selectedEndpoints(){return [...document.querySelectorAll('.ep-cb')].filter(c=>c.checked).map(c=>c.value);}

function pickScenario(k){SEL_SCENARIO=k;
  CATALOG.scenarios.forEach(s=>$('sc-'+s.key).classList.toggle('sel',s.key===k));
  $('endpoint-picker').style.display=(k==='api_select')?'block':'none';
  updateHint();}

function pickProfile(k){SEL_PROFILE=k;
  CATALOG.profiles.forEach(p=>$('pf-'+p.key).classList.toggle('sel',p.key===k));
  const p=CATALOG.profiles.find(x=>x.key===k);
  $('users').value=p.default_users; $('duration').value=p.default_duration; $('spawn').value=p.default_spawn_rate;
  syncUsers(); updateHint();}

function syncUsers(){$('users-val').textContent=$('users').value;}

function updateHint(){
  const p=CATALOG.profiles.find(x=>x.key===SEL_PROFILE);
  $('run-hint').textContent=p&&p.long_running?'⚠ This is a long-running profile — it will take a while.':'';
}

async function startRun(){
  const body={scenario:SEL_SCENARIO,profile:SEL_PROFILE,
    users:+$('users').value,duration:+$('duration').value,
    spawn_rate:+$('spawn').value,host:$('host').value.trim()};
  if(SEL_SCENARIO==='api_select'){
    const eps=selectedEndpoints();
    if(!eps.length){alert('Select at least one API endpoint to run.');return;}
    body.endpoints=eps;
  }
  const res=await fetch('/api/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!res.ok){alert('Could not start: '+(await res.json()).detail);return;}
  resetCharts(); go('live'); startPolling();
}

async function stopRun(){await fetch('/api/stop',{method:'POST'});}

function startPolling(){if(POLL)clearInterval(POLL);poll();POLL=setInterval(poll,1500);}

async function poll(){
  const s=await fetch('/api/state').then(r=>r.json());
  refreshStatuses();
  $('run-btn').disabled=['starting','running','finalizing'].includes(s.status);
  $('stop-btn').style.display=['starting','running'].includes(s.status)?'inline-block':'none';
  if(s.log) $('log').innerHTML=s.log.map(l=>`<div>${l}</div>`).join('');
  $('log').scrollTop=$('log').scrollHeight;
  scanAutoDownloads(s.log, 'Load run');

  const series=s.series||[]; const last=series[series.length-1]||{};
  $('k-vus').textContent=last.users??'–';
  $('k-rps').textContent=last.rps!=null?last.rps.toFixed(1):'–';
  $('k-reqs').textContent=last.total_requests??'–';
  $('k-fails').textContent=last.total_failures??'–';
  $('k-p95').textContent=last.p95!=null?Math.round(last.p95):'–';
  drawCharts(series);
  drawEndpoints(s.endpoints||[]);
  drawVerdict(s);

  if(['completed','failed','stopped','idle'].includes(s.status)){clearInterval(POLL);POLL=null;}
}

function resetCharts(){['cThroughput','cLatency'].forEach(id=>{if(CH[id]){CH[id].destroy();delete CH[id];}});}

function drawCharts(series){
  const L=series.map(p=>p.t);
  mk('cThroughput',{type:'line',data:{labels:L,datasets:[
    {label:'RPS',data:series.map(p=>p.rps),borderColor:'#3b82f6',backgroundColor:'rgba(59,130,246,.08)',fill:true,tension:.3,yAxisID:'y'},
    {label:'VUs',data:series.map(p=>p.users),borderColor:'#8b5cf6',backgroundColor:'rgba(139,92,246,.05)',fill:true,tension:.3,yAxisID:'y1'}]},
    options:baseOpts({y:{title:{display:true,text:'req/s'}},y1:{position:'right',title:{display:true,text:'VUs'},grid:{drawOnChartArea:false}}})});
  mk('cLatency',{type:'line',data:{labels:L,datasets:[
    {label:'p95 (ms)',data:series.map(p=>p.p95),borderColor:'#f59e0b',backgroundColor:'rgba(245,158,11,.08)',fill:true,tension:.3,yAxisID:'y'},
    {label:'failures/s',data:series.map(p=>p.fps),borderColor:'#ef4444',backgroundColor:'rgba(239,68,68,.06)',fill:true,tension:.3,yAxisID:'y1'}]},
    options:baseOpts({y:{title:{display:true,text:'ms'}},y1:{position:'right',title:{display:true,text:'fail/s'},grid:{drawOnChartArea:false}}})});
}

function baseOpts(scales){return{responsive:true,maintainAspectRatio:false,
  plugins:{legend:{position:'top',labels:{boxWidth:12,font:{size:11}}}},
  scales:Object.assign({x:{title:{display:true,text:'elapsed (s)'},ticks:{font:{size:10}}}},scales)};}

function mk(id,cfg){if(CH[id]){CH[id].data=cfg.data;CH[id].update('none');}else{CH[id]=new Chart($(id),cfg);}}

function drawEndpoints(eps){
  $('tb-endpoints').innerHTML=eps.length?eps.map(e=>`<tr>
    <td style="font-family:monospace;font-size:12px">${e.name}</td>
    <td>${e.num_requests}</td>
    <td style="color:${e.num_failures>0?'var(--fail)':'inherit'}">${e.num_failures}</td>
    <td>${e.median_ms}</td><td>${e.p95_ms}</td><td>${e.rps}</td></tr>`).join('')
    :'<tr><td colspan="6" class="empty">Waiting for data…</td></tr>';
}

function drawVerdict(s){
  const v=$('verdict');
  if(s.status!=='completed'&&s.status!=='failed'){v.style.display='none';return;}
  const passed=s.passed===true;
  v.style.display='block'; v.className='verdict '+(passed?'pass':'fail');
  const r=s.reports||{};
  const links=r.html?`<div class="reports">
    <a class="report-link" href="/report/${s.run_id}" target="_blank">📊 HTML report</a>
    <a class="report-link" href="/allure/${s.run_id}" target="_blank">📁 Allure</a>
    <span class="report-link">⬇ Download:
      <a href="/download/load/${s.run_id}/html">HTML</a> ·
      <a href="/download/load/${s.run_id}/json">JSON</a> ·
      <a href="/download/load/${s.run_id}/junit">JUnit</a> ·
      <a href="/download/load/${s.run_id}/allure">Allure.zip</a></span>
  </div>`:'';
  v.innerHTML=`${passed?'✅ PASS':'❌ FAIL'} — ${s.scenario} · ${s.profile}. `+
    (s.error?`<span>${s.error}</span>`:'')+links;
}

async function loadHistory(){
  const runs=await fetch('/api/runs').then(r=>r.json());
  $('tb-history').innerHTML=runs.length?runs.map(r=>{
    const su=r.summary||{}; const fr=(su.fail_ratio!=null?(su.fail_ratio*100).toFixed(1):'0.0');
    const badge=r.passed?'<span class="badge bp">PASS</span>':'<span class="badge bf">FAIL</span>';
    return `<tr>
      <td style="font-family:monospace;font-size:11.5px">${r.run_id}</td>
      <td>${r.scenario||'-'}</td><td>${r.profile||'-'}</td>
      <td>${su.total_requests??'-'}</td><td>${fr}%</td><td>${su.p95_ms??'-'}</td>
      <td>${badge}</td>
      <td style="white-space:nowrap;font-size:11px">
        <div>View: <a href="/report/${r.run_id}" target="_blank">HTML</a> ·
          <a href="/file/${r.run_id}/junit" target="_blank">JUnit</a> ·
          <a href="/file/${r.run_id}/json" target="_blank">JSON</a> ·
          <a href="/allure/${r.run_id}" target="_blank">Allure</a></div>
        <div>⬇ <a href="/download/load/${r.run_id}/html">HTML</a> ·
          <a href="/download/load/${r.run_id}/junit">JUnit</a> ·
          <a href="/download/load/${r.run_id}/json">JSON</a> ·
          <a href="/download/load/${r.run_id}/allure">Allure.zip</a></div>
      </td></tr>`;
  }).join(''):'<tr><td colspan="8" class="empty"><div class="empty-icon">🗃️</div>No runs yet — launch one!</td></tr>';
}

// ── Functional mode ─────────────────────────────────────────────────────────
const LAYER_ICON={api:'🔌',web:'🖥️',mobile:'📱'};
// Test-type prefix tokens (also computed server-side; derived here for old runs).
const TYPE_TOKEN={api:'API_',web:'WEB_',mobile:'MOBILE_'};
function fRunTypes(sel){return ['api','web','mobile'].filter(l=>(sel||[]).some(s=>s.startsWith('tests/'+l)));}

async function loadTests(){
  const d=await fetch('/api/tests').then(r=>r.json());
  TESTS=d.groups; $('f-base-url').value=d.defaults.web_url;
  renderTree();
}

let COLLAPSED=new Set(['api','web','mobile']);  // layers start collapsed by default

function renderTree(){
  if(!TESTS){return;}
  const q=($('test-filter').value||'').toLowerCase();
  let html='';
  for(const layer of ['api','web','mobile']){
    const items=(TESTS[layer]||[]).filter(t=>t.nodeid.toLowerCase().includes(q));
    if(!items.length) continue;
    const allSel=items.every(t=>SELECTED.has(t.nodeid));
    const collapsed=COLLAPSED.has(layer)?'collapsed':'';
    html+=`<div class="tree-group ${collapsed}" id="tg-${layer}">
      <div class="tree-head">
        <span class="tree-chevron" onclick="toggleTreeGroup('${layer}')">▼</span>
        <input type="checkbox" ${allSel?'checked':''} onclick="event.stopPropagation()" onchange="toggleLayer('${layer}',this.checked)">
        <span onclick="toggleTreeGroup('${layer}')" style="cursor:pointer">${LAYER_ICON[layer]} ${layer.toUpperCase()} <span class="hint">(${items.length})</span></span>
      </div>
      <div class="tree-items">` +
      items.map(t=>`<label style="display:flex;align-items:flex-start;gap:8px;padding:3px 0 3px 20px;font-size:12.5px;cursor:pointer;font-family:monospace;min-width:0">
        <input type="checkbox" style="flex:none;margin-top:2px" ${SELECTED.has(t.nodeid)?'checked':''} onchange="toggleOne('${t.nodeid.replace(/'/g,"\\'")}',this.checked)">
        <span style="min-width:0;word-break:break-all;overflow-wrap:anywhere">${t.name}</span></label>`).join('') +
      `</div></div>`;
  }
  $('test-tree').innerHTML=html||'<div class="empty" style="padding:24px">No matching tests</div>';
  $('sel-count').textContent=SELECTED.size+' selected';
}

function toggleTreeGroup(layer){
  COLLAPSED.has(layer)?COLLAPSED.delete(layer):COLLAPSED.add(layer);
  const el=$('tg-'+layer); if(el) el.classList.toggle('collapsed');
}
function toggleOne(nodeid,on){on?SELECTED.add(nodeid):SELECTED.delete(nodeid);renderTree();}
function toggleLayer(layer,on){(TESTS[layer]||[]).forEach(t=>on?SELECTED.add(t.nodeid):SELECTED.delete(t.nodeid));renderTree();}

async function selectImpacted(){
  try{
    const r=await fetch('/api/impact').then(r=>r.json());
    SELECTED.clear();
    const allNodes=['api','web','mobile'].flatMap(l=>TESTS[l]||[]);
    if(r.run_all){
      allNodes.forEach(t=>SELECTED.add(t.nodeid));
      $('f-hint').textContent='Core change — selected the full suite. '+(r.reason||'');
    }else if(r.impacted_tests&&r.impacted_tests.length){
      const files=new Set(r.impacted_tests);
      // nodeid looks like "tests/web/test_x.py::test_fn[chromium]" → match by file prefix
      allNodes.forEach(t=>{const file=t.nodeid.split('::')[0]; if(files.has(file)) SELECTED.add(t.nodeid);});
      $('f-hint').textContent=`Selected ${SELECTED.size} test(s) from ${files.size} impacted file(s). `+(r.reason||'');
    }else{
      $('f-hint').textContent='No tests impacted by current changes. '+(r.reason||'');
    }
    renderTree();
  }catch(e){$('f-hint').textContent='Impact analysis failed: '+e;}
}
function toggleApiUrl(){$('f-api-url-field').style.display=$('f-api-target').value==='custom'?'':'none';}

let RECPOLL=null;
async function startRecord(){
  const url=($('rec-url').value||'').trim(), name=($('rec-name').value||'').trim();
  if(!url||!name){$('rec-hint').textContent='Enter a start URL and a page name.';return;}
  $('rec-hint').textContent='';
  const res=await fetch('/api/record/start',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({url,page_name:name,with_test:$('rec-test').checked})});
  if(!res.ok){$('rec-hint').textContent='Could not start: '+(await res.json()).detail;return;}
  $('rec-btn').disabled=true;
  $('rec-hint').textContent='🎥 Recorder open — click through your flow in the browser, then close it.';
  if(RECPOLL) clearInterval(RECPOLL);
  RECPOLL=setInterval(pollRecord,1500);
}
async function pollRecord(){
  const s=await fetch('/api/record/state').then(r=>r.json());
  if(s.status==='generating') $('rec-hint').textContent='🧠 Converting recording to a Page Object…';
  if(s.status==='completed'||s.status==='failed'){
    clearInterval(RECPOLL);RECPOLL=null;$('rec-btn').disabled=false;
    const out=$('rec-out');out.style.display='block';
    if(s.status==='failed'){
      $('rec-hint').textContent='';
      out.innerHTML=`<div class="verdict fail">❌ ${escapeHtml((s.errors||['Recording failed']).join('; '))}</div>`;
      return;
    }
    $('rec-hint').textContent='';
    const written=(s.written||[]).map(w=>`<div style="font-size:12px">✅ wrote <code>${escapeHtml(w)}</code></div>`).join('');
    const errs=(s.errors||[]).map(e=>`<div style="font-size:12px;color:var(--fail)">⚠ ${escapeHtml(e)}</div>`).join('');
    let vbadge='';
    if(s.validation){
      if(s.validation.ok===true) vbadge=`<div style="font-size:12px;color:var(--pass)">✅ collects under pytest${s.validation.repairs?` (after ${s.validation.repairs} AI repair round${s.validation.repairs>1?'s':''})`:''}</div>`;
      else if(s.validation.ok===false) vbadge=`<div style="font-size:12px;color:var(--fail)">⚠ still fails collection after ${s.validation.repairs} repair round(s) — review before running</div>`;
    }
    out.innerHTML=`<div class="verdict pass">✅ Generated from recording</div>${written}${errs}${vbadge}
      <details style="margin-top:8px"><summary style="cursor:pointer">Page Object</summary>
        <pre style="max-height:260px;overflow:auto;background:var(--surface2);padding:10px;border-radius:8px;font-size:12px">${escapeHtml(s.page_object||'')}</pre></details>
      ${s.test?`<details style="margin-top:6px"><summary style="cursor:pointer">Smoke test</summary>
        <pre style="max-height:260px;overflow:auto;background:var(--surface2);padding:10px;border-radius:8px;font-size:12px">${escapeHtml(s.test)}</pre></details>`:''}
      <div style="font-size:12px;color:var(--muted);margin-top:6px">Reload the test list to pick the new test.</div>`;
    loadTests();
  }
}

async function generateTest(){
  const scenario=($('gen-scenario').value||'').trim();
  if(!scenario){$('gen-hint').textContent='Describe a scenario first.';return;}
  $('gen-hint').textContent='🧠 Generating…'; $('gen-btn').disabled=true;
  try{
    const res=await fetch('/api/generate',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({scenario,page:$('gen-page').value.trim()||null,feature:$('gen-feature').value.trim()||null,write:true})});
    if(!res.ok){$('gen-hint').textContent='Failed: '+(await res.json()).detail;$('gen-btn').disabled=false;return;}
    const s=await res.json();
    $('gen-hint').textContent='';$('gen-btn').disabled=false;
    const out=$('gen-out');out.style.display='block';
    const written=(s.written||[]).map(w=>`<div style="font-size:12px">✅ wrote <code>${escapeHtml(w)}</code></div>`).join('')
      ||'<div style="font-size:12px;color:var(--muted)">Files already existed — shown below for manual merge.</div>';
    let vbadge='';
    if(s.validation){
      if(s.validation.ok===true) vbadge=`<div style="font-size:12px;color:var(--pass)">✅ collects under pytest${s.validation.repairs?` (after ${s.validation.repairs} AI repair round${s.validation.repairs>1?'s':''})`:''}</div>`;
      else if(s.validation.ok===false) vbadge=`<div style="font-size:12px;color:var(--fail)">⚠ still fails collection after ${s.validation.repairs} repair round(s) — review before running</div>`;
    }
    out.innerHTML=`<div class="verdict pass">✅ Generated</div>${written}${vbadge}
      ${s.page_object?`<details style="margin-top:8px" open><summary style="cursor:pointer">Page Object</summary>
        <pre style="max-height:260px;overflow:auto;background:var(--surface2);padding:10px;border-radius:8px;font-size:12px">${escapeHtml(s.page_object)}</pre></details>`:''}
      ${s.test?`<details style="margin-top:6px" open><summary style="cursor:pointer">Test</summary>
        <pre style="max-height:260px;overflow:auto;background:var(--surface2);padding:10px;border-radius:8px;font-size:12px">${escapeHtml(s.test)}</pre></details>`:''}`;
    loadTests();
  }catch(e){$('gen-hint').textContent='Error: '+e;$('gen-btn').disabled=false;}
}

async function startFunctional(){
  if(!SELECTED.size){$('f-hint').textContent='Select at least one test.';return;}
  $('f-hint').textContent='';
  const body={selection:[...SELECTED],base_url:$('f-base-url').value.trim(),
    api_target:$('f-api-target').value,api_url:$('f-api-url').value.trim(),
    mobile_device:$('f-mobile').value.trim()||'Pixel 5',browser:$('f-browser').value,
    headless:$('f-headless').checked,markers:$('f-markers').value.trim()};
  const res=await fetch('/api/functional/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!res.ok){$('f-hint').textContent='Could not start: '+(await res.json()).detail;return;}
  $('f-verdict').style.display='none'; $('tb-ftests').innerHTML='';
  $('f-artifacts').style.display='none'; $('f-artifacts').innerHTML='';
  startFPoll();
}
async function stopFunctional(){await fetch('/api/functional/stop',{method:'POST'});}
function startFPoll(){if(FPOLL)clearInterval(FPOLL);fpoll();FPOLL=setInterval(fpoll,1200);}

async function fpoll(){
  const s=await fetch('/api/functional/state').then(r=>r.json());
  refreshStatuses();
  const running=['starting','running','finalizing'].includes(s.status);
  $('f-run-btn').disabled=running;
  $('f-stop-btn').style.display=running?'inline-block':'none';
  const c=s.counts||{};
  $('fk-total').textContent=c.tests??'–'; $('fk-passed').textContent=c.passed??'–';
  $('fk-failed').textContent=c.failed??'–'; $('fk-skipped').textContent=c.skipped??'–';
  $('fk-errors').textContent=c.errors??'–';
  $('f-progress').style.width=(s.percent||0)+'%';
  if(s.log) $('f-log').innerHTML=s.log.map(l=>`<div>${escapeHtml(l)}</div>`).join('');
  $('f-log').scrollTop=$('f-log').scrollHeight;
  scanAutoDownloads(s.log, 'Functional tests');
  drawFCases(s.cases||[]);
  drawFVerdict(s);
  if(['completed','failed','idle'].includes(s.status)){clearInterval(FPOLL);FPOLL=null;loadFHistory();}
}

function escapeHtml(x){return (x||'').replace(/[&<>]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[m]));}
const FST={passed:'bp',failed:'bf',error:'bf',skipped:'bs'};
const GEN_BADGE={llm:'bb',rule:'bs',offline:'bs'};
function drawFCases(cases){
  if(!cases.length){$('tb-ftests').innerHTML='<tr><td colspan="4" class="empty">Waiting for results…</td></tr>';return;}
  $('tb-ftests').innerHTML=cases.map((c,i)=>{
    const hasAI=(c.status==='failed'||c.status==='error')&&c.analysis;
    const toggle=hasAI?`<button class="ai-toggle" onclick="toggleAI(${i})">🤖 AI Analysis</button>`:'';
    const main=`<tr>
      <td style="font-family:monospace;font-size:12px;word-break:break-all">${escapeHtml(c.name)}</td>
      <td><span class="badge ${FST[c.status]||'bs'}">${c.status.toUpperCase()}</span></td>
      <td>${c.time}</td>
      <td style="font-size:11.5px;color:var(--muted);word-break:break-word">${escapeHtml(c.message||'')} ${toggle}</td></tr>`;
    const ai=hasAI?`<tr class="ai-row" id="ai-${i}"><td colspan="4">${aiPanel(c.analysis)}</td></tr>`:'';
    return main+ai;
  }).join('');
}
function toggleAI(i){const el=$('ai-'+i);if(el)el.style.display=(el.style.display==='none')?'table-row':'none';}
function aiPanel(a){
  const conf=Math.round((a.confidence||0)*100);
  const causes=(a.possible_causes||[]).map(c=>`<li>${escapeHtml(c)}</li>`).join('');
  const fix=a.suggested_fix?`<div class="ai-fix"><div class="ai-lbl">Suggested fix (${escapeHtml(a.fix_kind||'')})</div><pre>${escapeHtml(a.suggested_fix)}</pre></div>`:'';
  const showNote=a.explanation&&a.explanation!==a.root_cause;
  return `<div class="ai-card">
    <div class="ai-head">🤖 AI Failure Analysis
      <span class="badge ${GEN_BADGE[a.generated_by]||'bs'}">${escapeHtml(a.generated_by||'')}</span>
      <span class="badge bb">${escapeHtml(a.category||'')}</span></div>
    <div class="ai-reason">${escapeHtml(a.root_cause||a.explanation||'No root cause identified.')}</div>
    <div class="ai-conf"><span class="ai-lbl" style="margin:0">Confidence</span>
      <div class="ai-bar"><div class="ai-bar-fill" style="width:${conf}%"></div></div>
      <span class="ai-conf-val">${conf}%</span></div>
    ${a.failing_symbol?`<div class="ai-meta">Failing symbol: <code>${escapeHtml(a.failing_symbol)}</code></div>`:''}
    ${a.file?`<div class="ai-meta">Source: <code>${escapeHtml(a.file)}</code></div>`:''}
    ${causes?`<div class="ai-lbl">Possible causes</div><ul class="ai-causes">${causes}</ul>`:''}
    ${showNote?`<div class="ai-fixnote">${escapeHtml(a.explanation)}</div>`:''}
    ${fix}
  </div>`;
}
function drawFVerdict(s){
  const v=$('f-verdict');
  if(s.status!=='completed'&&s.status!=='failed'){v.style.display='none';return;}
  const passed=s.passed_overall===true;
  v.style.display='block'; v.className='verdict '+(passed?'pass':'fail');
  const rid=s.run_id, links=(s.reports&&s.reports.html)?`<div class="reports">
    <a class="report-link" href="/freport/${rid}" target="_blank">📊 HTML report</a>
    <a class="report-link" href="/fallure/${rid}" target="_blank">📁 Allure</a>
    <span class="report-link">⬇ Download:
      <a href="/download/functional/${rid}/html">HTML</a> ·
      <a href="/download/functional/${rid}/junit">JUnit</a> ·
      <a href="/download/functional/${rid}/json">JSON</a> ·
      <a href="/download/functional/${rid}/allure">Allure.zip</a></span></div>`:'';
  v.innerHTML=`${passed?'✅ PASS':'❌ FAIL'} — ${(s.counts||{}).passed||0} passed, ${(s.counts||{}).failed||0} failed, ${(s.counts||{}).skipped||0} skipped. `+
    (s.error?`<span>${escapeHtml(s.error)}</span>`:'')+links;
  if(rid) loadFArtifacts(rid);
}

async function loadFArtifacts(rid){
  const box=$('f-artifacts');
  try{
    const a=await fetch('/api/functional/artifacts/'+encodeURIComponent(rid)).then(r=>r.json());
    const hasAny=(a.traces&&a.traces.length)||(a.videos&&a.videos.length);
    if(!hasAny){box.style.display='none';box.innerHTML='';return;}
    let html='<div style="background:var(--card);border-radius:var(--r);box-shadow:var(--sh);padding:14px 16px;margin-bottom:18px">'+
      '<div style="font-weight:600;margin-bottom:8px">🎬 Debug artifacts</div>';
    if(a.traces&&a.traces.length){
      html+='<div style="margin-bottom:6px;color:var(--muted);font-size:12px">Playwright traces (click to open in Trace Viewer):</div>';
      html+=a.traces.map(t=>`<div style="font-size:12px;margin:3px 0">🔍 <a href="/ftrace/${rid}/${encodeURIComponent(t.name)}" target="_blank">${escapeHtml(t.test)}</a> · <a href="/fartifact/${rid}/${encodeURIComponent(t.name)}">download (${t.size_kb} KB)</a></div>`).join('');
    }
    if(a.videos&&a.videos.length){
      html+='<div style="margin:10px 0 6px;color:var(--muted);font-size:12px">Videos:</div>';
      html+=a.videos.map(vd=>`<div style="margin:6px 0"><div style="font-size:12px;margin-bottom:3px">${escapeHtml(vd.name)}</div><video controls preload="metadata" style="max-width:100%;border-radius:6px" src="/fartifact/${rid}/${encodeURIComponent(vd.name)}"></video></div>`).join('');
    }
    html+='</div>';
    box.innerHTML=html; box.style.display='block';
  }catch(e){box.style.display='none';}
}

async function loadFHistory(){
  const runs=await fetch('/api/functional/runs').then(r=>r.json());
  $('tb-fhistory').innerHTML=runs.length?runs.map(r=>{
    const c=r.counts||{}; const badge=r.passed?'<span class="badge bp">PASS</span>':'<span class="badge bf">FAIL</span>';
    const types=(r.types&&r.types.length?r.types:fRunTypes(r.selection))
      .map(t=>`<span class="badge bb" style="margin-right:4px">${TYPE_TOKEN[t]||t}</span>`).join('');
    return `<tr>
      <td style="font-family:monospace;font-size:11.5px">${types}${r.run_id}</td>
      <td>${c.tests??'-'}</td><td style="color:var(--pass)">${c.passed??'-'}</td>
      <td style="color:var(--fail)">${c.failed??'-'}</td><td>${badge}</td>
      <td style="white-space:nowrap;font-size:11px">
        <div>View: <a href="/freport/${r.run_id}" target="_blank">HTML</a> ·
          <a href="/ffile/${r.run_id}/junit" target="_blank">JUnit</a> ·
          <a href="/ffile/${r.run_id}/summary" target="_blank">JSON</a> ·
          <a href="/fallure/${r.run_id}" target="_blank">Allure</a></div>
        <div>⬇ <a href="/download/functional/${r.run_id}/html">HTML</a> ·
          <a href="/download/functional/${r.run_id}/junit">JUnit</a> ·
          <a href="/download/functional/${r.run_id}/json">JSON</a> ·
          <a href="/download/functional/${r.run_id}/allure">Allure.zip</a></div>
      </td></tr>`;
  }).join(''):'<tr><td colspan="6" class="empty">No runs yet</td></tr>';
}

// ── AI provider ─────────────────────────────────────────────────────────────
const LLM_BADGE={available:'bp',needs_config:'bs',unreachable:'bf',not_installed:'bs',error:'bf'};

async function loadLLM(){
  const d=await fetch('/api/llm/providers').then(r=>r.json());
  $('llm-select').innerHTML=d.providers.map(p=>
    `<option value="${p.name}" ${p.name===d.current?'selected':''}>${p.label}</option>`).join('');
  $('tb-llm').innerHTML=d.providers.map(p=>`<tr>
    <td>${p.name===d.current?'➤ ':''}<strong>${p.label}</strong><br><span class="hint">${p.name}</span></td>
    <td>${p.kind}</td><td>${p.requires_api_key?'required':'—'}</td>
    <td style="font-size:11.5px;color:var(--muted)">${p.capabilities.join(' · ')}</td></tr>`).join('');
  const cur=d.providers.find(p=>p.name===d.current);
  $('llm-caps').innerHTML=(cur?cur.capabilities:[]).map(c=>`<span class="badge bb">${c}</span>`).join('');
  loadModels();
  refreshLLM();
}

async function loadModels(){
  const m=await fetch('/api/llm/models').then(r=>r.json());
  const opts=(m.available||[]);
  if(m.current && !opts.includes(m.current)) opts.unshift(m.current);
  $('llm-model').innerHTML=opts.length
    ? opts.map(x=>`<option ${x===m.current?'selected':''}>${x}</option>`).join('')
    : '<option value="">(auto / none)</option>';
}

async function selectModel(){
  const model=$('llm-model').value;
  const res=await fetch('/api/llm/model',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:model})});
  if(res.ok){ toast('Model set', 'AI model → '+model, 'ok'); refreshStatuses(); }
}

async function refreshLLM(){
  $('llm-badge').textContent='checking…'; $('llm-badge').className='badge bs';
  const s=await fetch('/api/llm/status').then(r=>r.json());
  const av=(s.health&&s.health.availability)||'error';
  $('llm-badge').textContent=av.replace('_',' ');
  $('llm-badge').className='badge '+(LLM_BADGE[av]||'bs');
  const models=(s.health&&s.health.models&&s.health.models.length)?' · models: '+s.health.models.slice(0,5).join(', '):'';
  $('llm-detail').textContent=`${s.detail||''} ${(s.health&&s.health.detail)||''}${models}`.trim();
}

async function selectLLM(){
  const name=$('llm-select').value;
  const res=await fetch('/api/llm/select',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})});
  const r=await res.json();
  if(!res.ok){toast('Provider error', r.detail||'could not select'); return;}
  loadLLM();
}

// ── Analytics (merged dashboard) ────────────────────────────────────────────
async function loadAnalytics(){
  let sum, trend;
  try{ sum=await fetch('/api/analytics/summary').then(r=>r.json());
       trend=await fetch('/api/analytics/trend').then(r=>r.json()); }
  catch(_){ return; }
  const pr=(sum.pass_rate!=null?sum.pass_rate:0);
  $('an-kpis').innerHTML=`
    <div class="kpi"><div class="kv">${pr.toFixed?pr.toFixed(1):pr}%</div><div class="kl">Pass rate</div></div>
    <div class="kpi"><div class="kv">${sum.total_runs||0}</div><div class="kl">Runs</div></div>
    <div class="kpi"><div class="kv">${sum.total_tests||0}</div><div class="kl">Tests executed</div></div>
    <div class="kpi"><div class="kv">${sum.flaky_count||0}</div><div class="kl">Flaky tests</div></div>`;
  const L=(trend||[]).map(r=>(r.run_ts||'').slice(5,16));
  mk('anTrend',{type:'bar',data:{labels:L,datasets:[
    {label:'Passed',data:(trend||[]).map(r=>r.passed||0),backgroundColor:'rgba(34,197,94,.8)'},
    {label:'Failed',data:(trend||[]).map(r=>r.failed||0),backgroundColor:'rgba(239,68,68,.8)'},
    {label:'Skipped',data:(trend||[]).map(r=>r.skipped||0),backgroundColor:'rgba(245,158,11,.7)'}]},
    options:baseOpts({x:{stacked:true,ticks:{font:{size:9}}},y:{stacked:true}})});
  mk('anRate',{type:'line',data:{labels:L,datasets:[{label:'Pass %',
    data:(trend||[]).map(r=>r.total>0?(r.passed/r.total*100):0),borderColor:'#3b82f6',
    backgroundColor:'rgba(59,130,246,.08)',fill:true,tension:.3}]},
    options:baseOpts({y:{min:0,max:100}})});
}

// ── Compare runs ────────────────────────────────────────────────────────────
let CMP_RUNS=[];
async function loadCompare(){
  const d=await fetch('/api/compare').then(r=>r.json());
  CMP_RUNS=d.runs||[];
  $('cmp-list').innerHTML=CMP_RUNS.length?CMP_RUNS.map((r,i)=>`
    <label style="display:flex;align-items:center;gap:8px;padding:4px 0;font-size:12.5px">
      <input type="checkbox" ${CMP.has(i)?'checked':''} onchange="toggleCmp(${i},this.checked)">
      <span class="badge ${r.type==='load'?'bb':'bs'}">${r.type}</span>
      <span style="font-family:monospace">${r.label}</span>
      <span class="badge ${r.passed?'bp':'bf'}">${r.passed?'PASS':'FAIL'}</span></label>`).join('')
    :'<div class="empty">No runs yet</div>';
  renderCompare();
}
function toggleCmp(i,on){on?CMP.add(i):CMP.delete(i);renderCompare();}
function renderCompare(){
  const sel=[...CMP].map(i=>CMP_RUNS[i]).filter(Boolean);
  if(sel.length<2){$('cmp-head').innerHTML='';$('cmp-body').innerHTML='<tr><td class="empty">Select 2+ runs above</td></tr>';return;}
  const keys=[...new Set(sel.flatMap(r=>Object.keys(r.metrics||{})))];
  $('cmp-head').innerHTML='<tr><th>Metric</th>'+sel.map(r=>`<th>${r.label}</th>`).join('')+'</tr>';
  $('cmp-body').innerHTML=[['verdict',...sel.map(r=>r.passed?'PASS':'FAIL')]]
    .concat(keys.map(k=>[k,...sel.map(r=>r.metrics[k]??'–')]))
    .map(row=>`<tr><td><strong>${row[0]}</strong></td>`+row.slice(1).map(v=>`<td>${v}</td>`).join('')+'</tr>').join('');
}

function _cmpMatrix(){
  const sel=[...CMP].map(i=>CMP_RUNS[i]).filter(Boolean);
  const keys=[...new Set(sel.flatMap(r=>Object.keys(r.metrics||{})))];
  const rows=[['metric', ...sel.map(r=>r.label)],
              ['run_type', ...sel.map(r=>r.type)],
              ['verdict', ...sel.map(r=>r.passed?'PASS':'FAIL')]]
    .concat(keys.map(k=>[k, ...sel.map(r=>r.metrics[k]??'')]));
  return {sel, rows};
}
function downloadCompare(fmt){
  const {sel, rows}=_cmpMatrix();
  if(sel.length<2){ toast('Compare', 'Select 2+ runs first.'); return; }
  let blob, name;
  if(fmt==='json'){
    blob=new Blob([JSON.stringify(sel,null,2)],{type:'application/json'}); name='comparison.json';
  }else if(fmt==='html'){
    const th=rows[0].map(c=>`<th style="text-align:left;padding:6px;border:1px solid #ccc">${c}</th>`).join('');
    const body=rows.slice(1).map(r=>'<tr>'+r.map(c=>`<td style="padding:6px;border:1px solid #ccc">${c}</td>`).join('')+'</tr>').join('');
    blob=new Blob([`<!doctype html><meta charset=utf-8><title>Shreyzen — Run Comparison</title><h2>Shreyzen — Run Comparison</h2><table style="border-collapse:collapse;font-family:system-ui"><thead><tr>${th}</tr></thead><tbody>${body}</tbody></table>`],{type:'text/html'}); name='comparison.html';
  }else{
    const csv=rows.map(r=>r.map(c=>`"${String(c).replace(/"/g,'""')}"`).join(',')).join('\n');
    blob=new Blob([csv],{type:'text/csv'}); name='comparison.csv';
  }
  const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download=name; a.click();
  URL.revokeObjectURL(a.href);
}

// ── Auto-download surfacing ──────────────────────────────────────────────────
// When the framework auto-downloads something mid-run (e.g. a Playwright
// browser), its log line carries the SHREYZEN-AUTODL marker with a
// human-readable message including the local disk footprint. Surface each such
// line as a toast in the active section — once per unique message.
const SHOWN_DL=new Set();
function scanAutoDownloads(log, section){
  (log||[]).forEach(line=>{
    const i=line.indexOf('SHREYZEN-AUTODL');
    if(i<0) return;
    const msg=line.slice(i+'SHREYZEN-AUTODL'.length).replace(/^[\s|]+/,'').trim();
    if(!msg || SHOWN_DL.has(msg)) return;
    SHOWN_DL.add(msg);
    const done=/installed|used on disk/i.test(msg);
    toast(`${section} — ${done?'download complete':'auto-downloading…'}`, msg, done?'ok':'info');
  });
}

// ── Toasts + error surfacing ────────────────────────────────────────────────
function toast(title, text, kind){
  const el=document.createElement('div'); el.className='toast '+(kind||'');
  el.innerHTML=`<span class="cl" onclick="this.parentNode.remove()">✕</span>`+
    `<div class="tt">${escapeHtml(title)}</div><div class="tx">${escapeHtml(text||'')}</div>`;
  $('toasts').appendChild(el);
  if(kind==='ok'||kind==='info') setTimeout(()=>el.remove(), 4000);
}
// Wrap fetch so any backend error is surfaced with its reason.
const _fetch=window.fetch;
window.fetch=async(...a)=>{
  let r;
  try{ r=await _fetch(...a); }
  catch(e){ toast('Network error', String(e)); throw e; }
  if(!r.ok && (r.headers.get('content-type')||'').includes('json')){
    try{ const b=await r.clone().json(); if(b && b.detail) toast('Error '+r.status, typeof b.detail==='string'?b.detail:JSON.stringify(b.detail)); }catch(_){}
  }
  return r;
};
window.addEventListener('error', e=>toast('UI error', e.message));
window.addEventListener('unhandledrejection', e=>toast('UI error', (e.reason&&e.reason.message)||String(e.reason)));

// ── Theme ────────────────────────────────────────────────────────────────────
function applyTheme(t){
  document.documentElement.setAttribute('data-theme', t);
  $('theme-btn').textContent = t==='dark' ? '☀️' : '🌙';
  try{ localStorage.setItem('ps-theme', t); }catch(_){}
  if(window.Chart){ Chart.defaults.color = t==='dark' ? '#cbd5e1' : '#475569';
                    Object.values(CH).forEach(c=>{try{c.update('none');}catch(_){}}); }
}
function toggleTheme(){
  const cur=document.documentElement.getAttribute('data-theme')==='dark'?'light':'dark';
  applyTheme(cur);
}
applyTheme((()=>{try{return localStorage.getItem('ps-theme')||'light';}catch(_){return 'light';}})());

// ── Top-bar live status strip (green/red dots + tooltips) ───────────────────
const AV_DOT={available:'ok',needs_config:'warn',unreachable:'bad',not_installed:'warn',error:'bad'};
const RUN_DOT={idle:'',completed:'ok',failed:'bad',running:'busy',starting:'busy',finalizing:'busy',stopped:'warn'};
async function refreshStatuses(){
  let s; try{ s=await fetch('/api/status').then(r=>r.json()); }catch(_){ return; }
  const procs=(s.processes||[]).filter(p=>p.running);
  // One status line per agent (planner · generator · healer), each with its own dot.
  const agents=(s.agents&&s.agents.items)||[];
  const agentItems=agents.map(a=>({
    label:'🤖 '+a.name,
    dot:a.ready?'ok':'warn',
    tip:`agent ${a.name} — mode: ${a.mode}`+(a.ready?'':' (LLM offline → deterministic fallback)')}));
  const items=[
    {label:'Load', dot:RUN_DOT[s.load.status]||'', tip:`Load run: ${s.load.status}`+(s.load.error?` — ${s.load.error}`:'')},
    {label:'Functional', dot:RUN_DOT[s.functional.status]||'', tip:`Functional run: ${s.functional.status}`+(s.functional.error?` — ${s.functional.error}`:'')},
    {label:'LLM: '+s.llm.provider+(s.llm.model?' · '+s.llm.model:''), dot:AV_DOT[s.llm.availability]||'warn', tip:`${s.llm.availability} — ${s.llm.detail||''}`},
    ...agentItems,
    {label:`Procs ${procs.length}`, dot:procs.length?'ok':'', tip: procs.length? procs.map(p=>`${p.label} (pid ${p.pid})`).join(', '):'no background processes'},
  ];
  $('statuses').innerHTML=items.map(i=>
    `<span class="statline" title="${escapeHtml(i.tip)}"><span class="dot ${i.dot}"></span><b>${escapeHtml(i.label)}</b></span>`).join('');
}

// ── Graceful shutdown ───────────────────────────────────────────────────────
// The server stops every background process (mock servers, runs, ollama) on
// exit via signal handlers + atexit, so Ctrl-C leaves nothing hanging. This
// button stops them on demand without killing the server.
async function stopAll(){
  await fetch('/api/shutdown', {method:'POST'});
  toast('Stopped', 'All background processes were stopped.', 'ok');
  refreshStatuses();
}

loadCatalog();
loadTests();
refreshStatuses();
setInterval(refreshStatuses, 4000);
</script>
</body></html>"""


if __name__ == "__main__":
    main()
