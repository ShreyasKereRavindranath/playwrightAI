"""
Central configuration loader.

All framework consumers must import from here — never call os.environ directly.
Values are read from config/.env (loaded at import time via python-dotenv).
"""

import os
from pathlib import Path
from dotenv import load_dotenv

_ENV_PATH = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH, override=False)


class Config:
    # ── Application ───────────────────────────────────────────────────────────
    BASE_URL: str = os.getenv("BASE_URL", "http://localhost:3000")

    # ── Browser ───────────────────────────────────────────────────────────────
    BROWSER: str = os.getenv("BROWSER", "chromium")
    HEADLESS: bool = os.getenv("HEADLESS", "true").lower() == "true"
    SLOW_MO: int = int(os.getenv("SLOW_MO", "0"))
    DEFAULT_TIMEOUT: int = int(os.getenv("DEFAULT_TIMEOUT", "30000"))
    # Auto-provision the Playwright browser binary if missing (no manual
    # `playwright install` needed). INSTALL_BROWSER_DEPS also pulls OS libs
    # via `--with-deps` (Linux/CI; requires root).
    AUTO_INSTALL_BROWSERS: bool = os.getenv("AUTO_INSTALL_BROWSERS", "true").lower() == "true"
    INSTALL_BROWSER_DEPS: bool = os.getenv("INSTALL_BROWSER_DEPS", "false").lower() == "true"
    VIEWPORT_WIDTH: int = int(os.getenv("VIEWPORT_WIDTH", "1280"))
    VIEWPORT_HEIGHT: int = int(os.getenv("VIEWPORT_HEIGHT", "720"))

    # ── Test Users ────────────────────────────────────────────────────────────
    TEST_USER_EMAIL: str = os.getenv("TEST_USER_EMAIL", "")
    TEST_USER_PASSWORD: str = os.getenv("TEST_USER_PASSWORD", "")

    # ── AI / LLM ──────────────────────────────────────────────────────────────
    ENABLE_AI_HEALING: bool = os.getenv("ENABLE_AI_HEALING", "false").lower() == "true"
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    AI_MODEL: str = os.getenv("AI_MODEL", "gpt-4o-mini")
    AI_MAX_TOKENS: int = int(os.getenv("AI_MAX_TOKENS", "2000"))

    # ── Capability: Flakiness Tracker ─────────────────────────────────────────
    FLAKINESS_TRACKING: bool = os.getenv("FLAKINESS_TRACKING", "true").lower() == "true"
    FLAKINESS_WINDOW: int = int(os.getenv("FLAKINESS_WINDOW", "20"))
    FLAKINESS_THRESHOLD: float = float(os.getenv("FLAKINESS_THRESHOLD", "0.15"))

    # ── Capability: Visual Regression ─────────────────────────────────────────
    VISUAL_REGRESSION: bool = os.getenv("VISUAL_REGRESSION", "false").lower() == "true"
    # Max allowed perceptual difference (imagehash Hamming distance 0-64; 10 ≈ 5%)
    VISUAL_DIFF_THRESHOLD: int = int(os.getenv("VISUAL_DIFF_THRESHOLD", "10"))

    # ── Capability: Accessibility Audit ───────────────────────────────────────
    ACCESSIBILITY_AUDIT: bool = os.getenv("ACCESSIBILITY_AUDIT", "false").lower() == "true"
    # Comma-separated: critical, serious, moderate, minor
    ACCESSIBILITY_FAIL_ON: str = os.getenv("ACCESSIBILITY_FAIL_ON", "critical,serious")

    # ── Capability: Performance Metrics ───────────────────────────────────────
    PERFORMANCE_METRICS: bool = os.getenv("PERFORMANCE_METRICS", "false").lower() == "true"
    PERFORMANCE_LCP_BUDGET_MS: int = int(os.getenv("PERFORMANCE_LCP_BUDGET_MS", "2500"))
    PERFORMANCE_LOAD_BUDGET_MS: int = int(os.getenv("PERFORMANCE_LOAD_BUDGET_MS", "5000"))
    # When true, a page that blows its LCP/load budget FAILS the test (hard gate)
    # instead of only logging a warning. Requires PERFORMANCE_METRICS=true.
    PERFORMANCE_GATE: bool = os.getenv("PERFORMANCE_GATE", "false").lower() == "true"

    # ── Capability: AI Test Summary ───────────────────────────────────────────
    AI_SUMMARY: bool = os.getenv("AI_SUMMARY", "false").lower() == "true"

    # ── Capability: Regression Detection / Alerts ─────────────────────────────
    # Compare the latest run to the median of prior runs and flag regressions.
    REGRESSION_ALERTS: bool = os.getenv("REGRESSION_ALERTS", "true").lower() == "true"
    # Min prior runs required before we alert (avoids crying wolf on thin history).
    REGRESSION_MIN_HISTORY: int = int(os.getenv("REGRESSION_MIN_HISTORY", "3"))
    # Alert if pass rate drops by this many percentage points vs baseline.
    REGRESSION_PASS_RATE_DROP: float = float(os.getenv("REGRESSION_PASS_RATE_DROP", "5.0"))
    # Alert if suite duration rises by this percent vs baseline.
    REGRESSION_DURATION_PCT: float = float(os.getenv("REGRESSION_DURATION_PCT", "25.0"))
    # Alert if avg LCP / load_time rises by this percent vs baseline.
    REGRESSION_PERF_PCT: float = float(os.getenv("REGRESSION_PERF_PCT", "25.0"))

    # ── Capability: Slack / Teams Notifications ───────────────────────────────
    SLACK_NOTIFICATIONS: bool = os.getenv("SLACK_NOTIFICATIONS", "false").lower() == "true"
    SLACK_WEBHOOK_URL: str = os.getenv("SLACK_WEBHOOK_URL", "")
    SLACK_CHANNEL: str = os.getenv("SLACK_CHANNEL", "#qa-alerts")
    TEAMS_WEBHOOK_URL: str = os.getenv("TEAMS_WEBHOOK_URL", "")

    # ── Capability: Report Retention / Auto-Pruning ───────────────────────────
    # Cap unbounded growth of run artifacts (functional_runs, load_runs,
    # screenshots, videos, a11y, visual_diffs, runs/*.json). Pruning runs
    # automatically after each functional/load run when RETENTION_ENABLED=true,
    # and on demand via `python -m tools.prune_reports`.
    RETENTION_ENABLED: bool = os.getenv("RETENTION_ENABLED", "true").lower() == "true"
    # Keep at most this many of the most-recent runs per category (0 = unlimited).
    RETENTION_MAX_RUNS: int = int(os.getenv("RETENTION_MAX_RUNS", "50"))
    # Drop runs older than this many days (0 = no age limit).
    RETENTION_MAX_AGE_DAYS: int = int(os.getenv("RETENTION_MAX_AGE_DAYS", "30"))
    # Total size ceiling for logs_and_reports/ in MB; oldest runs are dropped
    # first until under budget (0 = no size limit).
    RETENTION_MAX_SIZE_MB: int = int(os.getenv("RETENTION_MAX_SIZE_MB", "2048"))

    # ── Reporting ─────────────────────────────────────────────────────────────
    SCREENSHOT_ON_FAILURE: bool = os.getenv("SCREENSHOT_ON_FAILURE", "true").lower() == "true"
    SCREENSHOT_ALL_TESTS: bool = os.getenv("SCREENSHOT_ALL_TESTS", "true").lower() == "true"
    RECORD_VIDEO: bool = os.getenv("RECORD_VIDEO", "true").lower() == "true"
    TRACE_ON_FAILURE: bool = os.getenv("TRACE_ON_FAILURE", "true").lower() == "true"

    @classmethod
    def validate(cls) -> None:
        """Call at session start to catch missing required config early."""
        missing = []
        if not cls.BASE_URL:
            missing.append("BASE_URL")
        if not cls.TEST_USER_EMAIL:
            missing.append("TEST_USER_EMAIL")
        if not cls.TEST_USER_PASSWORD:
            missing.append("TEST_USER_PASSWORD")
        ai_features = [cls.ENABLE_AI_HEALING, cls.AI_SUMMARY]
        if any(ai_features):
            # AI features now work with any configured provider (cloud or local),
            # not just OpenAI. Validate the *selected* provider instead.
            try:
                from llm.service import get_service
                result = get_service().validate()
                if not result.ok:
                    provider = get_service().current_provider_name()
                    missing.append(
                        f"LLM provider '{provider}' is not configured ({result.detail}). "
                        "Set its API key, pick another provider via AI_PROVIDER, "
                        "or use a local provider (ollama/lmstudio)."
                    )
            except Exception as exc:  # pragma: no cover - defensive
                missing.append(f"LLM provider validation failed: {exc}")
        if cls.SLACK_NOTIFICATIONS and not cls.SLACK_WEBHOOK_URL:
            missing.append("SLACK_WEBHOOK_URL (required when SLACK_NOTIFICATIONS=true)")
        if missing:
            raise EnvironmentError(
                f"Missing required environment variables: {', '.join(missing)}. "
                "Check config/.env against config/.env.example."
            )
