# PlaySight — Configuration & Execution Guide

This guide covers every capability in the framework: what it does, how to configure it, and the exact command to run it.

---

## Prerequisites

**One-time setup (run in your own terminal — not inside Claude Code):**

```bash
# 1. Create the virtual environment with Python 3.11
python3.11 -m venv .venv

# 2. Activate it
source .venv/bin/activate          # macOS/Linux
# .venv\Scripts\activate           # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Install Playwright browsers
playwright install chromium
# or for all browsers:
playwright install
```

---

## Quick Start — Run the Example E2E Tests

```bash
cd hybrid_playwright_framework
source .venv/bin/activate
pytest tests/test_purchase_flow.py -v
```

This runs all 8 saucedemo.com E2E tests with:
- Per-run screenshots in `logs_and_reports/screenshots/run_YYYY-MM-DD_HH-MM-SS/`
- Per-run videos in `logs_and_reports/videos/run_YYYY-MM-DD_HH-MM-SS/`
- HTML report at `logs_and_reports/report.html`

---

## Configuration File

All flags live in **`config/.env`**. Copy `.env.example` to `.env` and fill in your values.

```bash
cp config/.env.example config/.env
```

The framework reads `.env` automatically on startup — no export needed.

---

## Capability 1 — AI Self-Healing Locators

Automatically recovers from broken locators by asking GPT-4o-mini to suggest a replacement.

**Setup:**
1. Set `OPENAI_API_KEY=sk-...` in `config/.env`
2. Set `ENABLE_AI_HEALING=true` in `config/.env`

**Usage in tests:** Replace `page.locator(...)` calls through `BasePage` helpers.
When a locator fails, `AISelfHeal.heal(intent, page_html)` is called to propose a fix.

**Review pending heals:**
```bash
cat data/healing_log.json
```

---

## Capability 2 — Flakiness Tracker

Records pass/fail for every test run in SQLite. Surfaces tests with a failure rate above the threshold.

**Setup:** Already on by default (`FLAKINESS_TRACKING=true`).

**Tune the sensitivity:**
```ini
FLAKINESS_TRACKING=true
FLAKINESS_WINDOW=20       # how many recent runs to evaluate
FLAKINESS_THRESHOLD=0.15  # 15% failure rate = flagged as flaky
```

**Query the database:**
```python
from utils.flakiness_tracker import FlakinessTracker
tracker = FlakinessTracker()
print(tracker.get_flaky_tests())     # list of flaky test IDs
print(tracker.get_stats("tests/test_purchase_flow.py::test_login_valid[chromium]"))
```

Flaky tests are also printed to the console at the end of every pytest session.

---

## Capability 3 — Visual Regression Testing

Perceptual hash diff (imagehash pHash) — compares screenshots to stored baselines.

**Setup:**
```ini
VISUAL_REGRESSION=true
VISUAL_DIFF_THRESHOLD=10   # Hamming distance 0-64; 10 ≈ <5% change
```

**First run:** Baselines are created automatically in `data/visual_baselines/`.

**Subsequent runs:** Any diff > threshold fails the test with a side-by-side diff image saved to `logs_and_reports/visual_diffs/run_*/`.

**Update baselines (after intentional UI change):**
```python
from utils.visual_regression import VisualRegression
vr = VisualRegression()
# inside a test with a `page` fixture:
vr.update_baseline(page, "login_page")
```

**Use in a test:**
```python
def test_login_ui(page, check_visual):
    page.goto("https://www.saucedemo.com")
    check_visual("login_page")   # creates baseline on first run, diffs thereafter
```

---

## Capability 4 — Accessibility Audit (WCAG 2.1 AA)

Injects axe-core via CDN and runs an accessibility audit after every test.

**Setup:**
```ini
ACCESSIBILITY_AUDIT=true
ACCESSIBILITY_FAIL_ON=critical,serious   # impact levels that fail the test
```

**Reports:** JSON reports per test at `logs_and_reports/a11y/run_*/`.

**Use as a fixture in a specific test:**
```python
def test_login_a11y(page, check_a11y):
    page.goto("https://www.saucedemo.com")
    check_a11y("login_page")   # raises AssertionError if violations found
```

**Use for all tests automatically:** Set `ACCESSIBILITY_AUDIT=true` — the hook in `conftest.py` audits every UI test page.

**Offline fallback:** Download axe-core and save to `utils/axe.min.js` — the auditor uses it if CDN is unavailable.
```bash
curl -o hybrid_playwright_framework/utils/axe.min.js \
  https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.9.1/axe.min.js
```

---

## Capability 5 — Natural Language Test Generator

Converts a plain-English scenario into a Page Object stub + pytest function.

**Setup:** Requires `OPENAI_API_KEY` in `config/.env`.

**Run:**
```bash
cd hybrid_playwright_framework
python tools/generate_test.py "User cannot checkout with an empty cart"
python tools/generate_test.py "Login fails with SQL injection" --page login --output tests/test_security.py
python tools/generate_test.py "Add item to cart and verify total" --page inventory --feature cart
```

Output is written to `pages/` and `tests/` — review and adjust before committing.

