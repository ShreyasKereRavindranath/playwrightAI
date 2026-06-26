# PlaySight — Global Step-by-Step Setup & Contribution Guide

> **Scope:** Every engineer and AI agent onboarding into PlaySight must follow these phases in order.
> Do not skip phases or run commands out of sequence.

---

## PHASE 0 — Prerequisites

Before touching any framework code, confirm the following are installed on your machine:

| Tool | Minimum Version | Check Command |
|------|----------------|---------------|
| Python | 3.11+ | `python --version` |
| pip | 23.0+ | `pip --version` |
| Git | 2.40+ | `git --version` |
| Node.js (for Playwright system deps) | 18+ | `node --version` |

```bash
# Confirm Python points to 3.11+
python3 --version
```

---

## PHASE 1 — Environment Setup

### Step 1.1 — Clone and Navigate
```bash
git clone <your-repo-url>
cd hybrid_playwright_framework
```

### Step 1.2 — Create and Activate a Virtual Environment
```bash
# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate

# Windows (PowerShell)
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

> **Rule:** Always activate the virtual environment before running any pip or pytest command.
> Never install packages globally.

### Step 1.3 — Install Python Dependencies
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### Step 1.4 — Install Playwright System Binaries
```bash
# Download Chromium, Firefox, and WebKit browser binaries
playwright install

# Install only specific browser (faster in CI)
playwright install chromium

# Install system OS-level dependencies (required on Linux CI runners)
playwright install-deps
```

### Step 1.5 — Configure Environment Variables
```bash
# Copy the example file and fill in real values
cp config/.env.example config/.env
```

Open `config/.env` and fill in:
- `BASE_URL` — the root URL of the application under test
- `BROWSER` — `chromium`, `firefox`, or `webkit`
- `HEADLESS` — `true` (CI) or `false` (local debugging)
- `SLOW_MO` — milliseconds to slow Playwright actions (0 for CI, ~100 for debug)
- `DEFAULT_TIMEOUT` — default element wait timeout in ms (recommended: `30000`)
- Any credentials using the `TEST_USER_*` prefix convention

> **Security Rule:** NEVER commit `config/.env`. It is in `.gitignore`.
> Only commit `config/.env.example` with all secret values replaced by descriptive placeholders.

---

## PHASE 2 — Understanding the Architecture

### Directory Responsibilities

```
hybrid_playwright_framework/
├── config/          → Environment config loader, constants, browser settings
├── pages/           → Page Object Model classes (one file per application page/section)
├── tests/           → Pytest test cases + conftest.py fixtures
├── utils/           → Reusable helpers: logger, API client, screenshot util, AI self-healing
├── data/            → JSON / CSV test data files (parameterized inputs)
└── logs_and_reports/→ Auto-generated: HTML reports, screenshots, Playwright traces
```

### Design Layers (Hybrid Model)
```
Test Layer (tests/)
    ↓  calls
Page Object Layer (pages/)
    ↓  inherits
Base Page Layer (pages/base_page.py)
    ↓  wraps
Playwright Page API
```

Data is injected from `data/` via pytest fixtures defined in `tests/conftest.py`.
Configuration flows from `config/.env` → `config/config.py` → anywhere that imports it.

---

## PHASE 3 — Building Page Objects

### Step 3.1 — Read the Rules First
Read `pages/PAGES_GUIDELINES.md` completely before creating any Page Object.

### Step 3.2 — Create a New Page Object
1. Create `pages/<feature_name>_page.py` (e.g., `pages/login_page.py`)
2. Import and extend `BasePage` from `pages/base_page.py`
3. Define all locators as class-level `@property` methods returning `Locator` objects
4. Define interaction methods (e.g., `login(username, password)`) that call `self.` helper methods
5. Never put `assert` or `expect` in Page Object methods

### Step 3.3 — Example Skeleton
```python
from pages.base_page import BasePage

class LoginPage(BasePage):
    URL = "/login"

    @property
    def username_input(self):
        return self.page.get_by_label("Username")

    @property
    def password_input(self):
        return self.page.get_by_label("Password")

    @property
    def submit_button(self):
        return self.page.get_by_role("button", name="Sign In")

    def navigate(self):
        self.goto(self.URL)

    def login(self, username: str, password: str):
        self.fill(self.username_input, username)
        self.fill(self.password_input, password)
        self.click(self.submit_button)
