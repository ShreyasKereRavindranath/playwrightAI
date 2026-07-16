# 🎭 PlaySight

**AI-Powered Hybrid Playwright Automation Framework**

A production-ready test automation framework built with Python, Pytest, and Playwright — enhanced with 15 AI and modern-technology capabilities to help QA teams ship faster with greater confidence.

---

## What is PlaySight?

PlaySight is a hybrid automation framework that combines:
- **Page Object Model (POM)** for maintainability
- **Data-Driven Testing** for parametrization
- **Behavior-Driven structure** for readability
- **AI integrations** for self-healing, test generation, visual regression, and intelligent reporting

It targets both **UI (browser)** and **API (REST contract)** layers from a single unified framework.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11 |
| Test Runner | Pytest 8.x |
| Browser Automation | Playwright 1.49 |
| AI / LLM | OpenAI GPT-4o-mini |
| API Testing | Requests + Pydantic v2 |
| Visual Regression | Pillow + imagehash (pHash) |
| Accessibility | axe-core (WCAG 2.1 AA) |
| Performance | Web Vitals via PerformanceObserver |
| Reporting | pytest-html + Allure |
| Notifications | Slack Block Kit + Microsoft Teams |
| Dashboard | FastAPI + Chart.js |
| Data Storage | SQLite (stdlib) |

---

## 15 AI & Modern Capabilities

| # | Capability | How to Activate |
|---|---|---|
| 1 | **AI Self-Healing Locators** | `ENABLE_AI_HEALING=true` + `OPENAI_API_KEY` |
| 2 | **Flakiness Tracker** | On by default (`FLAKINESS_TRACKING=true`) |
| 3 | **Visual Regression Testing** | `VISUAL_REGRESSION=true` |
| 4 | **Accessibility Audit (WCAG 2.1 AA)** | `ACCESSIBILITY_AUDIT=true` |
| 5 | **Natural Language Test Generator** | `python tools/generate_test.py "scenario"` |
| 6 | **Playwright Codegen → POM Converter** | `python tools/codegen_converter.py recorded.py` |
| 7 | **API Contract Testing** | `pytest tests/api/ -v` |
| 8 | **Change-Impact Test Prioritisation** | `python tools/prioritize_tests.py` |
| 9 | **Web Vitals Performance Metrics** | `PERFORMANCE_METRICS=true` |
| 10 | **Slack / Teams Notifications** | `SLACK_NOTIFICATIONS=true` + webhook URL |
| 11 | **AI Executive Test Summary** | `AI_SUMMARY=true` + `OPENAI_API_KEY` |
| 12 | **LLM Test Quality Auditor** | `python tools/audit_tests.py` |
| 13 | **Auto Test Repair on CI Failure** | `python tools/repair_test.py` |
| 14 | **Synthetic Test Data Generator** | `python tools/generate_data.py "description"` |
| 15 | **Planner → Generator → Healer agent trio** | `python tools/agents_cli.py pipeline "feature"` |

---

## 🤖 AI Agents: Planner → Generator → Healer

A cohesive, composable trio that authors and maintains tests as an autonomous loop.
Each agent routes LLM calls through `utils/llm_client.py` **and ships a deterministic
offline fallback**, so the agents — and the bundled `examples/` — run end-to-end with
**no API key**. Set `OPENAI_API_KEY` in `config/.env` to switch to the richer LLM path;
the calling code is identical (each agent reports its mode: `OFFLINE` / `LLM`).

```
 feature ─▶ Planner ─▶ TestPlan ─▶ Generator ─▶ pytest tests ─▶ (run) ─▶ Healer ─▶ diagnosis + fix
```

| Agent | Input | Output |
|-------|-------|--------|
| **Planner** | plain-English feature / user story | structured `TestPlan` (scenarios, priorities, markers, steps) → JSON |
| **Generator** | one planned scenario | runnable pytest test wired to fixtures (+ Page Object stub if needed) |
| **Healer** | pytest log / error + source | failure diagnosis + concrete fix (unified diff or guidance) |

```bash
# Plan a feature into scenarios
python tools/agents_cli.py plan "User can add a product to the cart and check out"

# End-to-end: plan → generate runnable tests (into tests/generated/)
python tools/agents_cli.py pipeline "User can add a product to the cart and check out" --write
HEADLESS=true pytest tests/generated -v          # the generated tests pass against SauceDemo

# Heal a failing run (diagnose + propose fixes; add --apply to patch diffs)
python tools/agents_cli.py heal --log examples/sample_pytest_failure.log
```

Runnable demos live in `examples/` (see `examples/README.md`); code and contribution
rules live in `agents/` (see `agents/AGENTS_GUIDELINES.md`).

---

## Project Structure

