"""
End-to-end tests for saucedemo.com covering:
  - Login (smoke + negative)
  - Full purchase flow: login → add to cart → checkout → confirmation (regression)
"""

import re
import pytest
from playwright.sync_api import expect

from pages.login_page import LoginPage
from pages.inventory_page import InventoryPage
from pages.cart_page import CartPage
from pages.checkout_page import CheckoutPage

pytestmark = pytest.mark.web


# ═══════════════════════════════════════════════════════
# LOGIN TESTS
# ═══════════════════════════════════════════════════════

@pytest.mark.smoke
def test_login_valid_credentials_redirects_to_inventory(
    login_page: LoginPage, e2e_data: dict
):
    """
    Scenario: Standard user logs in with valid credentials.
    Expected: Redirected to inventory page with 'Products' heading visible.
    """
    user = e2e_data["users"]["standard"]

    # Arrange
    login_page.navigate()

    # Act
    login_page.login(user["username"], user["password"])

    # Assert
    expect(login_page.page).to_have_url(re.compile(r"/inventory\.html"))
    expect(login_page.page.locator(".title")).to_have_text("Products")


@pytest.mark.regression
def test_login_locked_out_user_shows_error(
    login_page: LoginPage, e2e_data: dict
):
    """
    Scenario: A locked-out user attempts to login.
    Expected: A descriptive error message is displayed; user stays on login page.
    """
    user = e2e_data["users"]["locked_out"]

    # Arrange
    login_page.navigate()

    # Act
    login_page.login(user["username"], user["password"])

    # Assert
    expect(login_page.error_message).to_be_visible()
    expect(login_page.error_message).to_contain_text(user["expected_error"])
    expect(login_page.page).not_to_have_url(re.compile(r"/inventory\.html"))


@pytest.mark.regression
@pytest.mark.parametrize("username,password,error_fragment", [
    ("", "secret_sauce", "Username is required"),
    ("standard_user", "", "Password is required"),
    ("wrong_user", "wrong_pass", "Username and password do not match"),
], ids=["empty_username", "empty_password", "wrong_credentials"])
def test_login_validation_errors(
    login_page: LoginPage, username: str, password: str, error_fragment: str
):
    """
    Scenario: User submits login form with missing or incorrect credentials.
    Expected: Appropriate inline error is shown for each case.
    """
    # Arrange
    login_page.navigate()

    # Act
    login_page.login(username, password)

    # Assert
    expect(login_page.error_message).to_be_visible()
    expect(login_page.error_message).to_contain_text(error_fragment)


# ═══════════════════════════════════════════════════════
# FULL E2E PURCHASE FLOW
# ═══════════════════════════════════════════════════════

@pytest.mark.smoke
@pytest.mark.regression
def test_full_purchase_flow(
    login_page: LoginPage,
    inventory_page: InventoryPage,
    cart_page: CartPage,
    checkout_page: CheckoutPage,
    e2e_data: dict,
):
    """
    Scenario: Standard user completes full purchase flow end-to-end.
    Expected: Order confirmation page shows 'Thank you for your order!'

    Flow:
      Login → Inventory (add item) → Cart (verify item) →
      Checkout Step 1 (fill info) → Step 2 (review) → Confirmation
    """
    user = e2e_data["users"]["standard"]
    product = e2e_data["products"]["backpack"]
    customer = e2e_data["checkout"]["valid_customer"]

    # ── Step 1: Login ─────────────────────────────────────────────────────────
    login_page.navigate()
    login_page.login(user["username"], user["password"])
    expect(login_page.page).to_have_url(re.compile(r"/inventory\.html"))

    # ── Step 2: Add product to cart ───────────────────────────────────────────
    inventory_page.add_product_to_cart(product["add_to_cart_key"])
    expect(inventory_page.cart_badge).to_be_visible()
    expect(inventory_page.cart_badge).to_have_text("1")

    # ── Step 3: Open cart and verify product is present ───────────────────────
    inventory_page.go_to_cart()
    expect(login_page.page).to_have_url(re.compile(r"/cart\.html"))
    expect(cart_page.first_item_name).to_have_text(product["name"])

    # ── Step 4: Start checkout — fill customer information ────────────────────
    cart_page.proceed_to_checkout()
    expect(login_page.page).to_have_url(re.compile(r"/checkout-step-one\.html"))
    checkout_page.fill_customer_info(
        customer["first_name"],
        customer["last_name"],
        customer["zip_code"],
    )
    checkout_page.continue_to_overview()

    # ── Step 5: Review order overview ─────────────────────────────────────────
    expect(login_page.page).to_have_url(re.compile(r"/checkout-step-two\.html"))
    expect(checkout_page.finish_button).to_be_enabled()
    expect(checkout_page.item_total_label).to_contain_text(product["expected_price"])

    # ── Step 6: Finish and verify confirmation ────────────────────────────────
    checkout_page.finish_order()
    expect(login_page.page).to_have_url(re.compile(r"/checkout-complete\.html"))
    expect(checkout_page.confirmation_header).to_have_text("Thank you for your order!")
    expect(checkout_page.back_to_products_button).to_be_visible()


# ═══════════════════════════════════════════════════════
# CART TESTS
# ═══════════════════════════════════════════════════════

@pytest.mark.regression
def test_cart_item_count_updates_on_add(
    authenticated_page,
    inventory_page: InventoryPage,
    e2e_data: dict,
):
    """
    Scenario: Authenticated user adds an item; cart badge updates immediately.
    Expected: Cart badge shows '1' after adding one product.
    """
    product = e2e_data["products"]["backpack"]

    # Act
    inventory_page.add_product_to_cart(product["add_to_cart_key"])

    # Assert
    expect(inventory_page.cart_badge).to_have_text("1")


@pytest.mark.regression
def test_cart_is_empty_after_removing_item(
    authenticated_page,
    inventory_page: InventoryPage,
    cart_page: CartPage,
    e2e_data: dict,
):
    """
    Scenario: User adds an item then removes it from the cart page.
    Expected: Cart is empty; cart badge disappears.
    """
    product = e2e_data["products"]["backpack"]

    # Arrange — add product then navigate to cart
    inventory_page.add_product_to_cart(product["add_to_cart_key"])
    inventory_page.go_to_cart()

    # Act
    cart_page.remove_item(product["add_to_cart_key"])

    # Assert
    expect(cart_page.cart_items).to_have_count(0)
    expect(inventory_page.cart_badge).not_to_be_visible()
