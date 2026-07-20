# TEST_GUIDELINES.md — Test Layer Rules

> **Scope:** Every file inside `tests/` must comply with these rules.
> AI agents generating test code must read this file in full before producing any output.

---

## 1. WHAT THIS DIRECTORY CONTAINS

The `tests/` directory contains all Pytest test cases, the shared `conftest.py` fixture
module, and sub-directories organized **by layer** — `api`, `web`, and `mobile`.

```
tests/
├── conftest.py              → ALL shared fixtures (page objects, auth, test data)
├── api/                     → API-layer tests (no browser) — @pytest.mark.api
│   ├── conftest.py          → API session/auth fixtures
│   └── test_api_contracts.py
├── web/                     → Browser UI tests, desktop viewport — @pytest.mark.web
│   ├── test_purchase_flow.py
│   └── generated/           → Generator-agent output (review before committing)
├── mobile/                  → Browser UI tests, mobile emulation — @pytest.mark.mobile
│   ├── conftest.py          → mobile device context override (Playwright devices)
│   └── test_mobile_shopping.py
└── TEST_GUIDELINES.md       → This file
```

> **Layer rule:** Put a new test in the folder that matches its layer (`api` / `web` /
> `mobile`) and tag it with the matching marker (`pytestmark = pytest.mark.<layer>`),
> so it can be selected by folder **or** marker.

---

## 2. TEST FILE NAMING CONVENTION

| Item | Convention | Example |
|------|-----------|---------|
| File | `test_<feature>.py` | `test_login.py` |
| Function | `test_<feature>_<scenario>_<outcome>` | `test_login_invalid_password_shows_error` |
| Class (optional grouping) | `Test<Feature>` | `TestLoginFlow` |
| Fixture | `<noun>_<scope>` or just `<noun>` | `login_page`, `authenticated_page` |

---

## 3. FIXTURE RULES

### 3.1 All Reusable Fixtures Go in conftest.py
- Root `tests/conftest.py` — framework-wide fixtures (page objects, browser config, auth)
- Module `tests/<module>/conftest.py` — fixtures scoped to that module only
- **NEVER** define reusable fixtures inside individual `test_*.py` files

### 3.2 Required Fixture Pattern for Page Objects
```python
# tests/conftest.py
import pytest
from playwright.sync_api import Page
from pages.login_page import LoginPage
from pages.dashboard_page import DashboardPage

@pytest.fixture
def login_page(page: Page) -> LoginPage:
    return LoginPage(page)

@pytest.fixture
def dashboard_page(page: Page) -> DashboardPage:
    return DashboardPage(page)

@pytest.fixture
def authenticated_page(page: Page, login_page: LoginPage) -> Page:
    """Returns a page already in an authenticated state."""
    login_page.navigate()
    login_page.login(
        username=config.TEST_USER_EMAIL,
        password=config.TEST_USER_PASSWORD
    )
    return page
```

### 3.3 Test Data Fixtures
```python
import json
from pathlib import Path

@pytest.fixture
def login_data():
    data_path = Path(__file__).parent.parent / "data" / "login_test_data.json"
    with open(data_path) as f:
        return json.load(f)
```

### 3.4 Fixture Scope Guidelines
| Scope | When to Use |
|-------|------------|
| `function` (default) | Stateful objects that should reset between tests |
| `module` | Expensive but safe to share across a file (e.g., read-only API data) |
| `session` | Browser-level setup, authentication tokens that can be reused |
| `class` | Grouping related tests that share setup but not across the whole module |

---

## 4. TEST FUNCTION RULES

### 4.1 Structure: Arrange → Act → Assert
Every test must follow this three-part structure with clear visual separation:

```python
def test_login_with_valid_credentials_shows_dashboard(login_page, login_data):
    """
    Scenario: User logs in with correct credentials.
    Expected: Redirected to dashboard with welcome message visible.
    """
    # Arrange
    credentials = login_data["valid_user"]
    login_page.navigate()

    # Act
    login_page.login(credentials["email"], credentials["password"])

    # Assert
    expect(login_page.page).to_have_url(re.compile(r"/dashboard"))
    expect(dashboard_page.welcome_heading).to_be_visible()
```

### 4.2 One Behavior Per Test
- Each test function covers **exactly one user behavior or assertion group**
- If you find yourself writing `# scenario 2` mid-function, split it into a new test
- Tests should be readable as plain English when the function name is expanded