```
PlaySight/
├── config/
│   ├── .env                    # Your secrets — gitignored, never commit
│   ├── .env.example            # Template for new developers
│   └── config.py               # All Config.* constants read from .env
│
├── pages/                      # Page Object Model classes
│   ├── base_page.py            # BasePage — all POM classes extend this
│   ├── login_page.py
│   ├── inventory_page.py
│   ├── cart_page.py
│   └── checkout_page.py
│
├── tests/
│   ├── conftest.py             # All fixtures + capability hooks (screenshots, video, flakiness, a11y, perf, AI summary, Slack)
│   ├── test_purchase_flow.py   # 8 E2E tests against saucedemo.com
│   └── api/
│       ├── conftest.py         # API session, auth token, payload fixtures
│       └── test_api_contracts.py  # 10 Pydantic v2 contract tests
│
├── utils/
│   ├── llm_client.py           # OpenAI wrapper (all AI calls go through here)
│   ├── flakiness_tracker.py    # SQLite flakiness recording + analysis
│   ├── ai_self_heal.py         # Self-healing locator recovery
│   ├── visual_regression.py    # Perceptual hash baseline diff
│   ├── accessibility.py        # axe-core injector + report writer
│   ├── performance.py          # Web Vitals collector (LCP, CLS, TTFB)
│   ├── slack_notifier.py       # Slack Block Kit + Teams MessageCard
│   ├── ai_summary.py           # LLM run summary + HTML report injection
│   ├── test_data_generator.py  # AI-powered synthetic data generation
│   └── llm_judge.py            # Test quality scoring (A–F grades)
│
├── agents/                     # 🤖 Planner → Generator → Healer trio
│   ├── base_agent.py           # Shared LLM routing + offline fallback
│   ├── schemas.py              # Pydantic contracts (TestPlan, Scenario, HealResult)
│   ├── planner.py              # Feature → TestPlan
│   ├── generator.py            # Scenario → pytest test
│   ├── healer.py               # Failure → diagnosis + fix
│   └── AGENTS_GUIDELINES.md    # Contribution rules for the agents
│
├── examples/                   # Runnable agent demos + sample inputs
│   ├── 01_planner_demo.py
│   ├── 02_generator_demo.py
│   ├── 03_healer_demo.py
│   ├── 04_full_pipeline_demo.py
│   └── sample_pytest_failure.log
│
├── tools/                      # CLI tools — run independently
│   ├── agents_cli.py           # Planner/Generator/Healer CLI (plan|generate|heal|pipeline)
│   ├── generate_test.py        # Natural language → pytest test
│   ├── codegen_converter.py    # Playwright recording → POM class
│   ├── prioritize_tests.py     # Git diff → pytest -k filter
│   ├── generate_data.py        # Synthetic test data CLI
│   ├── audit_tests.py          # LLM test quality audit CLI
│   ├── repair_test.py          # AI-powered test repair CLI
│   ├── mock_api_server.py      # Local REST API mock (port 8765)
│   └── dashboard.py            # Analytics dashboard server (port 8766)
│
├── data/
│   ├── e2e_test_data.json      # E2E test input data
│   ├── login_test_data.json    # Login scenario data
│   └── visual_baselines/       # Baseline screenshots for visual regression
│
├── logs_and_reports/           # Auto-generated — gitignored
│   ├── report.html             # pytest-html report
│   ├── screenshots/run_*/      # Per-run screenshots
│   ├── videos/run_*/           # Per-run video recordings
│   ├── a11y/run_*/             # axe-core JSON reports
│   ├── visual_diffs/run_*/     # Visual regression diff images
│   ├── runs/                   # JSON run summaries (feeds dashboard)
│   ├── flakiness.db            # SQLite: test outcomes + perf metrics
│   └── pytest.log              # Full debug log
│
├── HOW_TO_CONFIGURE.md         # Step-by-step guide for all 14 capabilities
├── HOW_TO_PROCEED.md           # Onboarding and contribution guide
├── pytest.ini                  # Pytest configuration
└── requirements.txt            # All Python dependencies
```

---

## Quick Start

### 1 — Prerequisites

```bash
# Python 3.11 required (3.12+ not supported due to wheel compatibility)
python3.11 --version
```

### 2 — Setup

```bash
# Clone and enter the project
cd PlaySight

# Create virtual environment
python3.11 -m venv .venv
source .venv/bin/activate        # macOS / Linux
# .venv\Scripts\activate         # Windows

# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers
playwright install chromium
```

### 3 — Configure

```bash
cp config/.env.example config/.env
# Edit config/.env — set BASE_URL, TEST_USER_EMAIL, TEST_USER_PASSWORD
```

### 4 — Run Tests

```bash
# All tests (UI + API)
pytest tests/ -v

# Smoke tests only (fastest)
pytest -m smoke -v

# UI tests only
pytest tests/test_purchase_flow.py -v

# API contract tests only
pytest tests/api/ -v

# Parallel execution
pytest -n 4 tests/
```

