"""
CheckoutPage — covers the full checkout pipeline:
  Step 1: /checkout-step-one.html  (customer info form)
  Step 2: /checkout-step-two.html  (order overview)
  Complete: /checkout-complete.html (confirmation)
"""

from playwright.sync_api import Locator
from pages.base_page import BasePage


class CheckoutPage(BasePage):

    # ── Step One Locators ─────────────────────────────────────────────────────

    @property
    def first_name_input(self) -> Locator:
        return self.page.locator('[data-test="firstName"]')

    @property
    def last_name_input(self) -> Locator:
        return self.page.locator('[data-test="lastName"]')

    @property
    def zip_code_input(self) -> Locator:
        return self.page.locator('[data-test="postalCode"]')

    @property
    def continue_button(self) -> Locator:
        return self.page.locator('[data-test="continue"]')

    @property
    def step_one_error(self) -> Locator:
        return self.page.locator('[data-test="error"]')

    # ── Step Two (Overview) Locators ──────────────────────────────────────────

    @property
    def finish_button(self) -> Locator:
        return self.page.locator('[data-test="finish"]')

    @property
    def cancel_button(self) -> Locator:
        return self.page.locator('[data-test="cancel"]')

    @property
    def item_total_label(self) -> Locator:
        return self.page.locator(".summary_subtotal_label")

    @property
    def total_label(self) -> Locator:
        return self.page.locator(".summary_total_label")

    @property
    def overview_items(self) -> Locator:
        return self.page.locator(".cart_item")

    # ── Checkout Complete Locators ────────────────────────────────────────────

    @property
    def confirmation_header(self) -> Locator:
        return self.page.locator(".complete-header")

    @property
    def confirmation_text(self) -> Locator:
        return self.page.locator(".complete-text")

    @property
    def back_to_products_button(self) -> Locator:
        return self.page.locator('[data-test="back-to-products"]')

    # ── Actions ───────────────────────────────────────────────────────────────

    def fill_customer_info(self, first_name: str, last_name: str, zip_code: str) -> None:
        self.fill(self.first_name_input, first_name)
        self.fill(self.last_name_input, last_name)
        self.fill(self.zip_code_input, zip_code)

    def continue_to_overview(self) -> None:
        self.click(self.continue_button)

    def finish_order(self) -> None:
        self.click(self.finish_button)

    def cancel_checkout(self) -> None:
        self.click(self.cancel_button)

    def go_back_to_products(self) -> None:
        self.click(self.back_to_products_button)
