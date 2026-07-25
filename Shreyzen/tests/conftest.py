"""
Root conftest.py — framework-wide fixtures and capability hooks.

Artifact organisation per run (no overwrites across runs):

  logs_and_reports/
  ├── screenshots/run_2026-06-26_16-07-44/
  ├── videos/run_2026-06-26_16-07-44/
  ├── a11y/run_2026-06-26_16-07-44/
  ├── visual_diffs/run_2026-06-26_16-07-44/
  ├── flakiness.db
  ├── performance.db
  └── report.html

Capabilities activated by env flags in config/.env:
  FLAKINESS_TRACKING=true     → records pass/fail per test into SQLite
  VISUAL_REGRESSION=true      → perceptual hash diff; baseline auto-created on first run
  ACCESSIBILITY_AUDIT=true    → axe-core WCAG 2.1 AA audit after each test
  PERFORMANCE_METRICS=true    → Web Vitals (LCP, CLS, TTFB, load) per test
  AI_SUMMARY=true             → GPT-4o-mini executive summary injected into HTML report
  SLACK_NOTIFICATIONS=true    → Block Kit summary sent to SLACK_WEBHOOK_URL
"""

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import pytest
from playwright.sync_api import Page

from config.config import Config

# ── Bundled demo Page Objects (saucedemo) ──────────────────────────────────
# These are imported defensively so the framework core never hard-depends on
# the demo. `shreyzen init --clean` archives them to examples/legacy_saucedemo/;
# when they're absent the demo fixtures below simply aren't registered, and the
# rest of the framework (every capability, your own pages/tests) is unaffected.
try:
    from pages.cart_page import CartPage
    from pages.checkout_page import CheckoutPage
    from pages.inventory_page import InventoryPage
    from pages.login_page import LoginPage
    _DEMO_PAGES_AVAILABLE = True
except ImportError:
    _DEMO_PAGES_AVAILABLE = False

logger = logging.getLogger(__name__)

# ── Run-level constants (set once when conftest loads) ─────────────────────
RUN_TS         = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
SCREENSHOT_DIR = Path("logs_and_reports/screenshots") / f"run_{RUN_TS}"
# When Studio's engine drives the run it points SHREYZEN_ARTIFACT_DIR at the
# run's own folder so videos + traces land beside the report; otherwise they
# collect under logs_and_reports/artifacts/run_<ts>/ for standalone pytest runs.
_ARTIFACT_ENV  = os.environ.get("SHREYZEN_ARTIFACT_DIR")
ARTIFACT_DIR   = (Path(_ARTIFACT_ENV) if _ARTIFACT_ENV
                  else Path("logs_and_reports/artifacts") / f"run_{RUN_TS}")
VIDEO_DIR      = ARTIFACT_DIR if _ARTIFACT_ENV else Path("logs_and_reports/videos") / f"run_{RUN_TS}"

# ── Lazy-loaded capability singletons (only imported when flag is on) ──────
_flakiness_tracker = None
_perf_collector    = None
_a11y_auditor      = None
_visual_reg        = None


def _get_flakiness():
    global _flakiness_tracker
    if _flakiness_tracker is None and Config.FLAKINESS_TRACKING:
        from utils.flakiness_tracker import FlakinessTracker
        _flakiness_tracker = FlakinessTracker()
    return _flakiness_tracker


_failure_store = None


def _get_failure_store():
    global _failure_store
    if _failure_store is None and Config.FAILURE_TRACKING:
        from utils.failure_store import FailureStore
        _failure_store = FailureStore()
    return _failure_store


def _get_perf():
    global _perf_collector
    if _perf_collector is None and Config.PERFORMANCE_METRICS:
        from utils.performance import PerformanceCollector
        _perf_collector = PerformanceCollector()
    return _perf_collector


def _get_a11y():
    global _a11y_auditor
    if _a11y_auditor is None and Config.ACCESSIBILITY_AUDIT:
        from utils.accessibility import AccessibilityAuditor
        _a11y_auditor = AccessibilityAuditor()
    return _a11y_auditor


def _get_visual():
    global _visual_reg
    if _visual_reg is None and Config.VISUAL_REGRESSION:
        from utils.visual_regression import VisualRegression
        _visual_reg = VisualRegression()
    return _visual_reg


