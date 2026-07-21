# PlaySight — Load & Reliability Testing

A **Cypress-style local runner** for load, performance, and security testing.
No commands to memorise: open the dashboard, pick a test, pick a load profile,
set the virtual users, hit **Run**, and watch it live. Every run also produces
HTML, JUnit, JSON, and Allure reports, and the exact same engine runs headless
in CI.

Built on [Locust](https://locust.io/) (virtual users + custom load shapes) and
FastAPI (the launcher UI). The default target is the bundled mock API
(`tools/mock_api_server.py`), which the runner **auto-starts** — so it works
fully offline with nothing else running.

---

## Quick start

```bash
# one-time (in your own terminal)
source .venv/bin/activate
pip install -r requirements.txt        # installs locust

# launch the runner
python tools/studio.py serve
# → open http://127.0.0.1:8770, pick a test, hit Run
```

That's it. The dashboard auto-starts the mock API on `:8765` if nothing is
already answering there.

---

## Two modes: Functional & Load

The runner has a **Functional Tests** tab and a **Load** tab.

**Functional** runs the pytest suites straight from the browser — pick any
`api` / `web` / `mobile` tests (a whole layer, a file, or individual tests — the
tree is **collapsed by default**), choose the target per run (BASE_URL for
web/mobile; mock / public / custom for API; plus mobile device, browser, headless,
and an optional `-m` marker filter), hit **Run**, and watch live pass/fail with
per-test results. Reports land in `logs_and_reports/functional_runs/<id>/`
(HTML · JUnit · JSON · Allure), each **titled by test type** (`WEB_`/`API_`/`MOBILE_`)
and recent runs in the history are labelled the same way. This is the "run any
test against any specification" path.

**Load** is everything below — Locust virtual-user scenarios shaped by the 6
profiles. The rest of this guide covers Load mode.

---

## The Load tab

| Step | What you do |
|------|-------------|
| **1 · Pick a test** | Choose a scenario card (CRUD / User Journey / Security). |
| **2 · Pick a profile** | Choose one of the 6 load profiles, or **Custom**. |
| **3 · Tune & run** | Drag the **Virtual Users** slider, set duration / spawn rate / host, hit **▶ Run**. |
| **Live Run** | Real-time charts (throughput & VUs, p95 & failures/s), per-endpoint table, and a live log. |
| **History** | Every past run with its verdict and links to the reports. |

Picking a profile pre-fills sensible VU / duration defaults — override any of
them for full **custom VU control** ("run any test at any scale").

---

## Scenarios (what the virtual users do)

| Scenario | Class | Covers |
|----------|-------|--------|
| **API CRUD** | `BookingCrudUser` | Weighted mix of **C**reate (POST) · **R**ead (GET id) · List (GET) · **U**pdate (PUT) · **P**atch (PATCH) · **D**elete (DELETE). |
| **User Journey** | `UserJourneyUser` | One full ordered end-to-end journey per user: auth → create → read → update → patch → delete → confirm-404. |
| **Security Probes** | `SecurityUser` | Non-destructive checks: auth bypass, unauthenticated writes/deletes, SQL-injection in filters, malformed payloads, stored-XSS round-trip. A probe goes **red only when the API behaves insecurely**. |

All scenarios live in [`load/locustfile.py`](load/locustfile.py).

---

## Load profiles (how the load is shaped)

| Profile | Icon | Shape | Default VUs · duration |
|---------|------|-------|------------------------|
| **Smoke** | 💨 | Flat, tiny load — proves it works at all. | 3 · 30s |
| **Load** | 📈 | Ramp to peak, then hold — normal busy traffic. | 50 · 180s |
| **Stress** | 🔥 | Step past peak (~1.5×) to find degradation. | 100 · 240s |
| **Spike** | ⚡ | Low baseline, sudden slam to peak, recover. | 80 · 150s |
| **Soak** | 🛁 | Steady moderate load for a long time — finds leaks. | 30 · 1800s |
| **Breakpoint** | 📉 | Linear ramp with no ceiling (~2× peak). | 150 · 300s |
| **Custom** | 🎛️ | Flat load at exactly your VUs / duration. | your call |

The curves are defined by the pure `plan()` function in
[`load/catalog.py`](load/catalog.py) and driven by the Locust shape in
[`load/shapes.py`](load/shapes.py).

---

## Reports — on every run

Each run writes to `logs_and_reports/load_runs/<run_id>/`:

| Format | File | Notes |
|--------|------|-------|
| **HTML** | `report.html` | Locust's native interactive report, **titled `LOAD_<scenario>_<profile>`** with a user/system/LLM context panel. Open from the UI. |
| **JSON** | `results.json` | Machine-readable summary, thresholds, per-endpoint verdicts. |
| **JUnit** | `junit.xml` | One `<testcase>` per endpoint — ingested by CI test reporters. |
| **Allure** | `allure-results/` | `allure serve logs_and_reports/load_runs/<run_id>/allure-results` |

**Pass/fail is threshold-driven per profile:** an endpoint fails if its failure
ratio exceeds the profile's `max_fail_ratio` **or** its p95 exceeds the
`p95_budget_ms`. The headless CLI exits non-zero on any breach, so it gates CI.

---

## Headless / CLI (what CI uses)

```bash
# Defaults from the profile
python tools/studio.py run --scenario crud --profile smoke

# Any scenario at any scale
python tools/studio.py run --scenario journey --profile custom \
    --users 200 --duration 300 --spawn-rate 20 --host http://127.0.0.1:8765
```

Exit code `0` = all thresholds met, `1` = breach, `2` = bad arguments.

---

## CI (GitHub Actions)

| Workflow | Trigger | What it does |
|----------|---------|--------------|
| [`pr-checks.yml`](../.github/workflows/pr-checks.yml) | every PR + push to `main` | API contract smoke tests, a smoke load run on CRUD, plus web (desktop) and mobile (emulated) UI tests. Fails the PR on any breach. |
| [`load-manual.yml`](../.github/workflows/load-manual.yml) | **Run workflow** button | Pick scenario / profile / users / duration and run at any scale. |
| [`nightly-soak.yml`](../.github/workflows/nightly-soak.yml) | nightly @ 02:30 UTC | Soak on CRUD, Journey, and Security scenarios (matrix). |

All three upload the html + junit + json + allure artifacts.

---

## Targeting your own API

The default host is the bundled mock (`http://127.0.0.1:8765`). To hit a real
API, set the **Target host** field in the UI or pass `--host` on the CLI. The
scenarios assume the restful-booker contract (`/auth`, `/booking`,
`/booking/{id}`); adapt `load/locustfile.py` for a different API shape.

---

## Files

```
load/
├── catalog.py      ← scenarios, profiles, thresholds, pure load-plan math
├── shapes.py       ← Locust LoadTestShape (reads PLAYSIGHT_* env)
├── locustfile.py   ← the 3 scenarios (CRUD / journey / security)
├── reporting.py    ← JSON + JUnit + Allure writers
└── engine.py       ← launch Locust, stream live stats, generate reports
tools/studio.py ← CLI (serve | run) + the launcher dashboard
tools/functional_engine.py ← functional (pytest) test discovery + runner
../.github/workflows/ ← CI at the repo root (pr-checks · load-manual · nightly-soak)
```
