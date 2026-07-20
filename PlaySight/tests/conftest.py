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
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import pytest
from playwright.sync_api import Page

from config.config import Config
from pages.cart_page import CartPage
from pages.checkout_page import CheckoutPage
from pages.inventory_page import InventoryPage
from pages.login_page import LoginPage

logger = logging.getLogger(__name__)

# ── Run-level constants (set once when conftest loads) ─────────────────────
RUN_TS         = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
SCREENSHOT_DIR = Path("logs_and_reports/screenshots") / f"run_{RUN_TS}"
VIDEO_DIR      = Path("logs_and_reports/videos")      / f"run_{RUN_TS}"

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


def _safe_name(nodeid: str, max_len: int = 120) -> str:
    name = (
        nodeid
        .replace("::", "__").replace("[", "_").replace("]", "")
        .replace("/",  "__").replace(" ", "_").replace("\\", "__")
    )
    return name[:max_len]


# ── Session start ──────────────────────────────────────────────────────────

def pytest_configure(config):
    """
    Ensure the required Playwright browser is installed before any test runs.
    Runs once at collection start so users never need a manual
    `playwright install`. Controlled by AUTO_INSTALL_BROWSERS in config/.env.
    """
    if Config.AUTO_INSTALL_BROWSERS:
        from utils.browser_bootstrap import ensure_browser_installed
        ensure_browser_installed(Config.BROWSER, with_deps=Config.INSTALL_BROWSER_DEPS)


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
                    perf.collect(page, test_id=item.nodeid, run_ts=RUN_TS)
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


# ── Page Object fixtures ───────────────────────────────────────────────────

@pytest.fixture
def login_page(page: Page) -> LoginPage:
    return LoginPage(page)


@pytest.fixture
def inventory_page(page: Page) -> InventoryPage:
    return InventoryPage(page)


@pytest.fixture
def cart_page(page: Page) -> CartPage:
    return CartPage(page)


@pytest.fixture
def checkout_page(page: Page) -> CheckoutPage:
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