# ── Session-level counters for end-of-session hooks ───────────────────────
_session_stats: dict = {
    "passed": 0, "failed": 0, "error": 0, "skipped": 0,
    "failed_tests": [], "start_time": None,
}

# Per-test records for the Extent-style report (Capability 26). Collected in
# pytest_runtest_makereport; consumed in pytest_sessionfinish when EXTENT_REPORT.
_test_records: list = []


def _test_category(item) -> str:
    """Best-effort category for a test: a layer marker, else its folder."""
    for marker in ("web", "mobile", "api", "unit", "visual", "accessibility"):
        if item.get_closest_marker(marker):
            return marker
    parts = item.nodeid.split("/")
    return parts[1] if len(parts) > 2 and parts[0] == "tests" else "tests"


def _safe_name(nodeid: str, max_len: int = 120) -> str:
    name = (
        nodeid
        .replace("::", "__").replace("[", "_").replace("]", "")
        .replace("/",  "__").replace(" ", "_").replace("\\", "__")
    )
    return name[:max_len]


# ── Session start ──────────────────────────────────────────────────────────

def pytest_addoption(parser):
    """--quarantine-only runs ONLY the quarantined (known-flaky) tests."""
    parser.addoption(
        "--quarantine-only", action="store_true", default=False,
        help="Run only the quarantined (known-flaky) tests — the separate lane.")


def pytest_collection_modifyitems(config, items):
    """Enforce the quarantine list (Capability 2).

    Normal run  → deselect quarantined tests so a known flake can't gate the build.
    --quarantine-only → keep ONLY the quarantined tests (the flaky lane).
    Controlled by QUARANTINE_ENABLED; a no-op when the list is empty.
    """
    only = config.getoption("--quarantine-only")
    if not Config.QUARANTINE_ENABLED and not only:
        return
    try:
        from utils import quarantine
        q_ids = quarantine.ids()
    except Exception:
        q_ids = set()
    if not q_ids and not only:
        return

    keep, drop = [], []
    for item in items:
        is_q = item.nodeid in q_ids
        if only:
            (keep if is_q else drop).append(item)
        else:
            (drop if is_q else keep).append(item)
        if is_q:
            item.add_marker(pytest.mark.quarantine)

    if drop:
        reason = ("not quarantined (--quarantine-only)" if only
                  else "quarantined: known-flaky (see data/quarantine.json)")
        config.hook.pytest_deselected(items=drop)
        items[:] = keep
        logger.info("Quarantine: %d test(s) deselected — %s", len(drop), reason)


def pytest_configure(config):
    """
    Ensure the required Playwright browser is installed before any test runs.
    Runs once at collection start so users never need a manual
    `playwright install`. Controlled by AUTO_INSTALL_BROWSERS in config/.env.
    """
    if Config.AUTO_INSTALL_BROWSERS:
        from utils.browser_bootstrap import ensure_browser_installed
        ensure_browser_installed(Config.BROWSER, with_deps=Config.INSTALL_BROWSER_DEPS)


def pytest_html_report_title(report):
    """Title the pytest-html report with the run's test-type prefix when set.

    Studio's functional runner exports SHREYZEN_REPORT_TITLE (e.g.
    ``WEB_API_Functional Report — <run_id>``) so reports are identifiable by the
    type of test rather than a generic "report" + timestamp.
    """
    title = os.environ.get("SHREYZEN_REPORT_TITLE")
    if title:
        report.title = title


@pytest.fixture(scope="session", autouse=True)
def _session_setup():
    _session_stats["start_time"] = time.monotonic()
    Config.validate()
    yield


# ── Browser launch args ────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    return {
        **browser_type_launch_args,
        "headless": Config.HEADLESS,
        "slow_mo":  Config.SLOW_MO,
    }


# ── Browser context args — video dir is per-run ────────────────────────────

@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    record_video = Config.RECORD_VIDEO and not Config.HEADLESS
    if record_video:
        VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    return {
        **browser_context_args,
        "viewport": {"width": Config.VIEWPORT_WIDTH, "height": Config.VIEWPORT_HEIGHT},
        "record_video_dir": str(VIDEO_DIR) if record_video else None,
    }


# ── Playwright trace capture (TRACE_ON_FAILURE) ─────────────────────────────
# Records a full Playwright trace (DOM snapshots, screenshots, network, console)
# per test. The trace.zip opens in the Playwright Trace Viewer — Studio serves
# it inline so a failed test is one click from "watch what happened".

