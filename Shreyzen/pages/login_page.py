"""
LoginPage — https://www.saucedemo.com/
Covers: username/password login and error state inspection.
"""

from playwright.sync_api import Locator
from pages.base_page import BasePage


class LoginPage(BasePage):
    URL = "/"

    # ── Locators ──────────────────────────────────────────────────────────────

    @property
    def username_input(self) -> Locator:
        return self.page.locator('[data-test="username"]')

    @property
    def password_input(self) -> Locator:
        return self.page.locator('[data-test="password"]')

    @property
    def login_button(self) -> Locator:
        return self.page.locator('[data-test="login-button"]')

    @property
    def error_message(self) -> Locator:
        return self.page.locator('[data-test="error"]')

    @property
    def error_close_button(self) -> Locator:
        return self.page.locator('[data-test="error"] button')

    # ── Navigation ────────────────────────────────────────────────────────────

    def navigate(self) -> None:
        self.goto(self.URL)

    # ── Actions ───────────────────────────────────────────────────────────────

    def login(self, username: str, password: str) -> None:
        self.fill(self.username_input, username)
        self.fill(self.password_input, password)
        self.click(self.login_button)

    def dismiss_error(self) -> None:
        self.click(self.error_close_button)