```

---

## PHASE 4 — Writing Tests

### Step 4.1 — Read the Rules First
Read `tests/TEST_GUIDELINES.md` completely before writing any test.

### Step 4.2 — Fixture Setup
All shared fixtures live in `tests/conftest.py`. The framework provides:
- `page` — Playwright page fixture (from pytest-playwright, browser scope configurable)
- `login_page`, `home_page`, etc. — Page Object fixtures
- `test_data` — loads JSON test data from `data/`
- `authenticated_page` — a page fixture that starts in a logged-in state

### Step 4.3 — Test Naming Convention
```
test_<feature>_<scenario>_<expected_outcome>.py
Example: test_login_valid_credentials_redirects_to_dashboard.py
```

Function names follow the same pattern:
```python
def test_login_with_valid_credentials_redirects_to_dashboard(login_page, test_data):
    ...
```

### Step 4.4 — Running Tests

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run a specific test file
pytest tests/test_login.py -v

# Run tests matching a keyword
pytest -k "login" -v

# Run tests in parallel (N workers)
pytest -n 4

# Run with specific browser
pytest --browser firefox

# Run headed (visible browser window)
pytest --headed

# Generate HTML report
pytest --html=logs_and_reports/report.html --self-contained-html

# Run with Playwright trace (for debugging failures)
pytest --tracing=on
# Traces saved to: test-results/
# View with: playwright show-trace test-results/<trace.zip>

# Run only smoke tests
pytest -m smoke

# Run only regression tests
pytest -m regression
```

---

## PHASE 5 — AI Self-Healing Locators (Optional Module)

The framework includes a stub in `utils/ai_self_heal.py` for AI-assisted locator recovery.
When enabled, this module:
1. Catches `TimeoutError` from a failed locator
2. Calls an LLM with the page HTML snapshot and the original locator intent
3. Returns a candidate replacement locator
4. Logs the healed locator for human review

### Enabling AI Self-Healing
```bash
# Add to config/.env
ENABLE_AI_HEALING=true
OPENAI_API_KEY=sk-...
AI_HEALING_MODEL=gpt-4o-mini
```

> **Warning:** AI healing is a diagnostic safety net, not a substitute for proper locator
> maintenance. Review all healed locators before the next test cycle and update the
> Page Object accordingly.

---

## PHASE 6 — CI/CD Integration

### GitHub Actions Example
```yaml
name: Playwright Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          playwright install --with-deps chromium
      - name: Run tests
        run: pytest -n auto --html=logs_and_reports/report.html --self-contained-html
        env:
          BASE_URL: ${{ secrets.BASE_URL }}
          TEST_USER_EMAIL: ${{ secrets.TEST_USER_EMAIL }}
          TEST_USER_PASSWORD: ${{ secrets.TEST_USER_PASSWORD }}
      - name: Upload reports
        uses: actions/upload-artifact@v4
        if: always()
        with:
          name: test-reports
          path: logs_and_reports/
```

---

## PHASE 7 — Adding New Features (AI Agent Checklist)

When an AI agent is instructed to add a new feature to this framework, it must:

- [ ] Read `DO_NOT_DO.md` before generating any code
- [ ] Read the relevant `*_GUIDELINES.md` for the target directory
- [ ] Check `requirements.txt` before adding any import — add package if missing
- [ ] Extend `BasePage` for any new Page Object, never raw `Page`
- [ ] Define locators only inside Page Object `@property` methods
- [ ] Place all fixtures in `tests/conftest.py`, not inside individual test files
- [ ] Add test data to `data/` as JSON, not hardcoded in test functions
- [ ] Add appropriate pytest markers (`@pytest.mark.smoke`, `@pytest.mark.regression`)
- [ ] Verify the test runs in isolation with `pytest tests/path/to/test_file.py`
- [ ] Run `pytest --co` (collect-only) to ensure no import errors before full run

---

> Last reviewed: 2026-06-26
> Framework: PlaySight
> Owner: QA Automation Team