@pytest.fixture(autouse=True)
def _trace_capture(request):
    if not Config.TRACE_ON_FAILURE:
        yield
        return
    # Only trace browser tests — skip pure API/unit tests that never open a
    # context (requesting `context` there would force a needless browser launch).
    if "context" not in request.fixturenames and "page" not in request.fixturenames:
        yield
        return
    try:
        context = request.getfixturevalue("context")
        context.tracing.start(screenshots=True, snapshots=True, sources=True)
    except Exception as exc:  # tracing unsupported / no browser context
        logger.debug("Trace start skipped: %s", exc)
        yield
        return
    try:
        yield
    finally:
        # Keep the trace only when the test failed (TRACE_ON_FAILURE); on pass we
        # stop without writing to avoid disk bloat. request.node.rep_call is set
        # by the makereport hook below.
        failed = getattr(getattr(request.node, "rep_call", None), "failed", False) \
            or getattr(getattr(request.node, "rep_setup", None), "failed", False)
        try:
            if failed:
                ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
                dest = ARTIFACT_DIR / f"{_safe_name(request.node.nodeid)}__trace.zip"
                context.tracing.stop(path=str(dest))
                logger.info("Trace → %s", dest)
            else:
                context.tracing.stop()
        except Exception as exc:
            logger.debug("Trace stop skipped: %s", exc)


# ── Per-test capability fixture ────────────────────────────────────────────
# Visual regression and accessibility are opt-in per test via the
# `check_visual` and `check_a11y` fixtures, OR auto-run for every UI test
# when the flags are on via the `_auto_capabilities` autouse fixture.

@pytest.fixture
def check_visual(page: Page, request):
    """
    Fixture for on-demand visual regression check.
    Usage in test: check_visual(name="login_page")
    """
    vr = _get_visual()
    def _check(name: str = "") -> Optional[dict]:
        if vr is None:
            return None
        safe_name = name or _safe_name(request.node.nodeid)
        result = vr.check(page, safe_name)
        if result.get("status") == "fail":
            diff_path = result.get("diff_path", "")
            pytest.fail(
                f"Visual regression: '{safe_name}' diff={result.get('diff_bits')} bits "
                f"(threshold={Config.VISUAL_DIFF_THRESHOLD}). Diff: {diff_path}"
            )
        return result
    return _check


@pytest.fixture
def check_a11y(page: Page, request):
    """
    Fixture for on-demand accessibility audit.
    Usage in test: violations = check_a11y()
    """
    auditor = _get_a11y()
    def _check(context_name: str = "") -> Optional[dict]:
        if auditor is None:
            return None
        name = context_name or _safe_name(request.node.nodeid)
        result = auditor.audit(page, name)
        if result.get("blocking"):
            fail_on = [s.strip() for s in Config.ACCESSIBILITY_FAIL_ON.split(",")]
            blocking = [
                v for v in result.get("violations", [])
                if v.get("impact") in fail_on
            ]
            if blocking:
                issues = "; ".join(
                    f"[{v['impact'].upper()}] {v.get('id','?')}: {v.get('description','')}"
                    for v in blocking[:5]
                )
                pytest.fail(f"Accessibility violations on '{name}': {issues}")
        return result
    return _check


# ── Main lifecycle hook ────────────────────────────────────────────────────
#
# when="call"     → fires after test body, before any fixture teardown.
#                   Page is alive. Ideal for screenshots, perf, a11y.
# when="teardown" → fires after all teardowns. Context closed, video finalised.
#                   Ideal for video rename and flakiness recording.

