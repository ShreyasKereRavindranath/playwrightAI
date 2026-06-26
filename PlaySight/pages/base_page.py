"""
Base Page Object — all page classes must inherit from this.

Wraps Playwright's Page API with:
- Smart explicit waits on every interaction
- Consistent logging
- Retry-safe click/fill helpers
- Screenshot-on-failure support (called from conftest hooks)
"""

import logging
from typing import Optional
from playwright.sync_api import Page, Locator, expect, TimeoutError as PlaywrightTimeoutError

from config.config import Config

logger = logging.getLogger(__name__)


class BasePage:
    """Abstract base for all Page Objects.

    Usage:
        class LoginPage(BasePage):
            def navigate(self):
                self.goto(LoginPage.URL)
    """

    def __init__(self, page: Page) -> None:
        self.page = page
        self._timeout = Config.DEFAULT_TIMEOUT
        if Config.PERFORMANCE_METRICS:
            self._inject_perf_observer()

    def _inject_perf_observer(self) -> None:
        """Inject LCP/CLS PerformanceObservers before every navigation."""
        from utils.performance import PERF_INIT_SCRIPT
        try:
            self.page.add_init_script(PERF_INIT_SCRIPT)
        except Exception:
            pass

    # ── Navigation ────────────────────────────────────────────────────────────

    def goto(self, path: str) -> None:
        """Navigate to a path relative to BASE_URL and wait for network idle."""
        url = f"{Config.BASE_URL.rstrip('/')}/{path.lstrip('/')}"
        logger.info("Navigating to: %s", url)
        self.page.goto(url)
        self.page.wait_for_load_state("domcontentloaded")

    def goto_absolute(self, url: str) -> None:
        """Navigate to an absolute URL (use sparingly — prefer goto())."""
        logger.info("Navigating to absolute URL: %s", url)
        self.page.goto(url)
        self.page.wait_for_load_state("domcontentloaded")

    # ── Core Interactions ─────────────────────────────────────────────────────

    def click(self, locator: Locator, timeout: Optional[int] = None) -> None:
        """Wait for element to be visible and enabled, then click."""
        t = timeout or self._timeout
        logger.debug("Clicking: %s", locator)
        locator.wait_for(state="visible", timeout=t)
        locator.click(timeout=t)

    def fill(self, locator: Locator, value: str, timeout: Optional[int] = None) -> None:
        """Clear and fill an input field, waiting for it to be editable first."""
        t = timeout or self._timeout
        logger.debug("Filling '%s' into: %s", value, locator)
        locator.wait_for(state="visible", timeout=t)
        locator.clear()
        locator.fill(value, timeout=t)

    def type_text(self, locator: Locator, text: str, delay: int = 50) -> None:
        """Type text character-by-character (use for inputs that respond to keystroke events)."""
        logger.debug("Typing '%s' into: %s", text, locator)
        locator.wait_for(state="visible", timeout=self._timeout)
        locator.press_sequentially(text, delay=delay)

    def select_option(self, locator: Locator, value: str) -> None:
        """Select a <select> dropdown option by visible text or value."""
        logger.debug("Selecting '%s' in: %s", value, locator)
        locator.wait_for(state="visible", timeout=self._timeout)
        locator.select_option(value)

    def check(self, locator: Locator) -> None:
        """Check a checkbox or radio button."""
        logger.debug("Checking: %s", locator)
        locator.wait_for(state="visible", timeout=self._timeout)
        locator.check()

    def uncheck(self, locator: Locator) -> None:
        """Uncheck a checkbox."""
        logger.debug("Unchecking: %s", locator)
        locator.wait_for(state="visible", timeout=self._timeout)
        locator.uncheck()

    def hover(self, locator: Locator) -> None:
        """Hover over an element to reveal tooltips or dropdowns."""
        logger.debug("Hovering: %s", locator)
        locator.wait_for(state="visible", timeout=self._timeout)
        locator.hover()

    def press_key(self, locator: Locator, key: str) -> None:
        """Focus locator and press a keyboard key (e.g. 'Enter', 'Tab', 'Escape')."""
        logger.debug("Pressing '%s' on: %s", key, locator)
        locator.press(key)

    def upload_file(self, locator: Locator, file_path: str) -> None:
        """Set a file input's value to the given local file path."""
        logger.debug("Uploading '%s' to: %s", file_path, locator)
        locator.set_input_files(file_path)

    # ── Waiting Helpers ────────────────────────────────────────────────────────

    def wait_for_visible(self, locator: Locator, timeout: Optional[int] = None) -> None:
        """Block until the locator is visible."""
        locator.wait_for(state="visible", timeout=timeout or self._timeout)

    def wait_for_hidden(self, locator: Locator, timeout: Optional[int] = None) -> None:
        """Block until the locator is hidden or detached."""
        locator.wait_for(state="hidden", timeout=timeout or self._timeout)

    def wait_for_url(self, pattern: str, timeout: Optional[int] = None) -> None:
        """Block until the page URL matches the given glob or regex pattern."""
        logger.debug("Waiting for URL pattern: %s", pattern)
        self.page.wait_for_url(pattern, timeout=timeout or self._timeout)

    def wait_for_network_idle(self, timeout: Optional[int] = None) -> None:
        """Block until there are no in-flight network requests."""
        self.page.wait_for_load_state("networkidle", timeout=timeout or self._timeout)

    # ── State Queries (non-asserting) ──────────────────────────────────────────

    def is_visible(self, locator: Locator) -> bool:
        """Return True if the element is currently visible, False otherwise."""
        return locator.is_visible()

    def is_enabled(self, locator: Locator) -> bool:
        """Return True if the element is currently enabled."""
        return locator.is_enabled()

    def get_text(self, locator: Locator) -> str:
        """Return trimmed inner text of the element."""
        locator.wait_for(state="visible", timeout=self._timeout)
        return (locator.inner_text() or "").strip()

    def get_attribute(self, locator: Locator, attribute: str) -> Optional[str]:
        """Return the value of a given HTML attribute, or None if absent."""
        return locator.get_attribute(attribute)

    def get_input_value(self, locator: Locator) -> str:
        """Return the current value of an input or textarea element."""
        locator.wait_for(state="visible", timeout=self._timeout)
        return locator.input_value()

    def count(self, locator: Locator) -> int:
        """Return the number of elements matching the locator."""
        return locator.count()

    # ── JavaScript Utilities ───────────────────────────────────────────────────

    def scroll_to(self, locator: Locator) -> None:
        """Scroll the element into the viewport."""
        locator.scroll_into_view_if_needed()

    def scroll_to_bottom(self) -> None:
        """Scroll the page to the very bottom."""
        self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")

    # ── Screenshot / Debug ────────────────────────────────────────────────────

    def take_screenshot(self, name: str = "screenshot") -> bytes:
        """Capture a full-page screenshot and save it to logs_and_reports/."""
        path = f"logs_and_reports/{name}.png"
        logger.info("Taking screenshot: %s", path)
        return self.page.screenshot(path=path, full_page=True)

    # ── AI Self-Healing Hook ───────────────────────────────────────────────────

    def safe_click(self, locator: Locator, intent: str, timeout: Optional[int] = None) -> None:
        """Click with AI self-healing fallback.

        If the locator fails, delegates to utils.ai_self_heal to recover a
        candidate locator based on the DOM snapshot and the human-readable intent.
        The healed locator is logged for Page Object maintenance.
        """
        try:
            self.click(locator, timeout=timeout)
        except PlaywrightTimeoutError:
            logger.warning("Locator timed out. Attempting AI self-heal for intent: '%s'", intent)
            self._attempt_ai_heal(intent, action="click")

    def safe_fill(
        self, locator: Locator, value: str, intent: str, timeout: Optional[int] = None
    ) -> None:
        """Fill with AI self-healing fallback (see safe_click for healing behavior)."""
        try:
            self.fill(locator, value, timeout=timeout)
        except PlaywrightTimeoutError:
            logger.warning("Locator timed out. Attempting AI self-heal for intent: '%s'", intent)
            self._attempt_ai_heal(intent, action="fill", value=value)

    def _attempt_ai_heal(self, intent: str, action: str, value: str = "") -> None:
        """Internal — invoke AI healing module if enabled in config."""
        from config.config import Config

        if not Config.ENABLE_AI_HEALING:
            raise PlaywrightTimeoutError(
                f"Element not found for intent '{intent}'. "
                "AI healing is disabled. Update the Page Object locator."
            )

        from utils.ai_self_heal import AISelfHeal

        healer = AISelfHeal(self.page)
        healed_locator = healer.heal(intent=intent, page_html=self.page.content())

        if healed_locator:
            logger.warning(
                "AI healed locator for '%s': %s — UPDATE THE PAGE OBJECT", intent, healed_locator
            )
            target = self.page.locator(healed_locator)
            if action == "click":
                self.click(target)
            elif action == "fill":
                self.fill(target, value)
        else:
            raise PlaywrightTimeoutError(
                f"AI healing failed for intent: '{intent}'. Manual Page Object update required."
            )
