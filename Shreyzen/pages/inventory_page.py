"""
InventoryPage — https://www.saucedemo.com/inventory.html
Covers: product listing, adding items to cart, navigating to cart.
"""

from playwright.sync_api import Locator
from pages.base_page import BasePage


class InventoryPage(BasePage):
    URL = "/inventory.html"

    # ── Locators ──────────────────────────────────────────────────────────────

    @property
    def page_title(self) -> Locator:
        return self.page.locator(".title")

    @property
    def cart_badge(self) -> Locator:
        return self.page.locator(".shopping_cart_badge")

    @property
    def cart_link(self) -> Locator:
        return self.page.locator(".shopping_cart_link")

    @property
    def inventory_items(self) -> Locator:
        return self.page.locator(".inventory_item")

    @property
    def sort_dropdown(self) -> Locator:
        return self.page.locator('[data-test="product-sort-container"]')

    def add_to_cart_button(self, product_key: str) -> Locator:
        """Return the 'Add to cart' button for a specific product by its data-test key.

        product_key examples: 'sauce-labs-backpack', 'sauce-labs-bike-light'
        """
        return self.page.locator(f'[data-test="add-to-cart-{product_key}"]')

    def remove_button(self, product_key: str) -> Locator:
        return self.page.locator(f'[data-test="remove-{product_key}"]')

    def product_name(self, name: str) -> Locator:
        return self.page.get_by_text(name, exact=True)

    # ── Actions ───────────────────────────────────────────────────────────────

    def add_product_to_cart(self, product_key: str) -> None:
        self.click(self.add_to_cart_button(product_key))

    def remove_product_from_cart(self, product_key: str) -> None:
        self.click(self.remove_button(product_key))

    def go_to_cart(self) -> None:
        self.click(self.cart_link)

    def sort_products_by(self, option: str) -> None:
        """Sort options: 'az', 'za', 'lohi', 'hilo'."""
        self.select_option(self.sort_dropdown, option)