@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report  = outcome.get_result()

    setattr(item, f"rep_{report.when}", report)

    page: Page = item.funcargs.get("page")

    # ── After test body (page still alive) ───────────────────────────────
    if report.when == "call":
        status = "FAIL" if report.failed else "PASS"
        safe   = _safe_name(item.nodeid)

        # 1. Screenshot
        if page is not None:
            want = Config.SCREENSHOT_ALL_TESTS or (Config.SCREENSHOT_ON_FAILURE and report.failed)
            if want:
                ts   = datetime.now().strftime("%H-%M-%S")
                dest = SCREENSHOT_DIR / f"{safe}__{ts}__{status}.png"
                dest.parent.mkdir(parents=True, exist_ok=True)
                try:
                    page.screenshot(path=str(dest), full_page=True)
                    logger.info("Screenshot → %s", dest)
                except Exception as exc:
                    logger.warning("Screenshot skipped (%s): %s", safe, exc)

        # 2. Performance metrics (page must be open)
        if page is not None:
            perf = _get_perf()
            if perf:
                try:
                    metrics = perf.collect(page, test_id=item.nodeid, run_ts=RUN_TS)
                    # Hard gate: fail an otherwise-passing test that blew its
                    # perf budget (opt-in via PERFORMANCE_GATE).
                    exceeded = metrics.get("budgets_exceeded") if metrics else None
                    if exceeded and Config.PERFORMANCE_GATE and not report.failed:
                        detail = ", ".join(
                            f"{k}={v['value']}ms > budget {v['budget']}ms"
                            for k, v in exceeded.items()
                        )
                        report.outcome = "failed"
                        msg = f"Performance budget exceeded: {detail}"
                        report.longrepr = msg
                        report.sections.append(("Performance budget", msg))
                        logger.warning("Perf GATE failed %s: %s", safe, detail)
                except Exception as exc:
                    logger.debug("Perf collection skipped: %s", exc)

        # 3. Auto accessibility check (if ACCESSIBILITY_AUDIT=true)
        if page is not None and Config.ACCESSIBILITY_AUDIT:
            auditor = _get_a11y()
            if auditor:
                try:
                    result = auditor.audit(page, context_name=safe)
                    if result.get("blocking"):
                        fail_on = [s.strip() for s in Config.ACCESSIBILITY_FAIL_ON.split(",")]
                        blocking = [
                            v for v in result.get("violations", [])
                            if v.get("impact") in fail_on
                        ]
                        if blocking and not report.failed:
                            # Annotate the report with a11y failures without double-failing
                            issues = "; ".join(
                                f"[{v['impact'].upper()}] {v.get('id','?')}"
                                for v in blocking[:3]
                            )
                            logger.warning("A11y violations on %s: %s", safe, issues)
                except Exception as exc:
                    logger.debug("A11y skipped: %s", exc)

        # 4. Visual regression auto-check (if VISUAL_REGRESSION=true)
        if page is not None and Config.VISUAL_REGRESSION:
            vr = _get_visual()
            if vr:
                try:
                    vr.check(page, safe)
                except Exception as exc:
                    logger.debug("Visual check skipped: %s", exc)

        # 5. Update session counters
        if report.failed:
            _session_stats["failed"] += 1
            _session_stats["failed_tests"].append(item.nodeid)
        elif report.passed:
            _session_stats["passed"] += 1
        elif report.skipped:
            _session_stats["skipped"] += 1

        # 5b. Record for the Extent-style report (Capability 26).
        if Config.EXTENT_REPORT:
            _test_records.append({
                "nodeid": item.nodeid,
                "name": item.nodeid.split("::", 1)[-1],
                "status": "fail" if report.failed else ("skip" if report.skipped else "pass"),
                "duration_s": round(getattr(report, "duration", 0.0) or 0.0, 3),
                "category": _test_category(item),
                "message": (report.longreprtext[:1500] if report.failed else ""),
            })

        # 5c. Persist failure detail for root-cause clustering (Capability 30).
        if report.failed and Config.FAILURE_TRACKING:
            fs = _get_failure_store()
            if fs:
                try:
                    tb = report.longreprtext or ""
                    msg = tb.strip().splitlines()[-1] if tb.strip() else "test failed"
                    browser = item.funcargs.get("browser_name", "")
                    fs.record(item.nodeid, message=msg, traceback=tb,
                              run_ts=RUN_TS, browser=browser)
                except Exception as exc:
                    logger.debug("Failure record skipped: %s", exc)

    # ── After all teardowns (context closed, video file finalised) ────────
    if report.when == "teardown":
        safe   = _safe_name(item.nodeid)
        status = "FAIL" if getattr(getattr(item, "rep_call", None), "failed", False) else "PASS"

        # 6. Video rename
        if page is not None:
            try:
                video = page.video
                if video:
                    src = Path(video.path())
                    if src.exists():
                        dest = VIDEO_DIR / f"{safe}__{status}{src.suffix}"
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        src.rename(dest)
                        logger.info("Video → %s", dest)
            except Exception as exc:
                logger.debug("Video rename skipped: %s", exc)

        # 7. Flakiness tracking
        tracker = _get_flakiness()
        if tracker:
            try:
                outcome_str = "fail" if status == "FAIL" else "pass"
                rep_call = getattr(item, "rep_call", None)
                duration = getattr(rep_call, "duration", 0.0) if rep_call else 0.0
                browser  = item.funcargs.get("browser_name", "unknown")
                tracker.record(item.nodeid, outcome_str, duration, RUN_TS, browser)
            except Exception as exc:
                logger.debug("Flakiness record skipped: %s", exc)


