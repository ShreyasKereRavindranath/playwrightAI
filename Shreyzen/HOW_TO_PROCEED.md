# Shreyzen — Global Step-by-Step Setup & Contribution Guide

> **Scope:** Every engineer and AI agent onboarding into Shreyzen must follow these phases in order.
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

> **Fastest path:** from the `Shreyzen/` directory, run **`./run.sh`**. It creates
> the virtualenv, installs dependencies on first launch, and opens the Test Runner
> UI at http://127.0.0.1:8770 — no other setup needed. The manual steps below are
> for when you want to run pytest directly or understand each piece.

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

### Step 1.4 — Install Playwright System Binaries (optional)
> **Auto-install:** The framework downloads the required browser automatically on
> the first `pytest` run (see `utils/browser_bootstrap.py` and the
> `AUTO_INSTALL_BROWSERS` flag). The commands below are only needed if you want to
> pre-warm the cache, install extra browsers, or work fully offline.
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
├── tests/           → Pytest tests, split by layer:
│   ├── api/         →   API-layer tests (no browser) — @pytest.mark.api
│   ├── web/         →   Browser UI tests, desktop — @pytest.mark.web
│   └── mobile/      →   Browser UI tests, device emulation — @pytest.mark.mobile
├── load/            → Locust load-test scenarios, profiles, and run engine
├── utils/           → Reusable helpers: logger, API client, browser bootstrap, AI self-healing
├── data/            → JSON / CSV test data files (parameterized inputs)
├── tools/           → CLIs: dashboard (:8766), load_runner (:8770), generators, mock API
└── logs_and_reports/→ Auto-generated: HTML/JUnit/Allure reports, screenshots, traces, load runs
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
pytest tests/web/test_purchase_flow.py -v

# Run a whole layer (by folder or marker)
pytest tests/api -v          # or:  pytest -m api
pytest tests/web -v          # or:  pytest -m web
pytest tests/mobile -v       # or:  pytest -m mobile

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

### Step 4.5 — Test Runner (functional + load) & Dashboards

The runner is a Cypress-style UI with two modes: **Functional** (run api/web/mobile
pytest tests, pick the target per run) and **Load** (Locust profiles + VUs).

```bash
# One command — creates the venv (if needed), installs deps on first launch, opens the UI
./run.sh                                     # → http://127.0.0.1:8770
#   ↳ same as: python tools/studio.py serve

# Headless load run (CI-friendly; exits non-zero on threshold breach)
python tools/studio.py run --scenario crud --profile smoke

# Analytics dashboard: pass rates, flakiness, performance, and load-run history
python tools/dashboard.py                    # → http://127.0.0.1:8766
```

From the UI: **Functional Tests** tab → pick tests → set target → **Run**; or the
**Load** tab → pick a scenario + profile → set VUs → **Run**. The Playwright browser
auto-installs on the first web/mobile run and the mock API auto-starts on demand.

See **`LOAD_TESTING.md`** for the full load/performance/security guide.

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

### Jenkins (no UI required)

The Studio (`:8770`) and dashboard (`:8766`) UIs are **developer conveniences** —
CI never needs them. Everything runs headless via CLIs, so Jenkins (or any
runner) drives the framework with plain shell steps:

| Purpose | Command |
|---|---|
| Environment check | `python -m tools.doctor` |
| Functional tests | `pytest -n auto -m smoke --junitxml=logs_and_reports/junit.xml` |
| Impacted tests only | `python -m tools.impact_run --run --base origin/main` |
| Load gate | `python tools/studio.py run --scenario crud --profile smoke` |
| Selected-API perf | `python tools/studio.py run --scenario api_select --endpoints create,read --profile stress` |
| Regression gate | `python -m tools.check_regressions --gate` |
| AI eval gate | `python -m tools.eval --gate` |
| LLM cost/usage report | `python -m tools.llm_usage` |

A ready-to-use declarative pipeline lives at the **repo root**:
[`../Jenkinsfile`](../Jenkinsfile). It creates a per-build virtualenv, runs the
API smoke, a load-smoke gate, web + mobile UI (in parallel), and a regression
gate, then publishes JUnit + archives all reports.

**Setup on Jenkins:**
1. Create three *Secret text* credentials: `shreyzen-base-url`,
   `shreyzen-test-user-email`, `shreyzen-test-user-password` (the `Jenkinsfile`
   binds these via `credentials(...)`).
2. New Item → *Pipeline* → *Pipeline script from SCM* → point at your repo; the
   `Jenkinsfile` is at the repository root.
3. (Optional) Install the **HTML Publisher** plugin and uncomment the
   `publishHTML` block to surface `report.html` / `extent_report.html` inline.
4. Ensure agents have Python 3.11+; Chromium OS libs are pulled by
   `playwright install --with-deps chromium` (needs root, or bake into the agent image).

Reports land under `Shreyzen/logs_and_reports/` (HTML, JUnit, JSON, Allure, and
the Extent report when `EXTENT_REPORT=true`) and are archived by the pipeline's
`post` block.

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
> Framework: Shreyzen
> Owner: QA Automation Team
