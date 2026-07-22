# UTILS_GUIDELINES.md — Utilities Layer Rules

> **Scope:** Every file inside `utils/` must comply with these rules.

---

## What Belongs Here

| File | Purpose |
|------|---------|
| `logger.py` | Framework logger factory — call `get_logger(__name__)` |
| `api_client.py` | REST API client for test data setup/teardown via API |
| `browser_bootstrap.py` | Auto-installs the required Playwright browser on first run |
| `ai_self_heal.py` | LLM-assisted locator recovery (optional, config-gated) |
| `flakiness_tracker.py` | Records pass/fail per test into SQLite; flags flaky tests |
| `visual_regression.py` | Perceptual-hash screenshot diff vs. baselines |
| `accessibility.py` | axe-core WCAG audit runner |
| `performance.py` | Web-Vitals (LCP/CLS/TTFB/load) collector |
| `ai_summary.py` | LLM executive summary injected into the HTML report |
| `slack_notifier.py` | Slack / Teams run-summary notifications |
| `llm_client.py` / `llm_judge.py` | OpenAI wrapper / test-quality auditor |
| `test_data_generator.py` | Synthetic test-data generation |

## Rules

### DO:
- Import `get_logger` from `utils/logger.py` in every module that needs logging
- Use `ApiClient` for all HTTP calls — never import `requests` directly in tests or pages
- Keep utilities stateless or clearly document any stateful requirements

### DO NOT:
- Add UI interaction logic to utilities — that belongs in `pages/`
- Add test assertions to utilities — that belongs in `tests/`
- Add new packages without updating `requirements.txt`
- Create utility functions that duplicate Playwright built-in capabilities

---

> Last reviewed: 2026-07-20
