"""
CartPage — https://www.saucedemo.com/cart.html
Covers: viewing cart contents, removing items, navigating to checkout.
"""

from playwright.sync_api import Locator
from pages.base_page import BasePage


class CartPage(BasePage):
    URL = "/cart.html"

    # ── Locators ──────────────────────────────────────────────────────────────

    @property
    def cart_items(self) -> Locator:
        return self.page.locator(".cart_item")

    @property
    def first_item_name(self) -> Locator:
        return self.cart_items.first.locator(".inventory_item_name")

    @property
    def checkout_button(self) -> Locator:
        return self.page.locator('[data-test="checkout"]')

    @property
    def continue_shopping_button(self) -> Locator:
        return self.page.locator('[data-test="continue-shopping"]')

    def item_name_in_cart(self, name: str) -> Locator:
        return self.page.get_by_text(name, exact=True)

    def remove_item_button(self, product_key: str) -> Locator:
        return self.page.locator(f'[data-test="remove-{product_key}"]')

    # ── Actions ───────────────────────────────────────────────────────────────

    def proceed_to_checkout(self) -> None:
        self.click(self.checkout_button)

    def continue_shopping(self) -> None:
        self.click(self.continue_shopping_button)

    def remove_item(self, product_key: str) -> None:
        self.click(self.remove_item_button(product_key))
