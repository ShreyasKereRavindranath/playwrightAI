# UTILS_GUIDELINES.md — Utilities Layer Rules

> **Scope:** Every file inside `utils/` must comply with these rules.

---

## What Belongs Here

| File | Purpose |
|------|---------|
| `logger.py` | Framework logger factory — call `get_logger(__name__)` |
| `api_client.py` | REST API client for test data setup/teardown via API |
| `ai_self_heal.py` | LLM-assisted locator recovery (optional, config-gated) |

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

> Last reviewed: 2026-06-26
