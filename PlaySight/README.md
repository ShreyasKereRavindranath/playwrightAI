# 🎭 PlaySight

A hybrid **Playwright + Pytest** automation framework with AI assists, a full
**load / performance / security** testing platform, and two web dashboards — all
runnable locally with no commands to memorise.

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

## PlaySight Studio — the unified runner

```bash
python tools/studio.py serve                 # → http://127.0.0.1:8770   (or ./run.sh)
```

**Studio** is one UI for everything, with a **light/dark theme toggle**, a live
**top-bar status strip** (green/red dots for the mock server, active runs, LLM
provider, and background processes — hover for details), inline **error toasts**,
and **graceful shutdown** (Ctrl-C stops every background process — mock servers,
runs, ollama — nothing left hanging). Tabs:

- **Functional** — a collapsible api/web/mobile test tree; pick a whole layer, a
  file, or individual tests; choose the target per run (BASE_URL; mock / public /
  custom API; mobile **device** + **markers** as dropdowns; browser; headless);
  watch live pass/fail; open or **download** HTML/JUnit/JSON/Allure.
- **Load** — Locust scenarios at any scale, headless-CI friendly:

```bash
python tools/studio.py run --scenario crud --profile smoke   # exits non-zero on breach
```

- **Analytics** — pass-rate/flakiness/perf trends (also standalone: `python tools/dashboard.py`).
- **Compare Runs** — pick 2+ runs (load or functional) and compare metrics side by side.
- **AI Provider** — pick the LLM provider (see below).

Every run embeds **who ran it, browser, OS/version, and a timezone-aware timestamp**
into its reports. Load scenarios: `crud` · `journey` · `security`. Profiles:
`smoke` · `load` · `stress` · `spike` · `soak` · `breakpoint` · `custom`.
Full guide: **[LOAD_TESTING.md](LOAD_TESTING.md)**.

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
| [HOW_TO_PROCEED.md](HOW_TO_PROCEED.md) | Onboarding: setup → architecture → building tests |
| [HOW_TO_CONFIGURE.md](HOW_TO_CONFIGURE.md) | Every capability, its config flags, and run commands |
| [LOAD_TESTING.md](LOAD_TESTING.md) | Load runner, profiles, scenarios, reports, CI |
| [LLM_PROVIDERS.md](LLM_PROVIDERS.md) | Multi-provider LLM layer: providers, config, local setup, extending |
| [DO_NOT_DO.md](DO_NOT_DO.md) | Anti-patterns to avoid |
| `*/‌*_GUIDELINES.md` | Layer-specific rules (tests, pages, config, utils, data, agents) |

---

## CI (GitHub Actions)

| Workflow | Trigger | Runs |
|----------|---------|------|
| `pr-checks.yml` | PR / push to `main` | API smoke · smoke load · web · mobile |
| `load-manual.yml` | manual dispatch | any scenario/profile/scale |
| `nightly-soak.yml` | nightly | soak on crud · journey · security |

---

## License & Contributing

PlaySight is open source under the **[MIT License](LICENSE)** — free for anyone to
use, fork, modify, and ship in their own projects (a copyright/permission notice is
all that's asked).

If it's useful to you: **⭐ star the repo, 🍴 fork it for your own project, and 🙌
contribute** — improvements, bug reports, and constructive critique are all
welcome via issues and pull requests. See the layer `*_GUIDELINES.md` docs for
conventions before submitting a PR.
