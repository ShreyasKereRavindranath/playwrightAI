# DO_NOT_DO.md — Global Strict Restrictions

> **Scope:** These rules apply to ALL contributors and AI agents generating code within this framework.
> Violations will cause test instability, maintenance nightmares, and framework rot.
> When in doubt — read this file first.

---

## 1. LOCATOR ANTI-PATTERNS — NEVER DO THESE

### 1.1 Fragile Locator Strategies
- **NEVER** use auto-generated, index-based XPath such as:
  ```python
  # BAD
  page.locator("//div[3]/span[1]/button[2]")
  ```
- **NEVER** rely on positional CSS selectors that break with UI reordering:
  ```python
  # BAD
  page.locator("ul > li:nth-child(3) > a")
  ```
- **NEVER** build locators from raw inner text that changes across environments:
  ```python
  # BAD
  page.locator("text=Submit Order Now")  # text may vary per locale/env
  ```
- **NEVER** chain more than 3 locator scopes without introducing an intermediate Page Object variable.
- **NEVER** use `page.query_selector_all()` inside assertions — use `locator.count()` instead.

### 1.2 Preferred Locator Priority Order
Strictly follow this hierarchy, top = most preferred:
1. `data-testid` attribute → `page.locator('[data-testid="submit-btn"]')`
2. ARIA role + accessible name → `page.get_by_role("button", name="Submit")`
3. Stable `id` attribute → `page.locator("#submit-button")`
4. Stable `name` attribute → `page.locator('[name="username"]')`
5. Semantic CSS class (non-dynamic) → `page.locator(".login-form__submit")`
6. XPath — **only** as last resort, must be reviewed by a senior engineer before merge.

---

## 2. TIMING & SYNCHRONIZATION — NEVER DO THESE

- **NEVER** use `time.sleep()` or `asyncio.sleep()` for synchronization:
  ```python
  # BAD
  import time
  time.sleep(3)
  page.click("#submit")
  ```
- **NEVER** use hardcoded waits of any kind including `page.wait_for_timeout(3000)` unless
  explicitly wrapping a known third-party animation with a documented comment.
- **NEVER** assume an element is ready after `page.navigate()` returns — always assert on
  an expected element or URL state using `expect()`.
- **NEVER** poll for element state in a raw `while` loop — use Playwright's built-in
  `wait_for_selector`, `wait_for_load_state`, or `expect(locator).to_be_visible()`.

---

## 3. HARDCODING — NEVER DO THESE

- **NEVER** hardcode environment URLs, credentials, or tokens directly in test files:
  ```python
  # BAD
  page.goto("https://prod.myapp.com/login")
  page.fill("#password", "Admin123!")
  ```
- **NEVER** hardcode browser type, viewport sizes, or timeouts in test bodies — all must
  come from `config/config.py` or `.env`.
- **NEVER** hardcode test data inside test functions — use fixtures or `data/` files.
- **NEVER** store secrets in `.env` files that are committed to source control. `.env` is
  always in `.gitignore`; use `.env.example` for schema reference only.

---

## 4. CODE STRUCTURE — NEVER DO THESE

### 4.1 Test File Violations
- **NEVER** write raw Playwright API calls (`page.click`, `page.fill`) directly inside
  `tests/` files. All UI interactions belong in `pages/` Page Object classes.
- **NEVER** import `page` directly into a test — always receive it through a pytest fixture.
- **NEVER** create test functions without a descriptive docstring and at least one `allure`
  marker (or equivalent) for reporting.
- **NEVER** write more than one logical scenario per test function. One test = one behavior.
- **NEVER** create test dependencies (test A must run before test B) — all tests must be
  independently executable and idempotent.

### 4.2 Page Object Violations
- **NEVER** instantiate `Page` objects directly inside Page Object constructors — always
  receive the `page` fixture via injection.
- **NEVER** put assertions (`assert`, `expect`) inside Page Object methods. Page Objects
  describe **actions**, not **verifications**. Verifications belong in `tests/`.
- **NEVER** duplicate locator definitions across multiple Page Object classes. If a component
  appears on multiple pages (e.g., a nav bar), create a shared `components/` class.
- **NEVER** mix data-fetching logic (API calls, DB queries) with UI interaction logic in
  the same Page Object method.

### 4.3 Configuration Violations
- **NEVER** import `os.environ` directly in test files — always go through `config/config.py`.
- **NEVER** create multiple sources of truth for the same config value (e.g., base URL
  defined in both `pytest.ini` and `config.py`).

---

## 5. AI AGENT CODE GENERATION — NEVER DO THESE

- **NEVER** generate locators based on visual description alone without verifying against
  the DOM via browser DevTools or Playwright Inspector.
- **NEVER** generate a Page Object for a page you haven't inspected the actual HTML structure of.
- **NEVER** regenerate an entire file when only a method needs updating — use surgical edits.
- **NEVER** introduce new third-party packages without adding them to `requirements.txt` and
  documenting the reason in `HOW_TO_PROCEED.md`.
- **NEVER** create helper utilities inside `pages/` or `tests/` — utilities belong in `utils/`.
- **NEVER** create fixtures inside individual test files — all reusable fixtures belong in
  `tests/conftest.py`.
- **NEVER** output code with `TODO`, `FIXME`, or placeholder strings like `"REPLACE_ME"` —
  every generated artifact must be complete and immediately executable.

---

## 6. REPORTING & LOGGING — NEVER DO THESE

- **NEVER** print debug output with `print()` — use the Python `logging` module with the
  framework's configured logger from `utils/logger.py`.
- **NEVER** delete or overwrite files in `logs_and_reports/` programmatically during test
  setup — archiving is handled by the CI pipeline.
- **NEVER** suppress exceptions silently with bare `except: pass` blocks — always log and
  re-raise.

---

## 7. PARALLEL EXECUTION — NEVER DO THESE

- **NEVER** use shared mutable state (module-level variables, class-level counters) across
  tests that may run in parallel via `pytest-xdist`.
- **NEVER** write tests that depend on execution order or shared browser state.
- **NEVER** hardcode port numbers for local test servers — use dynamic port allocation.

---

> Last reviewed: 2026-06-26
> Owner: QA Automation Team