---

## Capability 6 — Playwright Codegen → POM Converter

Converts a raw `playwright codegen` recording into the framework's Page Object pattern.

**Setup:** Requires `OPENAI_API_KEY` in `config/.env`.

**Step 1 — Record with Playwright:**
```bash
playwright codegen https://www.saucedemo.com -o recorded.py
```

**Step 2 — Convert:**
```bash
python tools/codegen_converter.py recorded.py --page login
python tools/codegen_converter.py recorded.py --page checkout --with-test
```

Output: `pages/login_page.py` + (optionally) `tests/test_login.py`.

---

## Capability 7 — Accessibility Audit

See Capability 4 above — they share the same `utils/accessibility.py` implementation.

---

## Capability 8 — API Contract Tests

REST API contract validation using Pydantic v2 schemas.

**Setup:** No API key needed for the demo target (restful-booker.herokuapp.com).
To target your own API, set `API_BASE_URL` in `tests/api/conftest.py`.

**Run:**
```bash
# All API tests
pytest tests/api/ -v -m api

# Only smoke
pytest tests/api/ -v -m smoke

# Only regression
pytest tests/api/ -v -m regression
```

**Add a new contract:** Define a Pydantic model in `test_api_contracts.py` and write a test that parses the response through it — Pydantic raises `ValidationError` on schema mismatch.

---

## Capability 9 — Change-Impact Test Prioritisation

Reads `git diff` to identify which tests are affected by the current change. Outputs a `pytest -k` filter.

**Setup:** Requires the project to be a git repository.

**Run on CI (or locally):**
```bash
# Diff vs previous commit
python tools/prioritize_tests.py

# Diff vs main branch
python tools/prioritize_tests.py --base main

# Print the -k expression only (for CI scripting)
python tools/prioritize_tests.py --list --dry-run
```

**Wire into CI:**
```yaml
# GitHub Actions example
- name: Prioritise tests
  run: |
    FILTER=$(python tools/prioritize_tests.py --list --dry-run)
    if [ "$FILTER" = "ALL" ]; then
      pytest
    else
      pytest -k "$FILTER"
    fi
```

**Extend the map:** Edit `_PAGE_TO_TEST_MAP` in `tools/prioritize_tests.py` to add new page→test mappings as you grow the suite.

---

## Capability 10 — Slack / Teams Notifications

Sends a Block Kit summary card to Slack (or a MessageCard to Teams) at the end of every run.

**Setup:**
```ini
SLACK_NOTIFICATIONS=true
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T.../B.../...
SLACK_CHANNEL=#qa-alerts

# Optional Teams:
TEAMS_WEBHOOK_URL=https://outlook.office.com/webhook/...
```

