# Shreyzen — Configuration & Execution Guide

This guide covers every capability in the framework: what it does, how to configure it, and the exact command to run it.

---

## Prerequisites

> **Shortcut:** `./run.sh` (from `Shreyzen/`) does all of the below for you —
> creates the venv, installs dependencies on first launch, and opens the runner.
> The manual steps are only needed if you want to run pytest directly.

**One-time setup (run in your own terminal — not inside Claude Code):**

```bash
# 1. Create the virtual environment with Python 3.11
python3.11 -m venv .venv

# 2. Activate it
source .venv/bin/activate          # macOS/Linux
# .venv\Scripts\activate           # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. (Optional) Install Playwright browsers
#    The framework auto-installs the required browser on first run, so this
#    step is optional. Run it manually only if you want to pre-warm the cache
#    or work fully offline:
playwright install chromium
# or for all browsers:
playwright install
```

> **Browsers install themselves.** You do **not** need to run `playwright install`
> by hand — on the first `pytest` run the framework detects a missing browser
> binary and downloads it automatically. See
> [Automatic Browser Provisioning](#automatic-browser-provisioning) below.

---

## Quick Start — Run the Example E2E Tests

```bash
cd hybrid_playwright_framework
source .venv/bin/activate
pytest tests/web/ -v
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

## Automatic Browser Provisioning

The framework installs the Playwright browser binary for you — there's no need to
run `playwright install` manually. At the start of every `pytest` session, a
`pytest_configure` hook (`tests/conftest.py` → `utils/browser_bootstrap.py`)
checks whether the browser named by `BROWSER` is present, and downloads it only
if it's missing.

- **First run / fresh machine:** the missing browser is downloaded once, then the
  run proceeds. No manual step.
- **Normal runs:** the binary is detected as present — the check is a cheap
  filesystem stat and adds no measurable overhead.
- **Branded channels** (`BROWSER=chrome` or `msedge`) are skipped, since those
  reuse the OS-installed browser.

**Flags (in `config/.env`):**
```ini
AUTO_INSTALL_BROWSERS=true    # master switch (default true). Set false to opt out.
INSTALL_BROWSER_DEPS=false    # also run `--with-deps` for OS libraries
                              # (Linux/CI only; requires root).
```

> **CI tip:** on a fresh Linux runner set `INSTALL_BROWSER_DEPS=true` so the
> system libraries Chromium needs are pulled in alongside the browser binary.
>
> **Offline / air-gapped:** set `AUTO_INSTALL_BROWSERS=false` and pre-install the
> browser once with `playwright install chromium`.

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
print(tracker.get_stats("tests/web/test_purchase_flow.py::test_login_valid[chromium]"))
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
python tools/generate_test.py "Login fails with SQL injection" --page login --output tests/web/test_security.py
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

Output: `pages/login_page.py` + (optionally) `tests/web/test_login.py`.

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
print(pc.get_trends("tests/web/test_purchase_flow.py::test_full_purchase_flow[chromium]"))
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
python tools/audit_tests.py --file tests/web/test_purchase_flow.py

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

## Capability 15 — Shreyzen Studio (unified runner)

A Cypress-style local UI for **functional + load tests, analytics, compare, and
LLM config**. Light/dark **theme toggle**, a live **top-bar status strip**
(green/red dots + tooltips for mock server, active runs, LLM, each AI agent —
planner · generator · healer — and background processes), **auto-download toasts**
(what was fetched + local disk used), inline **error toasts**, and **graceful
shutdown** (Ctrl-C stops everything it started). Formerly "Load Runner";
`tools/load_runner.py` remains a compatible alias.

**Launch:**
```bash
python tools/studio.py serve      # → http://127.0.0.1:8770   (or ./run.sh)
```

**Functional mode** — run the pytest suites from the browser:
- Pick any **api / web / mobile** tests from a **collapsible tree** — **collapsed
  by default** (whole layer, a file, or individual tests).
- Set the **target per run**: BASE_URL for web/mobile; API target = mock (auto-start) /
  public (restful-booker) / custom URL; mobile **device** and **markers** as
  dropdowns; browser; headless.
- Watch live pass/fail counts + per-test results; open or **download** HTML +
  JUnit + JSON + Allure under `logs_and_reports/functional_runs/<run_id>/`. Recent
  runs are labelled by test type (`WEB_` · `API_` · `MOBILE_`), and every report is
  **titled by its test type** and embeds who ran it, browser, OS, a timezone-aware
  timestamp, and the active LLM (or a deterministic offline fallback when none is
  configured/active).

**Load mode** — Locust load/soak/spike/stress + non-destructive security probes:
- **6 load profiles:** smoke, load, stress, spike, soak, breakpoint (+ Custom).
- **3 scenarios:** API CRUD (full create/read/update/patch/delete), a full
  user journey, and security probes.
- **Custom VU control:** run any scenario at any scale via the slider / fields.
- **Reports on every run:** HTML + JUnit + JSON + Allure under
  `logs_and_reports/load_runs/<run_id>/`. The HTML report is **titled by load type**
  (`LOAD_<scenario>_<profile>`) and carries a user/system/LLM context panel.
- **Auto-target:** starts the bundled mock API (`:8765`) automatically.

**Headless (CI):**
```bash
python tools/studio.py run --scenario crud --profile smoke
python tools/studio.py run --scenario journey --profile custom --users 200 --duration 300
```
Exits non-zero when the profile's thresholds are breached, so it gates CI.

**CI workflows** live in `.github/workflows/` at the **repo root** (one level above
`Shreyzen/`); each job runs with `working-directory: Shreyzen`. They are:
`pr-checks.yml` (PR gate), `load-manual.yml` (on-demand, any scale),
`nightly-soak.yml` (scheduled soak).

📖 **Full guide:** [LOAD_TESTING.md](LOAD_TESTING.md)

---

## Capability 16 — Analytics Dashboard

A read-only, PowerBI-style dashboard over your run history. It's **merged into
Shreyzen Studio** (the *Analytics* and *Compare Runs* tabs) and also runs
standalone:

**Launch:**
```bash
python tools/dashboard.py               # → http://127.0.0.1:8766
```

Pages: **Overview** (pass-rate & run trends), **Flakiness**, **Performance**
(Web Vitals), **Run History**, **API Contracts**, and **Load Tests** — the last
shows every load run's throughput/p95 with one-click links to its HTML, JSON,
JUnit, and **Allure** reports (Allure is generated on demand; needs the `allure`
CLI). Data is read from `logs_and_reports/flakiness.db`, `runs/*.json`, and
`load_runs/*/results.json` — no configuration required.

---

## Capability 17 — Multi-Provider LLM

Every AI feature runs through one provider-neutral layer, so you can use
**OpenAI, Anthropic Claude, Google Gemini, Ollama (local), LM Studio (local), or
any OpenAI-compatible endpoint** — chosen from the UI or CLI, remembered, and
switchable anytime. Local providers need no API key; Ollama auto-installs,
auto-starts, and auto-pulls its model on first use.

**Pick a provider:**
```bash
python tools/llm_config.py list            # providers + live status
python tools/llm_config.py select ollama   # switch (remembered)
# or in the runner UI:  python tools/studio.py serve → "AI Provider" tab
# or set AI_PROVIDER=<name> in config/.env
```

Only the selected provider's credentials are required (`OPENAI_API_KEY`,
`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, …). Unsupported features (e.g. embeddings
on Anthropic, temperature on current Claude models) are disabled gracefully
rather than failing.

