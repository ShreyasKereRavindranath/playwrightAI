"""
Mobile web tests — saucedemo.com under mobile-device emulation.

These reuse the same Page Objects as the desktop web tests but run inside a
mobile context (see tests/mobile/conftest.py): mobile viewport, touch, and a
mobile user-agent. They prove the responsive UI works on a phone-sized screen.
"""

import re

import pytest
from playwright.sync_api import Page, expect

from config.config import Config
from pages.inventory_page import InventoryPage
from pages.login_page import LoginPage

pytestmark = pytest.mark.mobile


@pytest.mark.smoke
def test_mobile_login_and_add_to_cart(page: Page, e2e_data: dict):
    """
    Scenario: A user logs in and adds a product to the cart on a phone.
    Expected: Reaches the inventory page and the cart badge shows one item,
              all within a mobile-width viewport.
    """
    user = e2e_data["users"]["standard"]
    product_key = e2e_data["products"]["backpack"]["add_to_cart_key"]

    # Arrange / Act — login on mobile
    login = LoginPage(page)
    login.navigate()
    login.login(user["username"], user["password"])

    # Assert — landed on inventory
    expect(page).to_have_url(re.compile(r"/inventory\.html"))

    # Act — add to cart
    inventory = InventoryPage(page)
    inventory.add_product_to_cart(product_key)

    # Assert — cart reflects the addition
    expect(inventory.cart_badge).to_have_text("1")

    # Assert — we are genuinely running a mobile-width viewport
    viewport = page.viewport_size
    assert viewport and viewport["width"] <= 500, \
        f"expected a mobile-width viewport, got {viewport}"


@pytest.mark.regression
def test_mobile_touch_emulation_enabled(page: Page):
    """
    Scenario: The mobile context should expose touch capabilities.
    Expected: navigator.maxTouchPoints > 0 (touch emulation is active).
    """
    page.goto(Config.BASE_URL)
    touch_enabled = page.evaluate(
        "() => (navigator.maxTouchPoints || 0) > 0 || 'ontouchstart' in window"
    )
    assert touch_enabled, "expected touch to be enabled under mobile emulation"
