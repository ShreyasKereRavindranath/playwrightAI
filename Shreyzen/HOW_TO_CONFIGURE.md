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

Automatically recovers from broken locators **at runtime**: when a click or fill
times out, the framework snapshots the live DOM, asks the configured LLM for a
replacement locator, retries the action, and logs the healed locator for review.

**Setup:**
1. Pick/configure any LLM provider (Capability 17) — OpenAI, Anthropic, Gemini,
   Ollama, or LM Studio. Healing is **provider-neutral**, not OpenAI-only.
2. Set `ENABLE_AI_HEALING=true` in `config/.env`

**How it fires:** Healing is built into `BasePage.click()` and `BasePage.fill()`,
so **every existing test** self-heals with no code changes. For fragile call
sites you can use `safe_click(locator, intent="the checkout button")` /
`safe_fill(...)` to give the healer richer intent context.

**Review healed locators** (fold the good ones back into your Page Objects):
```bash
python -m tools.healings                       # list pending healings
python -m tools.healings --all --json          # everything, machine-readable
python -m tools.healings --reviewed "<intent>" # mark one reviewed
```
The raw log remains at `data/healing_log.json`.

**Auto-fix / auto-PR (fold healings back automatically):** instead of hand-editing,
let the framework rewrite the Page Object. Because the healing log now records the
**original** selector alongside the healed one, `tools/heal_pr.py` can find that
selector in `pages/*.py` and replace it — then optionally commit on a branch and
open a PR.

```bash
python -m tools.heal_pr                 # preview the proposed edits (dry run)
python -m tools.heal_pr --apply         # write the fixes into pages/*.py
python -m tools.heal_pr --open-pr       # write + commit on a branch + open a PR (needs the gh CLI)
python -m tools.heal_pr --json          # machine-readable plan
```

Only **unambiguous** healings are applied automatically — the original selector
must appear in exactly one place in exactly one Page Object. Anything else
(`ambiguous`, `not_found`, `no_original`) is reported for a human, never guessed.
Auto-fix works for `self.page.locator("<css/test-id>")`-style locators (the fragile
kind healing targets); `get_by_role/label` locators are reported for manual edit.
Applied entries are marked in the log (`APPLIED` / `PR_OPENED`) so they don't
resurface. Without the `gh` CLI, `--open-pr` still commits on a branch and prints
the push/PR command. Wire it into a nightly job to keep locators self-maintaining.

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

### AI diagnosis + auto-quarantine

Tracking a flake is only half the job — this turns it into action. `tools/flaky.py`
explains *why* a test is flaky (AI diagnosis, with a deterministic offline
heuristic when no LLM is configured) and manages a **quarantine list** that keeps
known-flaky tests out of the gating run so they can't fail the build.

```bash
python -m tools.flaky                      # list flaky tests + quarantine status
python -m tools.flaky --diagnose           # diagnose every flaky test (category · why · fix)
python -m tools.flaky --diagnose "<id>"    # diagnose one test id
python -m tools.flaky --quarantine "<id>"  # quarantine a test (records the diagnosis)
python -m tools.flaky --unquarantine "<id>"
python -m tools.flaky --list-quarantine
```

Diagnosis categories: `timing · animation · network · data · selector ·
order_dependency · cross_browser · unknown`, each with a concrete suggested fix.

**Quarantine lane.** Quarantined tests (in `data/quarantine.json`) are deselected
from the normal run and marked `@pytest.mark.quarantine`; run just them in their
own lane with:
```bash
pytest --quarantine-only        # run ONLY the quarantined tests
```

**Auto-quarantine (opt-in).** With `AUTO_QUARANTINE=true`, the session-end hook
adds any newly-detected flaky test to the quarantine list automatically, attaching
its AI/heuristic diagnosis and suggested fix. Enforcement is on by default
(`QUARANTINE_ENABLED=true`) and is a no-op while the list is empty.

```ini
QUARANTINE_ENABLED=true   # honour the quarantine list during collection
AUTO_QUARANTINE=false     # auto-add newly-flaky tests at session end
```

