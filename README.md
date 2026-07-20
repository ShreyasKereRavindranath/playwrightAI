# 🎭 PlaySight

A hybrid **Playwright + Pytest** automation framework with AI assists, a full
**load / performance / security** testing platform, and two web dashboards — all
runnable locally with a single command.

> **The framework lives in [`PlaySight/`](PlaySight/).** Run every command from
> that directory (`cd PlaySight`). Full docs: **[PlaySight/README.md](PlaySight/README.md)**.

- **UI tests** (Playwright) split by layer: `api` · `web` · `mobile`
- **Load testing** (Locust): 6 profiles, custom virtual-user control, live dashboard
- **Unified runner**: pick functional (api/web/mobile) *or* load tests from one UI
- **Reports everywhere**: HTML · JUnit · JSON · Allure
- **AI capabilities**: self-healing locators, NL→test generation, auto-repair, test-quality audit, run summaries — **pluggable across OpenAI · Anthropic · Gemini · Ollama · LM Studio · custom endpoints**
- **CI ready**: GitHub Actions for PR checks, on-demand load, and nightly soaks
- **Zero-friction**: dependencies, browsers, and the mock API all install/start themselves

---

## Quick start

**One command** — creates the virtualenv, installs everything on first launch, and
opens the runner. Browsers and the mock API start themselves later from the UI:

```bash
cd PlaySight
./run.sh                    # → http://127.0.0.1:8770
```

Open the URL, pick tests, hit **Run**. `./run.sh` also forwards args:
`./run.sh serve --port 9100` or `./run.sh run --scenario crud --profile smoke`.

<details><summary>No bash? Run the Python launcher directly</summary>

```bash
cd PlaySight
python3.11 -m venv .venv && source .venv/bin/activate   # once (Python 3.11)
python tools/studio.py serve                       # → http://127.0.0.1:8770
```
It installs `requirements.txt` on first launch; the Playwright browser
auto-installs on the first web/mobile run; the mock API auto-starts on demand.
</details>

<details><summary>Prefer to run pytest directly / set up manually?</summary>

```bash
cd PlaySight
python3.11 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt      # installs Playwright + Locust
cp config/.env.example config/.env   # then edit values
pytest tests/web -v                  # browser auto-installs on first run
```
</details>

---

## Running tests

_All commands run from `PlaySight/`._

```bash
pytest                     # everything
pytest tests/api -v        # API layer      (or: pytest -m api)
pytest tests/web -v        # Web UI, desktop (or: pytest -m web)
pytest tests/mobile -v     # Mobile UI      (or: pytest -m mobile)
pytest -m smoke            # critical path only
pytest -n 4                # parallel (xdist)
```

Mobile device is configurable: `MOBILE_DEVICE="iPhone 13" pytest tests/mobile -v`.

## PlaySight Studio — the unified runner

```bash
cd PlaySight && ./run.sh              # or: python tools/studio.py serve  → :8770
```

One UI for everything, with a **light/dark theme toggle**, a live **top-bar status
strip** (green/red dots + tooltips for the mock server, active runs, LLM provider,
and background processes), inline **error toasts**, and **graceful shutdown**
(Ctrl-C stops every background process — nothing left hanging). Tabs:
**Functional** (collapsible api/web/mobile test tree; per-run target with device &
marker dropdowns; live pass/fail; open or **download** reports), **Load** (Locust
at any scale), **Analytics** & **Compare Runs** (trends + side-by-side), and
**AI Provider**. Every run embeds who ran it, browser, OS, and a timezone-aware
timestamp into its reports.

```bash
# Headless load run (exits non-zero if profile thresholds are breached)
python tools/studio.py run --scenario crud --profile smoke
```

Load scenarios: `crud` · `journey` · `security`. Profiles: `smoke` · `load` · `stress`
· `spike` · `soak` · `breakpoint` · `custom`. Full guide: **[PlaySight/LOAD_TESTING.md](PlaySight/LOAD_TESTING.md)**.

## Dashboards

```bash
python tools/studio.py serve         # unified runner + analytics + compare        → :8770  (./run.sh)
python tools/dashboard.py            # standalone analytics (also merged in Studio) → :8766
```

---

## Project layout

```
PlaywrightFramework/          ← repo root
├── .github/workflows/        → CI (pr-checks · load-manual · nightly-soak)
├── README.md                 → this file
└── PlaySight/                → the framework (run everything from here)
    ├── config/   → configuration loader + .env
    ├── pages/    → Page Object Model classes
    ├── tests/    → api/ · web/ · mobile/ (+ shared conftest.py)
    ├── load/     → Locust scenarios, profiles, run engine
    ├── utils/    → logger, browser bootstrap, AI helpers, trackers
    ├── tools/    → dashboard, load_runner, functional engine, mock API, generators
    ├── data/     → JSON test data + visual baselines
    ├── logs_and_reports/ → generated reports, screenshots, load & functional runs
    └── run.sh    → one-command launcher
```

## Documentation

| Doc | What it covers |
|-----|----------------|
| [PlaySight/README.md](PlaySight/README.md) | Full project readme |
| [PlaySight/HOW_TO_PROCEED.md](PlaySight/HOW_TO_PROCEED.md) | Onboarding: setup → architecture → building tests |
| [PlaySight/HOW_TO_CONFIGURE.md](PlaySight/HOW_TO_CONFIGURE.md) | Every capability, its config flags, and run commands |
| [PlaySight/LOAD_TESTING.md](PlaySight/LOAD_TESTING.md) | Load runner, profiles, scenarios, reports, CI |
| [PlaySight/LLM_PROVIDERS.md](PlaySight/LLM_PROVIDERS.md) | Multi-provider LLM layer: providers, config, local setup, extending |
| [PlaySight/DO_NOT_DO.md](PlaySight/DO_NOT_DO.md) | Anti-patterns to avoid |
| `PlaySight/*/*_GUIDELINES.md` | Layer-specific rules (tests, pages, config, utils, data, agents) |

---

## CI (GitHub Actions)

| Workflow | Trigger | Runs |
|----------|---------|------|
| `pr-checks.yml` | PR / push to `main` | API smoke · smoke load · web · mobile |
| `load-manual.yml` | manual dispatch | any scenario/profile/scale |
| `nightly-soak.yml` | nightly | soak on crud · journey · security |

---

## License & Contributing

Open source under the **[MIT License](LICENSE)** — free for anyone to use, fork,
modify, and ship in their own projects.

If it helps you: **⭐ star the repo, 🍴 fork it, and 🙌 contribute** — improvements,
bug reports, and constructive critique are welcome via issues and pull requests.