📖 **Full guide:** [LLM_PROVIDERS.md](LLM_PROVIDERS.md)

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

# Web UI tests (desktop) — by folder or marker
pytest tests/web/ -v
pytest -m web -v

# Mobile UI tests (device emulation)
pytest tests/mobile/ -v
pytest -m mobile -v
# pick a device:
MOBILE_DEVICE="iPhone 13" pytest tests/mobile/ -v

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
| Auto browser-install fails (proxy/offline) | Set `AUTO_INSTALL_BROWSERS=false`, then run `playwright install chromium` manually in a clean terminal |
| Chromium missing OS libs on Linux/CI | Set `INSTALL_BROWSER_DEPS=true` (runs `--with-deps`; needs root) |
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
│   ├── web/                    ← browser UI tests (desktop)
│   │   ├── test_purchase_flow.py   ← E2E saucedemo.com tests
│   │   └── generated/          ← Generator agent output (web tests)
│   ├── mobile/                 ← browser UI tests (mobile emulation)
│   │   ├── conftest.py         ← mobile device context override
│   │   └── test_mobile_shopping.py
│   └── api/
│       ├── conftest.py         ← API session/auth fixtures
│       └── test_api_contracts.py ← Pydantic contract tests
├── utils/
│   ├── llm_client.py           ← OpenAI wrapper
│   ├── browser_bootstrap.py    ← auto-installs Playwright browsers
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
│   ├── dashboard.py            ← analytics dashboard UI (:8766, Cap #16)
│   ├── load_runner.py          ← test runner UI + CLI (:8770, Cap #15)
│   ├── functional_engine.py    ← functional (pytest) test discovery + runner
│   ├── mock_api_server.py      ← local mock API (full CRUD target)
│   ├── generate_test.py        ← NL → test generator (Cap #5)
│   ├── codegen_converter.py    ← codegen → POM (Cap #6)
│   ├── prioritize_tests.py     ← change-impact prioritiser (Cap #9)
│   ├── generate_data.py        ← data generator CLI (Cap #14)
│   ├── audit_tests.py          ← LLM judge CLI (Cap #12)
│   ├── repair_test.py          ← auto repair CLI (Cap #13)
│   └── agents_cli.py           ← plan/generate/heal agent CLI
├── load/                       ← load testing (Cap #15)
│   ├── catalog.py              ← scenarios, profiles, load-plan math
│   ├── shapes.py               ← Locust LoadTestShape (6 profiles)
│   ├── locustfile.py           ← CRUD / journey / security scenarios
│   ├── reporting.py            ← JSON + JUnit + Allure writers
│   └── engine.py               ← launch Locust, stream stats, reports
├── agents/                     ← planner / generator / healer agents
├── run.sh                      ← one-command launcher (venv + deps + serve)
├── data/
│   ├── e2e_test_data.json      ← E2E test data
│   └── visual_baselines/       ← auto-created baseline PNGs
├── logs_and_reports/
│   ├── report.html             ← pytest-html report
│   ├── screenshots/run_*/      ← per-run screenshots
│   ├── videos/run_*/           ← per-run videos
│   ├── a11y/run_*/             ← axe-core JSON reports
│   ├── visual_diffs/run_*/     ← diff images
│   ├── load_runs/<id>/         ← per load-run html/junit/json/allure
│   ├── functional_runs/<id>/   ← per functional-run html/junit/json/allure
│   ├── flakiness.db            ← flakiness SQLite database
│   └── performance.db          ← performance SQLite database
├── requirements.txt
└── pytest.ini
```

> **CI lives one level up**, at the repository root: `../.github/workflows/`
> (`PlaywrightFramework/.github/workflows/`). Each job sets
> `working-directory: Shreyzen` so the steps run against this framework.
