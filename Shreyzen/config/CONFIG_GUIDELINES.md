# CONFIG_GUIDELINES.md — Configuration Layer Rules

> **Scope:** Every file inside `config/` must comply with these rules.

---

## What Belongs Here

- `config.py` — the single source of truth for all configuration values
- `.env.example` — schema reference for required environment variables (committed to source control)
- `.env` — actual secret values (NEVER committed — already in `.gitignore`)

## Rules

### DO:
- Always load environment variables through `Config` class attributes
- Use `Config.validate()` in the session-scoped fixture to catch missing variables early
- Keep `.env.example` up to date whenever a new `Config` attribute is added
- Use clear prefix conventions: `TEST_USER_*` for credentials, `AI_*` for AI settings
- Group related flags: browser provisioning (`AUTO_INSTALL_BROWSERS`,
  `INSTALL_BROWSER_DEPS`), capabilities (`VISUAL_REGRESSION`, `ACCESSIBILITY_AUDIT`,
  `PERFORMANCE_METRICS`, `FLAKINESS_TRACKING`), and notifications (`SLACK_*`)

### DO NOT:
- Call `os.environ` or `os.getenv` directly in any file outside `config/config.py`
- Store default production credentials in `.env.example`
- Define the same config key in both `pytest.ini` and `config.py`
- Commit `.env` files under any circumstances

---

> Last reviewed: 2026-07-20
