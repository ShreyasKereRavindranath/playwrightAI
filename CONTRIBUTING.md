# Contributing to PlaySight

Thanks for taking the time to contribute! 🎭 PlaySight is a hybrid
**Playwright + Pytest** framework with AI assists, load/performance/security
testing, and web dashboards. Contributions of every size are welcome —
bug reports, docs, tests, and features.

This guide gets you from a fresh clone to a merged PR. By participating you
agree to abide by our [Code of Conduct](CODE_OF_CONDUCT.md).

---

## Table of contents

- [Ways to contribute](#ways-to-contribute)
- [Development setup](#development-setup)
- [Project layout](#project-layout)
- [Coding standards](#coding-standards)
- [Running tests & checks locally](#running-tests--checks-locally)
- [Pull request workflow](#pull-request-workflow)
- [Reporting bugs & requesting features](#reporting-bugs--requesting-features)
- [Demo asset](#demo-asset)

---

## Ways to contribute

- 🐛 **Report a bug** — open a [bug report](https://github.com/ShreyasKereRavindranath/playwrightAI/issues/new/choose).
- ✨ **Request a feature** — open a [feature request](https://github.com/ShreyasKereRavindranath/playwrightAI/issues/new/choose).
- 🧪 **Add tests** — new scenarios under `PlaySight/tests/{api,web,mobile}` or load scenarios under `PlaySight/load`.
- 📝 **Improve docs** — the `*_GUIDELINES.md` docs and the `HOW_TO_*` guides.
- 🌱 **Good first issues** — look for the
  [`good first issue`](https://github.com/ShreyasKereRavindranath/playwrightAI/labels/good%20first%20issue)
  label; these are scoped for newcomers.

---

## Development setup

PlaySight targets **Python 3.11**. The one-command launcher creates the
virtualenv and installs everything on first run.

```bash
git clone https://github.com/ShreyasKereRavindranath/playwrightAI.git
cd playwrightAI/PlaySight

./run.sh                    # creates .venv, installs deps, opens Studio → http://127.0.0.1:8770
```

Prefer to set things up manually?

```bash
cd PlaySight
python3.11 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt      # installs Playwright + Locust
cp config/.env.example config/.env   # then edit values
pytest tests/web -v                  # the browser auto-installs on first run
```

> The Playwright browser auto-installs on the first web/mobile run, and the mock
> API auto-starts on demand — no extra bootstrap steps required.

For AI features (self-healing locators, NL→test generation, auto-repair, etc.),
configure a provider as described in
[LLM_PROVIDERS.md](PlaySight/LLM_PROVIDERS.md). Without a provider, PlaySight
falls back to a deterministic offline mode, so you can still run and develop.

---

## Project layout

```
PlaySight/
  config/   → configuration loader + .env
  pages/    → Page Object Model classes
  tests/    → api/ · web/ · mobile/ (+ shared conftest.py)
  load/     → Locust scenarios, profiles, run engine
  utils/    → logger, browser bootstrap, AI helpers, trackers
  tools/    → dashboard, load_runner, functional engine, mock API, generators
  data/     → JSON test data + visual baselines
  logs_and_reports/  → generated reports, screenshots, load & functional runs
  run.sh    → one-command launcher
.github/workflows/   → CI (pr-checks · load-manual · nightly-soak), at the repo root
```

---

## Coding standards

Each layer documents its own conventions — **read the relevant guide before
opening a PR**:

| Area | Guide |
|------|-------|
| Onboarding & architecture | [HOW_TO_PROCEED.md](PlaySight/HOW_TO_PROCEED.md) |
| Capabilities & config flags | [HOW_TO_CONFIGURE.md](PlaySight/HOW_TO_CONFIGURE.md) |
| Anti-patterns to avoid | [DO_NOT_DO.md](PlaySight/DO_NOT_DO.md) |
| Tests | [tests/TEST_GUIDELINES.md](PlaySight/tests/TEST_GUIDELINES.md) |
| Page objects | [pages/PAGES_GUIDELINES.md](PlaySight/pages/PAGES_GUIDELINES.md) |
| Config | [config/CONFIG_GUIDELINES.md](PlaySight/config/CONFIG_GUIDELINES.md) |
| Utils | [utils/UTILS_GUIDELINES.md](PlaySight/utils/UTILS_GUIDELINES.md) |
| Data | [data/DATA_GUIDELINES.md](PlaySight/data/DATA_GUIDELINES.md) |
| AI agents | [agents/AGENTS_GUIDELINES.md](PlaySight/agents/AGENTS_GUIDELINES.md) |
| Load testing | [LOAD_TESTING.md](PlaySight/LOAD_TESTING.md) |

General principles:

- **Page Object Model** — UI interactions go through page classes in `pages/`, not raw selectors in tests.
- **Markers** — tag tests with the right pytest markers (`api` / `web` / `mobile` / `smoke` / `unit`) so CI and the Studio filters work.
- **Keep it deterministic** — avoid flaky waits; prefer Playwright's auto-waiting and explicit expectations.
- **Data-driven** — put fixtures/test data in `data/`, not inline.
- Skim [DO_NOT_DO.md](PlaySight/DO_NOT_DO.md) — it lists the anti-patterns we actively reject in review.

---

## Running tests & checks locally

Run these before pushing — they mirror the `pr-checks.yml` CI workflow:

```bash
cd PlaySight
pytest -m smoke            # critical path
pytest tests/api -v        # API layer   (or: pytest -m api)
pytest tests/web -v        # Web UI      (or: pytest -m web)
pytest tests/mobile -v     # Mobile UI   (or: pytest -m mobile)
pytest -n 4                # parallel (xdist)

python tools/studio.py run --scenario crud --profile smoke   # smoke load; exits non-zero on breach
```

CI runs on every PR/push to `main`; please make sure your branch is green before
requesting review.

---

## Pull request workflow

1. **Fork** the repo and create a branch from `main`:
   `git checkout -b feat/short-description` (or `fix/…`, `docs/…`).
2. **Make focused changes** — one logical change per PR is easier to review.
3. **Add or update tests** for any behavior change.
4. **Run the checks above** and make sure CI passes.
5. **Write a clear PR description** — the PR template will prompt you for the what/why and how you tested.
6. **Link the issue** it addresses (`Closes #123`).
7. A maintainer will review; please respond to feedback and keep the branch up to date with `main`.

Keep commits meaningful; we're not strict about a specific commit convention, but
a concise, descriptive message helps.

---

## Reporting bugs & requesting features

Please use the [issue templates](https://github.com/ShreyasKereRavindranath/playwrightAI/issues/new/choose).
A good bug report includes:

- What you expected vs. what happened
- Exact steps / command to reproduce
- Environment (OS, Python version, browser)
- Relevant logs from `logs_and_reports/` or console output

For security issues, **do not** open a public issue — follow [SECURITY.md](SECURITY.md).

---

## Demo asset

The README shows a demo GIF from `docs/assets/demo.gif`. To (re)record it:

1. Run `./run.sh` and open Studio at `http://127.0.0.1:8770`.
2. Capture ~20–30s: pick a couple of tests → **Run** → show live pass/fail →
   open a report. Keep it under a few MB.
3. Save it as `docs/assets/demo.gif` (create the folder if needed) and open a PR.

---

Thanks again for contributing! If anything here is unclear, open an issue and
we'll improve this guide.