**How to get a Slack Webhook:**
1. Go to [api.slack.com/apps](https://api.slack.com/apps) → Create New App → Incoming Webhooks.
2. Enable Incoming Webhooks, click "Add New Webhook to Workspace".
3. Copy the Webhook URL into `SLACK_WEBHOOK_URL`.

The notification is sent automatically at the end of every `pytest` session when the flag is on.

---

## Capability 11 — Web Vitals Performance Metrics

Collects LCP, CLS, TTFB, DOM Content Loaded, and full load time per test via a PerformanceObserver init script.

**Setup:**
```ini
PERFORMANCE_METRICS=true
PERFORMANCE_LCP_BUDGET_MS=2500   # warn if LCP exceeds this
PERFORMANCE_LOAD_BUDGET_MS=5000  # warn if page load exceeds this
```

Metrics are stored in `logs_and_reports/performance.db` (SQLite).

**Query trends:**
```python
from utils.performance import PerformanceCollector
pc = PerformanceCollector()
print(pc.get_trends("tests/test_purchase_flow.py::test_full_purchase_flow[chromium]"))
print(pc.get_run_summary("2026-06-26_14-07-55"))
```

---

## Capability 12 — LLM Test Quality Auditor

Scores test files on assertion quality, independence, and coverage. Grades A–F.

**Setup:** Requires `OPENAI_API_KEY` in `config/.env`.

**Run:**
```bash
# Audit all tests
python tools/audit_tests.py

# Audit a single file
python tools/audit_tests.py --file tests/test_purchase_flow.py

# Save report
python tools/audit_tests.py --output logs_and_reports/audit_report.json
```

The report JSON contains per-test findings with severity levels and actionable suggestions.

---

## Capability 13 — Automatic Test Repair on CI Failure

Reads the pytest failure log, identifies the failing file from the traceback, and proposes a targeted fix as a unified diff.

**Setup:** Requires `OPENAI_API_KEY` in `config/.env`.

**Step 1 — Capture the log:**
```bash
pytest tests/ 2>&1 | tee logs_and_reports/pytest.log
```

**Step 2 — Generate a fix:**
```bash
python tools/repair_test.py
# or point at a specific log
python tools/repair_test.py --log logs_and_reports/pytest.log

# Auto-apply with patch (review the diff first)
python tools/repair_test.py --apply

# Pass the error directly
python tools/repair_test.py \
  --error "TimeoutError waiting for [data-test='checkout-button']" \
  --file pages/cart_page.py
```

Always review the proposed diff before applying.

---

## Capability 14 — Synthetic Test Data Generator

Generates structured test data records from a plain-English description.

**Setup:** Requires `OPENAI_API_KEY` in `config/.env`.

**Run:**
```bash
# 5 valid checkout customer records
python tools/generate_data.py "checkout customer with valid credit card" --count 5

# Save to a file
python tools/generate_data.py "invalid login credential pairs" --count 3 \
  --output data/login_negative_data.json

# Edge/boundary cases
python tools/generate_data.py "product price boundary values" --edge-cases --count 5

# With a schema hint
python tools/generate_data.py "user profile" --count 3 \
  --schema '{"name":"string","email":"string","age":"int"}'
```

Generated data can be loaded in tests via the `e2e_data` fixture or `load_test_data("my_file.json")`.

---

## AI Summary in HTML Report

At the end of every run, an LLM executive summary is injected into the HTML report.

**Setup:**
```ini
AI_SUMMARY=true
OPENAI_API_KEY=sk-...
```

Run your tests as normal:
```bash
pytest tests/ -v
```

Open `logs_and_reports/report.html` — the AI summary appears at the top.

---

## Running Specific Test Subsets

```bash
# Smoke tests only
pytest -m smoke -v

# Regression tests
pytest -m regression -v

# UI tests (exclude API)
pytest tests/test_purchase_flow.py -v

# API contract tests only
pytest tests/api/ -v

# Run in parallel (4 workers)
pytest -n 4 tests/

# With Allure report
pytest --alluredir=logs_and_reports/allure-results
allure serve logs_and_reports/allure-results
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ModuleNotFoundError: greenlet` or `pydantic-core` | Python 3.14 not supported. Use Python 3.11: `python3.11 -m venv .venv` |
| `PermissionError: [Errno 1] Operation not permitted` on rerunfailures | Already fixed: `-p no:rerunfailures` in `pytest.ini` |
| Browser fails to launch in sandboxed terminal | Run tests from your own terminal, not inside Claude Code |
| `pip install` gets a 403/proxy error | Open a fresh terminal without `HTTP_PROXY` set, then run pip |
| Screenshot warning `Target page ... has been closed` | Fixed: screenshots now taken in `makereport(when="call")` hook |
| axe-core `net::ERR_NAME_NOT_RESOLVED` | Offline? Save `axe.min.js` locally: `curl -o utils/axe.min.js <CDN URL>` |
| OpenAI calls fail | Check `OPENAI_API_KEY` in `config/.env`. Run: `python -c "from utils.llm_client import LLMClient; print(LLMClient().available)"` |

---

## Directory Structure Reference

```
hybrid_playwright_framework/
├── config/
│   ├── .env                    ← your secrets (gitignored)
│   ├── .env.example            ← template for new developers
│   └── config.py               ← all Config.* constants
├── pages/                      ← Page Object classes
│   ├── base_page.py
│   ├── login_page.py
│   ├── inventory_page.py
│   ├── cart_page.py
│   └── checkout_page.py
├── tests/
│   ├── conftest.py             ← all fixtures + capability hooks
│   ├── test_purchase_flow.py   ← E2E saucedemo.com tests
│   └── api/
│       ├── conftest.py         ← API session/auth fixtures
│       └── test_api_contracts.py ← Pydantic contract tests
├── utils/
│   ├── llm_client.py           ← OpenAI wrapper
│   ├── flakiness_tracker.py    ← SQLite flakiness DB
│   ├── ai_self_heal.py         ← self-healing locators
│   ├── visual_regression.py    ← perceptual hash diff
│   ├── accessibility.py        ← axe-core auditor
│   ├── performance.py          ← Web Vitals collector
│   ├── slack_notifier.py       ← Slack + Teams notifications
│   ├── ai_summary.py           ← LLM test run summary
│   ├── test_data_generator.py  ← synthetic data gen
│   └── llm_judge.py            ← test quality auditor
├── tools/
│   ├── generate_test.py        ← NL → test generator (Cap #5)
│   ├── codegen_converter.py    ← codegen → POM (Cap #6)
│   ├── prioritize_tests.py     ← change-impact prioritiser (Cap #9)
│   ├── generate_data.py        ← data generator CLI (Cap #14)
│   ├── audit_tests.py          ← LLM judge CLI (Cap #12)
│   └── repair_test.py          ← auto repair CLI (Cap #13)
├── data/
│   ├── e2e_test_data.json      ← E2E test data
│   └── visual_baselines/       ← auto-created baseline PNGs
├── logs_and_reports/
│   ├── report.html             ← pytest-html report
│   ├── screenshots/run_*/      ← per-run screenshots
│   ├── videos/run_*/           ← per-run videos
│   ├── a11y/run_*/             ← axe-core JSON reports
│   ├── visual_diffs/run_*/     ← diff images
│   ├── flakiness.db            ← flakiness SQLite database
│   └── performance.db          ← performance SQLite database
├── requirements.txt
└── pytest.ini
```