# ── End-of-session hook: AI Summary + Slack ────────────────────────────────

def pytest_sessionfinish(session, exitstatus):
    elapsed = time.monotonic() - (_session_stats["start_time"] or time.monotonic())
    passed  = _session_stats["passed"]
    failed  = _session_stats["failed"]
    skipped = _session_stats["skipped"]
    error   = _session_stats["error"]

    flaky_tests = []
    tracker = _get_flakiness()
    if tracker:
        try:
            flaky_tests = tracker.get_flaky_tests()
        except Exception:
            pass

    # 7b. Auto-quarantine newly-detected flaky tests (opt-in via AUTO_QUARANTINE).
    #     Each new entry gets an AI (or offline heuristic) diagnosis so the reason
    #     and a suggested fix travel with the quarantine record.
    if Config.AUTO_QUARANTINE and flaky_tests and tracker:
        try:
            from utils import quarantine, flaky_analysis
            already = quarantine.ids()
            for ft in flaky_tests:
                tid = ft.get("test_id", "")
                if not tid or tid in already:
                    continue
                dx = flaky_analysis.diagnose(tid, tracker.get_history(tid))
                quarantine.add(
                    tid, reason=dx["explanation"], category=dx["category"],
                    confidence=dx["confidence"], suggested_fix=dx["suggested_fix"],
                    flake_rate=ft.get("flake_rate"), source=f"auto:{dx['via']}")
                logger.warning("Auto-quarantined flaky test %s [%s] — %s",
                               tid, dx["category"], dx["suggested_fix"])
        except Exception as exc:
            logger.debug("Auto-quarantine skipped: %s", exc)

    ai_summary_text = None

    # 8. AI executive summary
    if Config.AI_SUMMARY:
        try:
            from utils.ai_summary import AISummaryGenerator
            gen = AISummaryGenerator()
            ai_summary_text = gen.generate(
                passed=passed,
                failed=failed,
                error=error,
                skipped=skipped,
                duration=elapsed,
                run_ts=RUN_TS,
                failed_tests=_session_stats["failed_tests"][:10],
                flaky_tests=[t.get("test_id", "") for t in flaky_tests[:5]],
            )
            # Inject into HTML report if it exists
            report_path = Path("logs_and_reports/report.html")
            if report_path.exists():
                gen.inject_into_html_report(ai_summary_text, str(report_path))
        except Exception as exc:
            logger.warning("AI summary failed: %s", exc)

    # 8b. Extent-style consolidated HTML report (Capability 26)
    if Config.EXTENT_REPORT:
        try:
            from utils import extent_report
            meta = {
                "title": "Shreyzen — Extent Report",
                "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "context": {
                    "Run": RUN_TS,
                    "Passed": passed, "Failed": failed,
                    "Skipped": skipped, "Errors": error,
                    "Duration": f"{elapsed:.1f}s",
                    "Flaky": ", ".join(t.get("test_id", "") for t in flaky_tests[:5]) or "—",
                },
            }
            out = extent_report.write_report(
                Path("logs_and_reports/extent_report.html"), _test_records, meta)
            logger.info("Extent report → %s", out)
        except Exception as exc:
            logger.warning("Extent report failed: %s", exc)

    # 9. Slack / Teams notification
    if Config.SLACK_NOTIFICATIONS:
        try:
            report_url = None  # Set to your CI artifact URL if available
            if Config.SLACK_WEBHOOK_URL:
                from utils.slack_notifier import SlackNotifier
                SlackNotifier().send_run_summary(
                    passed=passed,
                    failed=failed,
                    skipped=skipped,
                    error=error,
                    duration=elapsed,
                    run_ts=RUN_TS,
                    report_url=report_url,
                    ai_summary=ai_summary_text,
                    flaky_tests=flaky_tests[:5],
                )
            if Config.TEAMS_WEBHOOK_URL:
                from utils.slack_notifier import TeamsNotifier
                TeamsNotifier().send_run_summary(
                    passed=passed,
                    failed=failed,
                    skipped=skipped,
                    duration=elapsed,
                    run_ts=RUN_TS,
                    ai_summary=ai_summary_text,
                )
        except Exception as exc:
            logger.warning("Notification failed: %s", exc)

    # 10. Log flaky tests summary to console
    if flaky_tests:
        logger.warning(
            "FLAKY TESTS detected (%d):\n%s",
            len(flaky_tests),
            "\n".join(f"  {t.get('test_id','?')} (fail_rate={t.get('fail_rate',0):.0%})"
                      for t in flaky_tests[:10]),
        )

    # 11. Write run JSON for the dashboard
    try:
        total = passed + failed + skipped + error
        run_data = {
            "run_ts":       RUN_TS,
            "passed":       passed,
            "failed":       failed,
            "skipped":      skipped,
            "error":        error,
            "total":        total,
            "pass_rate":    round(passed / max(total, 1) * 100, 1),
            "duration_s":   round(elapsed, 2),
            "failed_tests": _session_stats["failed_tests"][:20],
            "flaky_count":  len(flaky_tests),
        }
        runs_dir = Path("logs_and_reports/runs")
        runs_dir.mkdir(parents=True, exist_ok=True)
        (runs_dir / f"run_{RUN_TS}.json").write_text(json.dumps(run_data, indent=2))
    except Exception as exc:
        logger.debug("Run JSON export failed: %s", exc)

    # 12. Regression detection vs prior runs (must run AFTER the run JSON above,
    #     so the just-finished run is included in the history).
    if Config.REGRESSION_ALERTS:
        try:
            from utils.regression_detector import detect_with_config
            reg = detect_with_config()
            if reg.has_regressions:
                logger.warning(
                    "REGRESSIONS vs baseline (%d):\n%s",
                    len(reg.regressions),
                    "\n".join(f"  [{r.severity.upper()}] {r.message}"
                              for r in reg.regressions),
                )
                # Push a Slack alert too, if notifications are on.
                if Config.SLACK_NOTIFICATIONS and Config.SLACK_WEBHOOK_URL:
                    try:
                        from utils.slack_notifier import SlackNotifier
                        lines = "\n".join(f"• [{r.severity.upper()}] {r.message}"
                                          for r in reg.regressions)
                        SlackNotifier(Config.SLACK_WEBHOOK_URL, Config.SLACK_CHANNEL).send_text(
                            f"📉 *Regressions detected* in run `{reg.latest_run}` "
                            f"(vs {reg.baseline_runs} prior runs):\n{lines}"
                        )
                    except Exception as exc:
                        logger.debug("Regression Slack alert skipped: %s", exc)
        except Exception as exc:
            logger.debug("Regression detection skipped: %s", exc)


