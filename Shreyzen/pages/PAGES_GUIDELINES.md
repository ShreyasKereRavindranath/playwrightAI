# PAGES_GUIDELINES.md — Page Object Layer Rules

> **Scope:** Every file inside `pages/` must comply with these rules.
> AI agents generating Page Object code must read this file in full before producing any output.

---

## 1. WHAT THIS DIRECTORY CONTAINS

The `pages/` directory contains the **Page Object Model (POM)** layer of the framework.
Each file maps to one logical page or major UI section of the application under test.

```
pages/
├── base_page.py         → Abstract base class all Page Objects must inherit
├── login_page.py        → Example: login screen interactions
├── dashboard_page.py    → Example: main dashboard interactions
├── components/          → Shared UI components reused across pages (nav, modals, etc.)
│   └── nav_bar.py
└── PAGES_GUIDELINES.md  → This file
```

---

## 2. FILE NAMING CONVENTION

| Pattern | Example |
|---------|---------|
| `<feature>_page.py` | `login_page.py`, `checkout_page.py` |
| `<feature>_component.py` in `components/` | `nav_bar.py`, `date_picker.py` |
| Class name: `<Feature>Page` | `LoginPage`, `CheckoutPage` |

---

## 3. MANDATORY STRUCTURE FOR EVERY PAGE OBJECT

Every Page Object class must follow this exact structure in order:

```python
from playwright.sync_api import Page, Locator, expect
from pages.base_page import BasePage


class ExamplePage(BasePage):
    """
    Page Object for the Example page (/example).
    Covers: <list what user flows this page handles>
    """

    # --- Section 1: Page URL ---
    URL = "/example"

    # --- Section 2: Locators (as @property methods) ---

    @property
    def heading(self) -> Locator:
        return self.page.get_by_role("heading", name="Example Page")

    @property
    def submit_button(self) -> Locator:
        return self.page.get_by_role("button", name="Submit")

    # --- Section 3: Navigation ---

    def navigate(self) -> None:
        self.goto(self.URL)

    # --- Section 4: Actions (user behavior methods) ---

    def submit_form(self, name: str, email: str) -> None:
        self.fill(self.page.get_by_label("Full Name"), name)
        self.fill(self.page.get_by_label("Email"), email)
        self.click(self.submit_button)
```

---

## 4. LOCATOR DECLARATION RULES

### DO:
- Declare every locator as a `@property` method returning `Locator`
- Use `page.get_by_role()`, `page.get_by_label()`, `page.get_by_placeholder()` as primary strategies
- Use `page.locator('[data-testid="..."]')` when semantic locators are unavailable
- Use `page.locator("#stable-id")` for elements with stable, unique IDs
- Scope child locators using `.locator()` chained from a parent: `self.form_container.locator("input")`
- Add a one-line docstring when the locator's purpose is not obvious from its name

### DO NOT:
- Define locators as string constants at class level (strings are not lazy — they can't adapt)
- Define locators inside action methods — they become invisible to maintenance and AI inspection
- Define the same locator in more than one Page Object file
- Use XPath unless reviewed and approved
- Use CSS `:nth-child()` positional selectors
- Use text-based locators for text that changes across environments or locales

---

## 5. ACTION METHOD RULES

### DO:
- Name action methods as verb phrases describing user intent:
  - `login(username, password)` ✓
  - `add_item_to_cart(product_name)` ✓
  - `click_submit_button()` — acceptable only if the action is atomic and named intentionally
- Accept typed parameters (use Python type hints on all parameters and return types)
- Call `self.*` helper methods from `BasePage` — never call raw `page.*` methods in subclasses
- Return `self` from chainable actions to allow fluent call chaining

### DO NOT:
- Put `assert` or `expect` calls inside action methods — that is the test layer's job
- Perform navigation side effects silently — if an action triggers navigation, document it in the docstring
- Write methods longer than ~20 lines — split into private helpers (`_fill_personal_info(...)`)
- Accept `page` as a method parameter — `self.page` is always available via `BasePage`

---

## 6. COMPONENT OBJECTS (pages/components/)

Reusable UI components that appear on multiple pages (navigation bar, modal dialogs,
toast notifications, pagination) must be extracted into `pages/components/`.

```python
# pages/components/nav_bar.py
from playwright.sync_api import Page, Locator
from pages.base_page import BasePage

class NavBar(BasePage):
    """Shared navigation bar component present on all authenticated pages."""

    @property
    def user_menu(self) -> Locator:
        return self.page.get_by_role("button", name="User Menu")

    def open_user_menu(self) -> None:
        self.click(self.user_menu)
```

Compose components inside page objects via constructor injection:
```python
from pages.components.nav_bar import NavBar

class DashboardPage(BasePage):
    def __init__(self, page: Page):
        super().__init__(page)
        self.nav = NavBar(page)
```

---

## 7. BASE PAGE INHERITANCE RULES

- All Page Objects **must** inherit from `BasePage` — never from Playwright's `Page` directly
- `BasePage` wraps `page.*` calls with smart waits, retry logic, and logging
- When `BasePage` does not expose a method you need, add it to `BasePage` — do not call
  raw `page.*` in subclasses
- `BasePage.__init__` must always be called via `super().__init__(page)` — never omit it

---

## 8. AI AGENT GENERATION CHECKLIST

Before generating a Page Object, an AI agent must confirm:

- [ ] The HTML structure of the target page has been inspected (via DevTools or provided spec)
- [ ] The page URL relative path is known and set as `URL = "/path"`
- [ ] All locators are declared as `@property` returning `Locator`
- [ ] No raw `page.*` calls appear in the subclass body (only `self.*` from `BasePage`)
- [ ] No `assert` or `expect` statements inside any method
- [ ] Class name matches file name in PascalCase / snake_case respectively
- [ ] File is saved to `pages/` (or `pages/components/` if it's a shared component)
- [ ] `BasePage` is imported from `pages.base_page` (relative import path)

---

> Last reviewed: 2026-06-26
> Owner: QA Automation Team