**CI pattern:** gate on the clean suite, then run the flaky lane non-blocking:
```bash
pytest -m smoke                       # gating job — quarantined flakes excluded
pytest --quarantine-only || true      # visibility job — never fails the build
```

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

> **Reliability:** generated files are automatically validated with
> `pytest --collect-only` and repaired by the LLM on failure — see
> **Capability 28**. Disable per-run with `--no-repair`.

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

> **Prefer Capability 22** (record-and-generate) for a one-click flow — Studio
> launches the recorder and converts the result automatically, no manual
> `codegen`/convert steps.

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

> **Prefer Capability 21** (import-graph impact analysis) for zero-maintenance
> selection — it needs no manual map and traces the real dependency graph.

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
PERFORMANCE_GATE=false           # true → exceeding a budget FAILS the test (hard gate)
```

By default a blown budget only logs a warning. Set `PERFORMANCE_GATE=true` to
turn budgets into a **hard gate**: any otherwise-passing test whose LCP or load
time exceeds its budget is failed, with the breach recorded in the report (and,
with `TRACE_ON_FAILURE=true`, a trace captured). This makes perf regressions
break the build like any functional failure.

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
- **AI Failure Analysis** — every failed/error test gets a 🤖 root-cause panel
  right in the per-test results: the diagnosed **reason**, a **confidence** bar,
  the failure **category**, the **failing locator** and source file, likely
  **causes**, and a **suggested fix** (locator swap or unified diff). It's powered
  by the [Healer agent](agents/AGENTS_GUIDELINES.md) and **works offline** (a
  deterministic rule engine) — richer diffs appear when an LLM provider is
  configured. The **top (highest-confidence) failure is also injected as a banner
  at the top of the run's HTML report**, and every analysed failure is persisted
  to `logs_and_reports/functional_runs/<run_id>/analysis.json`. Analysis never
  blocks or breaks a run — if it can't classify a failure it simply doesn't
  annotate it.

**Load mode** — Locust load/soak/spike/stress + non-destructive security probes:
- **6 load profiles:** smoke, load, stress, spike, soak, breakpoint (+ Custom).
- **4 scenarios:** API CRUD (full create/read/update/patch/delete), a full
  user journey, security probes, and **Selected APIs** (`api_select`) — pick
  exactly which endpoints to hit, then run any profile against just that set.
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

## Capability 18 — Report Retention / Auto-Pruning

Run artifacts (`functional_runs/`, `load_runs/`, `screenshots/`, `videos/`,
`a11y/`, `visual_diffs/`, `runs/*.json`) grow forever otherwise — videos and
self-contained HTML dominate disk. Three independent caps are enforced *per
category*, and pruning runs automatically after every functional/load run.

**Setup (defaults shown):**
```ini
RETENTION_ENABLED=true
RETENTION_MAX_RUNS=50        # keep the 50 newest runs per category (0 = unlimited)
RETENTION_MAX_AGE_DAYS=30    # drop runs older than 30 days (0 = no age limit)
RETENTION_MAX_SIZE_MB=2048   # keep the whole tree under 2 GB, oldest dropped first (0 = off)
```

**On demand (safe to preview first):**
```bash
python -m tools.prune_reports --dry-run          # show what would go, delete nothing
python -m tools.prune_reports                     # enforce Config/.env limits now
python -m tools.prune_reports --max-runs 20 --max-age-days 14 --max-size-mb 1024
```

Newest runs are always kept (ordering is by modification time). Every prune
logs exactly what it dropped and why — no silent truncation. In Shreyzen Studio,
`GET /api/retention/preview` shows the dry-run and `POST /api/retention/prune`
enforces it.

---

## Capability 19 — Inline Trace & Video Viewer
Every failed browser test captures a full **Playwright trace** (DOM snapshots,
screenshots, network, console, sources) into its own run folder, and videos
(when recording) land beside it. Shreyzen Studio surfaces both inline under the
functional run's Results panel — one click from a failure to *watching what
happened*.

**Setup (defaults shown):**
```ini
TRACE_ON_FAILURE=true   # write trace.zip for tests that fail (off = never trace)
RECORD_VIDEO=true       # record video (only applies when HEADLESS=false)
```

Traces are written **only on failure** to keep disk use low, and are swept up by
the retention caps above. In Studio, a trace link opens the Playwright Trace
Viewer (`playwright show-trace`) on the host; you can also download the
`trace.zip` and drop it on [trace.playwright.dev](https://trace.playwright.dev).
Videos play inline in the browser.

Endpoints: `GET /api/functional/artifacts/{run_id}` (list),
`GET /fartifact/{run_id}/{name}` (download/stream),
`GET /ftrace/{run_id}/{name}` (open in Trace Viewer).

---

## Capability 20 — Regression Detection & Alerts

The dashboard charts trends; this turns them into decisions. After each run,
the latest result is compared to the **median of prior runs** and meaningful
regressions are flagged — in the log, on the dashboard, and (optionally) in
Slack.

**Setup (defaults shown):**
```ini
REGRESSION_ALERTS=true
REGRESSION_MIN_HISTORY=3        # need at least 3 prior runs before alerting
REGRESSION_PASS_RATE_DROP=5.0   # alert if pass rate drops ≥ 5 percentage points
REGRESSION_DURATION_PCT=25.0    # alert if suite is ≥ 25% slower
REGRESSION_PERF_PCT=25.0        # alert if avg LCP / load_time is ≥ 25% worse
```

Baseline uses the **median** (robust to a single outlier run), and nothing is
reported until there's enough history — no crying wolf. A breach ≥ 2× the
threshold is marked `critical`, otherwise `warning`.

**On demand / CI gate:**
```bash
python -m tools.check_regressions           # human-readable report
python -m tools.check_regressions --gate    # exit 1 on any regression (fails CI)
python -m tools.check_regressions --json     # machine-readable
```

Dashboard endpoint: `GET /api/regressions`. When `SLACK_NOTIFICATIONS=true`,
regressions are also pushed to Slack alongside the run summary.

---

## Capability 21 — Import-Graph Test Impact Analysis

Runs **only the tests a change can actually break** — determined from the real
Python import graph, not a hand-maintained keyword map (Capability 9). A test is
impacted if it, or anything in its transitive import closure, was changed.

**No setup** beyond being a git repo. Non-Python changes under `tests/`/`data/`
and "core" changes (`conftest.py`, `config/config.py`, `pages/base_page.py`,
`requirements.txt`, `pytest.ini`) conservatively trigger the full suite — it
errs toward running too much rather than skipping something risky.

**CLI:**
```bash
python -m tools.impact_run                 # analyse vs working tree, print plan
python -m tools.impact_run --base main     # diff vs main
python -m tools.impact_run --list          # impacted test files (or ALL)
python -m tools.impact_run --run           # run pytest on just those tests
python -m tools.impact_run --run --base origin/main -- -m smoke   # extra pytest args after --
```

**In Studio:** the Functional tab's **🎯 Changed only** button selects exactly
the impacted tests (or the whole suite on a core change). Endpoint:
`GET /api/impact?base=HEAD`.

**Wire into CI:**
```yaml
- name: Run impacted tests
  run: python -m tools.impact_run --run --base origin/${{ github.base_ref }}
```

---

## Capability 22 — Record-and-Generate Authoring

Author a test by *doing* it. Studio launches Playwright's recorder in a real
browser; you click through the flow, close the browser, and the recording is
converted into a framework-shaped **Page Object** (+ optional smoke test) in the
project's conventions — no hand-written selectors.

**Setup:** Any configured LLM provider (Capability 17). Recording needs a
display, so it runs on the machine hosting Studio (not a headless CI box).

**In Studio:** Functional tab → **0 · Record a new test** → enter a start URL and
a page name → **● Record flow**. Files are written to `pages/<name>_page.py` and
`tests/web/test_<name>.py` (existing files are never overwritten).

**CLI:**
```bash
python -m tools.record_generate https://www.saucedemo.com/ --page login
python -m tools.record_generate https://example.com --page checkout --no-test
python -m tools.record_generate https://example.com --page cart --print-only
```

Endpoints: `POST /api/record/start`, `GET /api/record/state`.

> **Reliability:** the converted Page Object + smoke test are validated with
> `pytest --collect-only` and repaired by the LLM on failure — see **Capability 28**.

---

## Capability 23 — Central Results Database

Every functional and load run is persisted as a compact row in a
`run_summaries` table inside `logs_and_reports/flakiness.db` (the same SQLite
file that holds flakiness + perf). This gives you **durable, queryable run
history that survives artifact pruning** — even after the HTML/videos for a run
are swept by retention (Capability 18), its summary row remains.

**Why SQLite (capacity):** a summary row is ~1 KB, so a million runs ≈ 1 GB and
SQLite handles that comfortably (its hard ceiling is 281 TB). The raw
videos/HTML are what actually grow — that's what retention bounds. For a shared
team store you can later point the same API at Postgres; the write path is
isolated in `utils/results_db.py`.

**Automatic:** runs record themselves on finalize — no flag needed.

**Query it:**
```bash
python -m tools.results_db --stats                 # totals + pass rate
python -m tools.results_db --list                  # recent runs
python -m tools.results_db --list --kind load      # filter by kind
python -m tools.results_db --get <run_id>          # full stored summary
python -m tools.results_db --backfill              # import older runs from JSON
```

Endpoints: `GET /api/db/runs`, `GET /api/db/run/{run_id}`,
`POST /api/db/backfill`.

---

## Capability 24 — Natural-Language Test Authoring in Studio

Describe a scenario in plain English and get a framework-shaped Page Object +
pytest test. This is the CLI generator (Capability 5) surfaced in the UI.

**In Studio:** Functional tab → **0b · Describe a test** → type a scenario
(e.g. "User cannot checkout with an empty cart") → **✨ Generate**. Files are
written under `pages/` and `tests/web/` (existing files are never overwritten)
and the code is shown for review. Endpoint: `POST /api/generate`.

> **Multi-user note:** run history is now centralized (Capability 23), which is
> the foundation for a shared team deployment. Per-user auth/roles on the Studio
> server is a deployment concern left to your reverse proxy / SSO for now.

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

> **Note — two distinct banners.** This **executive summary** (whole-run
> narrative, gated on `AI_SUMMARY=true`, injected into
> `logs_and_reports/report.html`) is separate from the **AI Failure Analysis**
> banner that the Studio functional runner injects into each run's own
> `functional_runs/<run_id>/report.html` (top failure's root cause + fix; works
> offline, no flag required — see Capability 15). They don't collide.

---

## Capability 25 — Project Scaffolding (`init`) & Environment Doctor

Drop Shreyzen onto **any** project without inheriting the bundled saucedemo demo.
`init` writes a `config/.env` pointed at your app, scaffolds a starter Page Object
and smoke test in the framework's conventions, and can archive the demo so you
start from a clean slate. `doctor` validates the environment before you run.

**No setup** — both ship with the framework.

### `shreyzen init` — scaffold a new project

```bash
# Interactive (prompts for name / URL / template / page):
python -m tools.init
#   ↳ also: ./run.sh init      or   python tools/studio.py init

# Non-interactive:
python -m tools.init --name "Acme Store" --url https://acme.example.com --template web --page dashboard

# API-only project:
python -m tools.init --url https://api.acme.com --template api --page account

# Start clean — archive the saucedemo demo to examples/legacy_saucedemo/ (reversible):
python -m tools.init --url https://acme.example.com --clean --yes
```

| Flag | Meaning |
|---|---|
| `--name` | Human-readable project name (used in generated docstrings). |
| `--url` | Application root URL → `BASE_URL` in `config/.env`. |
| `--template` | `web` \| `api` \| `mobile` — which starter test to scaffold (default `web`). |
| `--page` | Page/feature name for the starter files (default `home`). |
| `--clean` | Move the bundled saucedemo demo (pages, tests, data) to `examples/legacy_saucedemo/`. |
| `--force` | Overwrite existing generated files (never overwrites silently otherwise). |
| `--yes` | Assume "yes" to prompts (non-interactive / CI). |

What it writes:
- `config/.env` — from `.env.example` with your `BASE_URL` (an existing `.env` is
  never clobbered; only its `BASE_URL` is updated unless you pass `--force`).
- `pages/<page>_page.py` — a `BasePage` subclass stub (skipped for `--template api`).
- `tests/<layer>/test_<page>_smoke.py` — a runnable starter test with a placeholder
  assertion to replace.

**Reversible decoupling:** `--clean` *moves* the demo, it never deletes. The
framework core doesn't depend on the demo — `tests/conftest.py` imports the demo
Page Objects and registers their fixtures **defensively**, so archiving them (or
just deleting `pages/*_page.py`) leaves every capability working. Move the files
back to restore the demo.

### `shreyzen doctor` — validate the environment

```bash
python -m tools.doctor          # human-readable checklist
python -m tools.doctor --json   # machine-readable (for CI)
#   ↳ also: ./run.sh doctor
```

Checks Python version (3.11+), the virtualenv, core dependencies, `config/.env`
and its required keys, the Playwright browser binary, the git repo (needed for
impact analysis), and the selected LLM provider. Each failing check prints a fix
hint. It exits **non-zero only on blocking failures** (soft/optional issues —
e.g. no LLM key — are warnings), so it can gate CI:

```yaml
- name: Validate environment
  run: python -m tools.doctor
```

---

## Capability 26 — Selected-API Load Testing (`api_select`)

Choose **exactly which API endpoints** to exercise (the way you pick individual
tests in the Functional tab), then run **any** load profile against just that
selection. Useful for isolating a hot endpoint under stress, or excluding
destructive verbs from a soak.

**In Studio:** Load tab → pick the **🎯 Selected APIs** scenario → an endpoint
checklist appears (method + path). Tick the endpoints, pick a profile, Run.

**CLI / CI:**
```bash
# Stress only the create + read endpoints:
python tools/studio.py run --scenario api_select --endpoints create,read --profile stress

# All endpoints (omit --endpoints) at a soak profile:
python tools/studio.py run --scenario api_select --profile soak
```

Endpoint keys come from `load/catalog.py:API_ENDPOINTS`
(`auth, create, read, list, update, patch, delete, ping` for the demo booking
API — edit that catalog to point at your own API). An unknown key fails loudly.
The selection is recorded in the run's `results.json` (`meta.endpoints`) and each
chosen endpoint is judged against the profile's thresholds like any other run.

---

## Capability 27 — Extent-style HTML Report

A single self-contained, interactive report — the Python equivalent of Java's
ExtentReports — with a **pass/fail/skip donut**, a **category/suite breakdown**,
and **filterable per-test cards** (status, duration, category, failure message).
It **complements** the existing pytest-html / Allure / JUnit outputs rather than
replacing them.

**Setup:**
```ini
EXTENT_REPORT=true    # off by default
```

**Where it lands:**
- Functional pytest runs → `logs_and_reports/extent_report.html`
- Load runs → `logs_and_reports/load_runs/<run_id>/extent_report.html`

No API key or extra dependency — the report is built with the standard library
and opens in any browser. The builder (`utils/extent_report.py`) is a pure
function of its inputs, so it is fully unit-tested.

---

## Capability 28 — Self-Validating NL Generation (validate-and-repair)

Every AI code-generation path — the **NL generator** (Capabilities 5 & 24), the
**record-and-generate** converter (Capability 22), and the **agent generator**
(planner→generator pipeline) — can emit code that *looks* right but fails to
import, has a syntax slip, or references a fixture that doesn't exist. This
closes that gap: after code is written, the framework runs `pytest --collect-only`
on it and — on failure — feeds the exact pytest error back to the LLM to
**self-correct**, repeating until the files collect cleanly or the attempt budget
runs out.

Collection is the gate because it catches the failure modes generated tests hit
(syntax, imports, unresolved fixtures) **without** launching a browser or hitting
a target — so it's fast and safe to run on every generation.

**Setup (defaults shown):**
```ini
NL_REPAIR_ENABLED=true    # validate + repair after every NL generation
NL_REPAIR_ATTEMPTS=2      # max LLM self-correction rounds
```

**CLI:**
```bash
# On by default — validates and repairs the generated files:
python tools/generate_test.py "User cannot checkout with an empty cart"

# Tune or disable per-run:
python tools/generate_test.py "Login with SQL injection" --repair-attempts 3
python tools/generate_test.py "Add item to cart" --no-repair
```

**In Studio:** the *Describe a test* flow and the *Record a new test* flow both
show a badge on the result — `✅ collects under pytest` (with the repair-round
count) or `⚠ still fails collection — review before running`. The agent
pipeline (`python -m tools.agents_cli generate/pipeline --write`) prints the same
status per scenario.

The loop lives in `utils/generation_validator.py`; both the validator and the
repair function are injectable, so the orchestration is fully unit-tested without
a real LLM. It's best-effort — it never fails the generation request, and on
give-up it keeps the best attempt and warns.

---

## Capability 29 — MCP Server (agent-native interface)

Drive the whole framework from any **MCP client** (Claude Code, Cursor, …). The
server exposes the framework's capabilities as tools an agent can call
conversationally — *"run the smoke tests"*, *"generate a test for empty-cart
checkout and make sure it collects"*, *"why is test Y flaky? quarantine it"*,
*"apply the pending self-heals as a PR"* — without leaving the editor.

**Setup:** `mcp` is an **optional** dependency (in `requirements.txt`). Everything
else works without it; the server prints an install hint if it's missing.
```bash
pip install mcp
python -m tools.mcp_server        # stdio transport
```

**Register with Claude Code / Cursor** (`.mcp.json` or the client's MCP config):
```json
{
  "mcpServers": {
    "shreyzen": {
      "command": "python",
      "args": ["-m", "tools.mcp_server"],
      "cwd": "/absolute/path/to/Shreyzen"
    }
  }
}
```

**Tools exposed:**

| Tool | What it does |
|---|---|
| `discover_tests` | List every collectable test, grouped by layer |
| `run_tests` | Run a selection / marker and return a pass/fail summary (real runner + reports) |
| `generate_test` | NL → Page Object + test, then validate-and-repair (Capability 28) |
| `flaky_list` · `flaky_diagnose` | List flaky tests; explain *why* one is flaky + a fix |
| `quarantine` | list / add / remove quarantined tests (Capability 2) |
| `heal` | Self-heal status / apply / open a PR (Capability 1) |
| `impact_analysis` | Tests a change can break, from the import graph (Capability 21) |
| `run_load` | Run a load profile incl. `api_select` endpoint selection (Capabilities 15, 26) |
| `results` | Recent run history + totals from the central DB (Capability 23) |
| `doctor` | Environment health check |

The tool *logic* lives in plain `tool_*` functions in `tools/mcp_server.py`
(unit-tested with no MCP dependency); FastMCP is a thin wrapper. Long-running
tools (`run_tests`, `run_load`) block until done — expect a wait, or scope them
(markers / a small profile) for interactive use.

---

## Capability 30 — Failure Root-Cause Clustering & Triage

Turns a wall of red into a **ranked, labelled triage list**. Every failure's
message + traceback is persisted during runs; `tools/triage.py` groups recurring
failures into clusters (by a normalized error signature) and labels each root
cause: **product_bug · test_bug · flaky · environment** — via the LLM when a
provider is configured, else a deterministic heuristic.

**Setup:** capture is on by default (no API key needed):
```ini
FAILURE_TRACKING=true   # persist message + traceback per failure for clustering
```

**Run:**
```bash
python -m tools.triage              # ranked clusters + heuristic labels
python -m tools.triage --ai         # let the LLM label each cluster (+ suggested action)
python -m tools.triage --top 5      # only the largest clusters
python -m tools.triage --limit 500  # consider the last N failures
python -m tools.triage --json
```

Each cluster shows the signature, occurrence count, distinct tests and runs it
spans, the root-cause label, and (with `--ai`) a suggested next action. The
heuristic reads the error shape — assertion → product_bug, import/fixture →
test_bug, network/5xx → environment, a timeout across multiple runs → environment
vs. a one-off → flaky.

Failures are stored in `logs_and_reports/flakiness.db` (`test_failures` table).
The signature normalization, clustering, and heuristic are pure functions and
the LLM is injectable, so the pipeline is fully unit-tested. Also exposed as the
`cluster_failures` MCP tool (Capability 29).

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