# ── Page Object fixtures ───────────────────────────────────────────────────
# Registered only when the bundled saucedemo demo pages are present (see the
# defensive import at the top). Your own tests should instantiate their Page
# Objects directly or define fixtures in their own conftest — these exist purely
# to support the shipped demo suite.

if _DEMO_PAGES_AVAILABLE:

    @pytest.fixture
    def login_page(page: Page) -> "LoginPage":
        return LoginPage(page)


    @pytest.fixture
    def inventory_page(page: Page) -> "InventoryPage":
        return InventoryPage(page)


    @pytest.fixture
    def cart_page(page: Page) -> "CartPage":
        return CartPage(page)


    @pytest.fixture
    def checkout_page(page: Page) -> "CheckoutPage":
        return CheckoutPage(page)


    @pytest.fixture
    def authenticated_page(page: Page) -> Page:
        """Return a page already logged in (sitting on inventory)."""
        lp = LoginPage(page)
        lp.navigate()
        lp.login(Config.TEST_USER_EMAIL, Config.TEST_USER_PASSWORD)
        return page


# ── Test data fixtures ─────────────────────────────────────────────────────

@pytest.fixture
def load_test_data():
    def _load(filename: str) -> dict:
        data_path = Path(__file__).parent.parent / "data" / filename
        with open(data_path, encoding="utf-8") as f:
            return json.load(f)
    return _load


@pytest.fixture
def e2e_data(load_test_data) -> dict:
    return load_test_data("e2e_test_data.json")
