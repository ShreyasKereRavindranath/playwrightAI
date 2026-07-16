# AGENTS_GUIDELINES.md — the Planner → Generator → Healer trio

> Read this before modifying anything in `agents/`. These agents author and
> maintain the very tests the framework runs, so their output must obey the same
> rules everyone else does — see `DO_NOT_DO.md` and the other `*_GUIDELINES.md`.

## What the three agents do

```
             ┌──────────┐        ┌────────────┐        ┌──────────┐
 feature ───▶│  Planner │──plan─▶│  Generator │──code─▶│  (tests) │
 / story     └──────────┘        └────────────┘        └────┬─────┘
                                                             │ failures
                                                        ┌────▼─────┐
                                                        │  Healer  │──▶ diagnosis + fix
                                                        └──────────┘
```

| Agent | Input | Output (schema) |
|-------|-------|-----------------|
| `Planner` | plain-English feature / user story | `TestPlan` (list of `Scenario`) |
| `Generator` | one `Scenario` | `GeneratedArtifact` (pytest code + optional Page Object) |
| `Healer` | pytest log or error + source | `HealResult` (diagnosis + fix) |

All data contracts live in `agents/schemas.py` (pydantic). Agents exchange these
models, never loose dicts, so plans round-trip to JSON and prompts have one schema.

## Design rules

1. **Never crash on a missing API key.** Every agent extends `BaseAgent`, which
   exposes `use_llm`. If the LLM is unavailable or errors, fall back to the
   deterministic offline path and set `generated_by="offline"` (or `"rule"`) so
   callers can tell. This is what makes `examples/` runnable in CI.
2. **All LLM calls go through `utils/llm_client.LLMClient`** — never call the
   OpenAI SDK directly. Config (model, key, tokens) comes from `config/config.py`.
3. **Generated code must obey `DO_NOT_DO.md`.** No raw `page.click`/`fill` in
   tests, no `time.sleep`, no hardcoded URLs/credentials, locators only in Page
   Objects, data from the `e2e_data` fixture. The offline templates already do
   this; keep it that way if you edit prompts.
4. **The Healer proposes; humans dispose.** Fixes are emitted as unified diffs or
   guidance. `Healer.apply()` only touches disk when explicitly called (CLI
   `--apply`), and only for the two diff shapes it can verify (1:1 replacement,
   pure removal). Anything else is `fix_kind="none"` guidance.
5. **Offline heuristics are AUT-aware but documented.** The Planner assumes
   inventory/cart/checkout require login (true for the SauceDemo AUT) and prepends
   `login`. If you retarget the framework, update `_KNOWN_PAGES` and that rule.

## Where things go

```
agents/
├── __init__.py       → public exports (Planner, Generator, Healer, schemas)
├── base_agent.py     → BaseAgent: LLM routing, offline flag, text helpers
├── schemas.py        → pydantic contracts (TestPlan, Scenario, HealResult, …)
├── planner.py        → Planner
├── generator.py      → Generator
└── healer.py         → Healer

tools/agents_cli.py   → CLI: plan | generate | heal | pipeline
examples/             → runnable demos + sample inputs
tests/generated/      → Generator output (review before committing)
logs_and_reports/plans/ → saved TestPlan JSON
```

## Relationship to existing capabilities

- `utils/ai_self_heal.py` heals locators **at runtime** (during a test). The
  `Healer` agent heals **after** a run (CI/local) and can emit diffs. They are
  complementary; both route through `LLMClient`.
- `tools/generate_test.py` and `tools/repair_test.py` are the older single-shot
  CLIs. The `agents/` package is the cohesive, offline-capable successor; prefer
  `tools/agents_cli.py` going forward.

## Extending

- **New page object?** Add its keywords to `agents/planner.py::_KNOWN_PAGES` and
  its fixture to `agents/generator.py::_FIXTURES`.
- **New failure category?** Add a pattern to `agents/healer.py::_PATTERNS`, an
  explanation in `_explain`, and a branch in `_heal_offline`.
- **Always** run the demos after changing an agent — they are the smoke test:
  `for f in examples/0*.py; do python "$f"; done`.
