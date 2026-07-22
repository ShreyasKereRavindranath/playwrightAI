# Shreyzen AI Agent Examples

Runnable demos of the **Planner → Generator → Healer** trio (see `agents/`).

Every demo runs **with no API key** using deterministic offline logic. Set
`OPENAI_API_KEY` in `config/.env` to switch to the LLM path — the demo code is
identical either way; each agent reports its mode (`OFFLINE` / `LLM`) at the top.

## Prerequisites

```bash
source .venv/bin/activate          # or your venv
pip install -r requirements.txt
playwright install chromium        # only needed to *run* generated tests
```

## The demos

| File | Shows | Needs a browser? |
|------|-------|------------------|
| `01_planner_demo.py` | Feature → structured `TestPlan` (scenarios, priorities, steps) | No |
| `02_generator_demo.py` | Scenario → runnable pytest test wired to fixtures | No (writing); yes to run the test |
| `03_healer_demo.py` | Failures → diagnosis + unified-diff fixes (applied to throwaway copies) | No |
| `04_full_pipeline_demo.py` | Planner → Generator → Healer end-to-end | No (writing); yes to run tests |

```bash
python examples/01_planner_demo.py
python examples/02_generator_demo.py
python examples/03_healer_demo.py
python examples/04_full_pipeline_demo.py
```

`sample_pytest_failure.log` is a realistic two-failure pytest log for the Healer's
log-parsing path:

```bash
python tools/agents_cli.py heal --log examples/sample_pytest_failure.log
```

## Running the generated tests

Generated tests land in `tests/web/generated/` and use the framework's real fixtures
and page objects. They run against the configured `BASE_URL` (SauceDemo by default):

```bash
python tools/agents_cli.py pipeline "User can add a product to the cart and check out" --write
HEADLESS=true pytest tests/web/generated -v
```

These three generated tests pass against the live SauceDemo app out of the box.

## The unified CLI

`tools/agents_cli.py` is the single entry point (`plan`, `generate`, `heal`,
`pipeline`). Run `python tools/agents_cli.py -h` for full usage.