### 5 — Open the Report

```bash
open logs_and_reports/report.html
```

---

## Analytics Dashboard

Start the dashboard after running at least one test suite:

```bash
python tools/dashboard.py
# Open: http://localhost:8766
```

**Dashboard pages:**

| Page | What you see |
|---|---|
| Overview | Pass rate KPI, run trend chart, flaky test bar, browser split, slowest tests, recent runs table |
| Flakiness | Failure rate chart + risk classification (High / Medium / Low) |
| Performance | LCP, Load Time, TTFB, CLS trends per run + per-test breakdown |
| Run History | Complete run log with failed test names |
| API Contracts | Contract pass/fail results from latest run |

---

## Local Mock API Server

Run API contract tests without depending on an external service:

```bash
# Terminal 1 — start the mock server
python tools/mock_api_server.py
# Swagger docs: http://localhost:8765/docs

# Terminal 2 — run API tests against it
API_BASE_URL=http://localhost:8765 pytest tests/api/ -v
```

The mock server implements the full restful-booker contract: `POST /auth`, `GET/POST/PUT/PATCH/DELETE /booking`.

---

## AI Features Setup

All AI capabilities require an OpenAI API key:

```ini
# config/.env
OPENAI_API_KEY=sk-...
```

| Tool | Command |
|---|---|
| Generate a test from plain English | `python tools/generate_test.py "user cannot checkout with empty cart"` |
| Convert Playwright recording to POM | `python tools/codegen_converter.py recorded.py --page checkout` |
| Generate synthetic test data | `python tools/generate_data.py "valid checkout customers" --count 5` |
| Audit test quality (graded A–F) | `python tools/audit_tests.py` |
| Repair a failing test from log | `python tools/repair_test.py --log logs_and_reports/pytest.log` |
| AI summary in HTML report | Set `AI_SUMMARY=true` in `.env`, then run pytest normally |

---

## Capability Feature Flags

All capabilities are toggled in `config/.env` — no code changes required:

```ini
# Core (always on)
FLAKINESS_TRACKING=true
SCREENSHOT_ALL_TESTS=true
RECORD_VIDEO=true

# Flip to true to activate
VISUAL_REGRESSION=false
ACCESSIBILITY_AUDIT=false
PERFORMANCE_METRICS=false

# Requires OPENAI_API_KEY
ENABLE_AI_HEALING=false
AI_SUMMARY=false

# Requires webhook URL
SLACK_NOTIFICATIONS=false
SLACK_WEBHOOK_URL=
TEAMS_WEBHOOK_URL=
```

---

## CI/CD Integration

```yaml
# .github/workflows/tests.yml
name: PlaySight Tests

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
      - name: Prioritise tests by change impact
        run: python tools/prioritize_tests.py --list --dry-run
      - name: Run tests
        run: pytest -n auto -m smoke
        env:
          BASE_URL:            ${{ secrets.BASE_URL }}
          TEST_USER_EMAIL:     ${{ secrets.TEST_USER_EMAIL }}
          TEST_USER_PASSWORD:  ${{ secrets.TEST_USER_PASSWORD }}
          OPENAI_API_KEY:      ${{ secrets.OPENAI_API_KEY }}
          SLACK_WEBHOOK_URL:   ${{ secrets.SLACK_WEBHOOK_URL }}
      - name: Upload reports
        uses: actions/upload-artifact@v4
        if: always()
        with:
          name: playsight-reports
          path: logs_and_reports/
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `zsh: command not found: pytest` | Activate venv: `source .venv/bin/activate` |
| `ModuleNotFoundError: greenlet` | Wrong Python version. Use `python3.11 -m venv .venv` |
| `pip install` gets 403 Forbidden | Proxy blocking PyPI. Open a fresh terminal without `HTTP_PROXY` set |
| Browser fails to launch | Run from your own terminal, not inside a sandboxed IDE shell |
| Screenshot warning — page closed | Already fixed: screenshots taken in `makereport(when="call")` hook |
| axe-core `ERR_NAME_NOT_RESOLVED` | No internet. Save locally: `curl -o utils/axe.min.js <CDN_URL>` |
| Dashboard shows no data | Run `pytest tests/ -v` first to populate `logs_and_reports/runs/` |
| venv scripts broken after folder rename | Delete `.venv` and recreate: `python3.11 -m venv .venv && pip install -r requirements.txt` |

---

## Contributing

1. Read `HOW_TO_PROCEED.md` for onboarding and contribution rules
2. Read `DO_NOT_DO.md` before writing any code
3. All new Page Objects must extend `BasePage`
4. All fixtures go in `tests/conftest.py`
5. Run `pytest --co -q` to confirm no import errors before pushing
6. Never commit `config/.env`

---

## License

This framework is open for use in any project. Swap `BASE_URL` and credentials in `config/.env` to point at your own application — no other changes required.
