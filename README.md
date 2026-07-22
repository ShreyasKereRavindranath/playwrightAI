# 🎯 Shreyzen

[![CI](https://github.com/ShreyasKereRavindranath/playwrightAI/actions/workflows/pr-checks.yml/badge.svg)](https://github.com/ShreyasKereRavindranath/playwrightAI/actions/workflows/pr-checks.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)](https://www.python.org/downloads/release/python-3110/)
[![Playwright](https://img.shields.io/badge/Playwright-1.49-2EAD33.svg)](https://playwright.dev/python/)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)
[![Code of Conduct](https://img.shields.io/badge/Contributor%20Covenant-2.1-purple.svg)](CODE_OF_CONDUCT.md)

A hybrid **Playwright + Pytest** automation framework with AI assists, a full
**load / performance / security** testing platform, and two web dashboards — all
runnable locally with no commands to memorise.

## Demo

<!-- TODO: record a ~20-30s GIF/MP4 of ./run.sh → Studio → pick tests → Run → live pass/fail,
     drop it in docs/assets/, and replace the placeholder below. See CONTRIBUTING.md § Demo asset. -->
<p align="center">
  <img src="docs/assets/demo.gif" alt="Shreyzen Studio demo — pick tests, run, watch live pass/fail" width="820">
  <br>
  <em>Shreyzen Studio: pick tests, hit Run, watch live results. (Demo GIF coming soon.)</em>
</p>

- **UI tests** (Playwright) split by layer: `api` · `web` · `mobile`
- **Load testing** (Locust): 6 profiles, custom virtual-user control, live dashboard
- **Reports everywhere**: HTML · JUnit · JSON · Allure
- **AI capabilities**: self-healing locators, NL→test generation, auto-repair, test-quality audit, run summaries — **pluggable across OpenAI · Anthropic · Gemini · Ollama · LM Studio · custom endpoints**
- **CI ready**: GitHub Actions for PR checks, on-demand load, and nightly soaks
- **Zero-friction browsers**: the required browser auto-installs on first run

---

## Quick start

**One command** — creates the virtualenv, installs everything on first launch,
and opens the runner. Browsers and the mock API start themselves later from the UI:

```bash
./run.sh                    # → http://127.0.0.1:8770
```

That's it — open the URL, pick tests, hit **Run**. `./run.sh` also forwards args:
`./run.sh serve --port 9100` or `./run.sh run --scenario crud --profile smoke`.

<details><summary>No bash? Run the Python launcher directly</summary>

```bash
python3.11 -m venv .venv && source .venv/bin/activate   # once (Python 3.11)
python tools/studio.py serve                       # → http://127.0.0.1:8770
```
It installs `requirements.txt` on first launch; the Playwright browser
auto-installs on the first web/mobile run; the mock API auto-starts on demand.
</details>

<details><summary>Prefer to run pytest directly / set up manually?</summary>

```bash
python3.11 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt      # installs Playwright + Locust
cp config/.env.example config/.env   # then edit values
pytest tests/web -v                  # browser auto-installs on first run
```
</details>

---

## Running tests

```bash
pytest                     # everything
pytest tests/api -v        # API layer      (or: pytest -m api)
pytest tests/web -v        # Web UI, desktop (or: pytest -m web)
pytest tests/mobile -v     # Mobile UI      (or: pytest -m mobile)
pytest -m smoke            # critical path only
pytest -n 4                # parallel (xdist)
```

Mobile device is configurable: `MOBILE_DEVICE="iPhone 13" pytest tests/mobile -v`.

## Shreyzen Studio — the unified runner

```bash
python tools/studio.py serve                 # → http://127.0.0.1:8770   (or ./run.sh)
```

**Studio** is one UI for everything, with a **light/dark theme toggle**, a live
**top-bar status strip** (green/red dots for the mock server, active runs, LLM
provider, each AI agent — planner · generator · healer — and background
processes; hover for details), **auto-download toasts** (what was fetched and how
much local disk it uses), inline **error toasts**, and **graceful shutdown**
(Ctrl-C stops every background process — mock servers, runs, ollama — nothing
left hanging). Tabs:

- **Functional** — a collapsible api/web/mobile test tree (**collapsed by
  default**); pick a whole layer, a file, or individual tests; choose the target
  per run (BASE_URL; mock / public / custom API; mobile **device** + **markers**
  as dropdowns; browser; headless); watch live pass/fail; open or **download**
  HTML/JUnit/JSON/Allure. Recent runs are labelled by test type (`WEB_` · `API_`
  · `MOBILE_`).
- **Load** — Locust scenarios at any scale, headless-CI friendly:

```bash
python tools/studio.py run --scenario crud --profile smoke   # exits non-zero on breach
```

> ⚠️ **Point load tests at systems you own or are authorised to test.** The bundled
> **mock** server is the intended load target. The **public** API option
> (`restful-booker.herokuapp.com`) is a shared free demo — fine for a few
> functional API calls, but do **not** aim `load` / `stress` / `spike` / `soak`
> profiles at it (or any third-party host) without permission.

- **Analytics** — pass-rate/flakiness/perf trends (also standalone: `python tools/dashboard.py`).
- **Compare Runs** — pick 2+ runs (load or functional) and compare metrics side by side (table scrolls horizontally).
- **AI Provider** — pick the LLM provider (see below).

Every run embeds **who ran it, browser, OS/version, and a timezone-aware timestamp**
into its reports, and each report is **titled by its test type** (e.g. `WEB_API_…`
for functional, `LOAD_<scenario>_<profile>` for load). The active LLM provider and
model are recorded too — or, when none is configured/active, a deterministic
offline fallback. Load scenarios: `crud` · `journey` · `security`. Profiles:
`smoke` · `load` · `stress` · `spike` · `soak` · `breakpoint` · `custom`.
Full guide: **[LOAD_TESTING.md](Shreyzen/LOAD_TESTING.md)**.

## Dashboards

```bash
python tools/studio.py serve         # unified runner + analytics + compare        → :8770  (./run.sh)
python tools/dashboard.py            # standalone analytics (also merged in Studio) → :8766
```

---

## Project layout

```
config/   → configuration loader + .env
pages/    → Page Object Model classes
tests/    → api/ · web/ · mobile/ (+ shared conftest.py)
load/     → Locust scenarios, profiles, run engine
utils/    → logger, browser bootstrap, AI helpers, trackers
tools/    → dashboard, load_runner, functional engine, mock API, generators
data/     → JSON test data + visual baselines
logs_and_reports/  → generated reports, screenshots, load & functional runs
run.sh    → one-command launcher

../.github/workflows/ → CI, at the repo root (pr-checks · load-manual · nightly-soak)
```

## Documentation

| Doc | What it covers |
|-----|----------------|
| [HOW_TO_PROCEED.md](Shreyzen/HOW_TO_PROCEED.md) | Onboarding: setup → architecture → building tests |
| [HOW_TO_CONFIGURE.md](Shreyzen/HOW_TO_CONFIGURE.md) | Every capability, its config flags, and run commands |
| [LOAD_TESTING.md](Shreyzen/LOAD_TESTING.md) | Load runner, profiles, scenarios, reports, CI |
| [LLM_PROVIDERS.md](Shreyzen/LLM_PROVIDERS.md) | Multi-provider LLM layer: providers, config, local setup, extending |
| [DO_NOT_DO.md](Shreyzen/DO_NOT_DO.md) | Anti-patterns to avoid |
| `*/‌*_GUIDELINES.md` | Layer-specific rules (tests, pages, config, utils, data, agents) |

---

## CI (GitHub Actions)

| Workflow | Trigger | Runs |
|----------|---------|------|
| `pr-checks.yml` | PR / push to `main` | API smoke · smoke load · web · mobile |
| `load-manual.yml` | manual dispatch | any scenario/profile/scale |
| `nightly-soak.yml` | nightly | soak on crud · journey · security |

---

## Roadmap

A lightweight snapshot of where Shreyzen is headed — see the
[issues](https://github.com/ShreyasKereRavindranath/playwrightAI/issues) for
details and to weigh in.

- **In progress** — sharper AI auto-repair, richer run-comparison metrics.
- **Planned** — visual-regression baselines in Studio, more Locust scenarios, downloadable Allure trend history.
- **Ideas / help wanted** — additional LLM providers, accessibility (a11y) test layer, container/devcontainer setup.

Have an idea? Open a [feature request](https://github.com/ShreyasKereRavindranath/playwrightAI/issues/new/choose).

---

## License & Contributing

Shreyzen is open source under the **[MIT License](LICENSE)** — free for anyone to
use, fork, modify, and ship in their own projects (a copyright/permission notice is
all that's asked).

If it's useful to you: **⭐ star the repo, 🍴 fork it for your own project, and 🙌
contribute** — improvements, bug reports, and constructive critique are all welcome.

- **New here?** Start with **[CONTRIBUTING.md](CONTRIBUTING.md)** — setup, coding
  standards, and the PR workflow. Look for
  [good first issues](https://github.com/ShreyasKereRavindranath/playwrightAI/labels/good%20first%20issue)
  to get started.
- **Be kind** — this project follows a **[Code of Conduct](CODE_OF_CONDUCT.md)**.
- **Found a vulnerability?** See our **[Security Policy](SECURITY.md)** for private disclosure.

See the layer `*_GUIDELINES.md` docs for conventions before submitting a PR.

## Trademarks & attribution

Shreyzen builds on and integrates with excellent open-source and third-party
projects. All product names, logos, and brands are the property of their
respective owners, and their use here is for identification and compatibility
purposes only — it does not imply any endorsement or affiliation. This includes,
among others: Playwright (Microsoft), Cypress (Cypress.io), Locust, Allure
(Qameta Software), Chart.js, axe-core (Deque Systems), OpenAI, Anthropic, Google
Gemini, Ollama, LM Studio, and iPhone (Apple Inc.). Bundled fonts and libraries
are used under their own licenses (e.g. the Inter font under the SIL Open Font
License, Chart.js under MIT, axe-core under MPL-2.0).