### 4.3 Assertions
- **Always** use `playwright.sync_api.expect()` for UI state assertions — never raw `assert`
  on locators (except for Python object/data assertions)
- Chain multiple expectations about the same element rather than writing separate tests:
  ```python
  # Good — one logical assertion group
  expect(error_banner).to_be_visible()
  expect(error_banner).to_contain_text("Invalid credentials")

  # Bad — split into two tests for trivial variations
  ```
- Use `assert` for non-Playwright assertions (data values, API response fields, Python objects)

### 4.4 Docstrings
Every test function must have a docstring with at minimum:
- `Scenario:` — what user action is being tested
- `Expected:` — what the correct outcome is

---

## 5. PYTEST MARKERS

Use markers to categorize tests. Register all markers in `pytest.ini`.

```python
@pytest.mark.smoke
def test_login_smoke(login_page, login_data):
    ...

@pytest.mark.regression
@pytest.mark.parametrize("email,password,error", [
    ("", "pass", "Email is required"),
    ("bad@email", "", "Password is required"),
])
def test_login_validation_errors(login_page, email, password, error):
    ...
```

| Marker | Use Case |
|--------|----------|
| `@pytest.mark.api` | API-layer tests (no browser) — folder `tests/api/` |
| `@pytest.mark.web` | Browser UI tests, desktop — folder `tests/web/` |
| `@pytest.mark.mobile` | Browser UI tests, mobile emulation — folder `tests/mobile/` |
| `@pytest.mark.smoke` | Critical path, run on every deploy |
| `@pytest.mark.regression` | Full suite, run nightly or pre-release |
| `@pytest.mark.e2e` | End-to-end multi-page user journeys |
| `@pytest.mark.negative` | Negative / invalid-input scenarios |
| `@pytest.mark.accessibility` | Accessibility (a11y) assertions |
| `@pytest.mark.visual` | Visual-regression / screenshot comparison |
| `@pytest.mark.slow` | Tests that take >30s; excluded from fast runs |
| `@pytest.mark.skip(reason="...")` | Temporarily disabled — must include a reason |
| `@pytest.mark.xfail(reason="...")` | Known failure; tracked in a ticket |

---

## 6. DATA-DRIVEN TESTING PATTERN

Prefer `@pytest.mark.parametrize` for data variations of the same scenario:

```python
@pytest.mark.parametrize("username,password,expected_error", [
    ("", "ValidPass1!", "Username is required"),
    ("user@test.com", "", "Password is required"),
    ("user@test.com", "wrong", "Invalid credentials"),
], ids=["empty_username", "empty_password", "wrong_password"])
def test_login_validation(login_page, username, password, expected_error):
    """
    Scenario: User submits login form with invalid inputs.
    Expected: Appropriate validation error is shown for each case.
    """
    # Arrange
    login_page.navigate()

    # Act
    login_page.login(username, password)

    # Assert
    expect(login_page.error_message).to_contain_text(expected_error)
```

For large datasets, load from `data/*.json` via fixtures instead of inline parametrize lists.

---

## 7. INDEPENDENCE & IDEMPOTENCY RULES

- Every test must be able to run as the first — and only — test in the suite
- Tests must clean up any created data (users, records) in teardown via fixture `yield`
- If a test requires pre-existing data, that data must be created in the fixture, not assumed
- Tests that share a session-scoped browser state must be safe to run in any order

---

## 8. PARALLEL EXECUTION SAFETY

When `pytest-xdist` runs tests in parallel (`-n auto`):
- Do not use module-level mutable variables in test files
- Do not rely on file system paths that could collide (use unique names with `tmp_path` fixture)
- Each worker gets its own browser context — do not share state across fixtures of different scopes

---

## 9. AI AGENT TEST GENERATION CHECKLIST

Before generating a test, an AI agent must confirm:

- [ ] The corresponding Page Object exists in `pages/` for every UI interaction needed
- [ ] All fixtures are imported from or defined in `tests/conftest.py`
- [ ] Test follows Arrange → Act → Assert structure
- [ ] Only `expect()` is used for UI assertions, `assert` for data/Python assertions
- [ ] At least one pytest marker is applied
- [ ] A docstring with `Scenario:` and `Expected:` is present
- [ ] No raw `page.*` or locator calls appear directly in the test body
- [ ] Test data is loaded from `data/` via a fixture, not hardcoded inline
- [ ] Test is independently runnable: `pytest tests/path/to/test_file.py::test_function_name`

---

> Last reviewed: 2026-07-20
> Owner: QA Automation Team
