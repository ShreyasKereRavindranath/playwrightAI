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

    # ── Capability: AI Test Summary ───────────────────────────────────────────
    AI_SUMMARY: bool = os.getenv("AI_SUMMARY", "false").lower() == "true"

    # ── Capability: Slack / Teams Notifications ───────────────────────────────
    SLACK_NOTIFICATIONS: bool = os.getenv("SLACK_NOTIFICATIONS", "false").lower() == "true"
    SLACK_WEBHOOK_URL: str = os.getenv("SLACK_WEBHOOK_URL", "")
    SLACK_CHANNEL: str = os.getenv("SLACK_CHANNEL", "#qa-alerts")
    TEAMS_WEBHOOK_URL: str = os.getenv("TEAMS_WEBHOOK_URL", "")

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
        if any(ai_features) and not cls.OPENAI_API_KEY:
            missing.append("OPENAI_API_KEY (required when any AI feature is enabled)")
        if cls.SLACK_NOTIFICATIONS and not cls.SLACK_WEBHOOK_URL:
            missing.append("SLACK_WEBHOOK_URL (required when SLACK_NOTIFICATIONS=true)")
        if missing:
            raise EnvironmentError(
                f"Missing required environment variables: {', '.join(missing)}. "
                "Check config/.env against config/.env.example."
            )
