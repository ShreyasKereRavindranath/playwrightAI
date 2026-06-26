# DATA_GUIDELINES.md — Test Data Layer Rules

> **Scope:** Every file inside `data/` must comply with these rules.

---

## What Belongs Here

- JSON files containing parameterized test inputs
- CSV files for large data-driven test matrices
- Fixture data for API-seeded test records

## File Naming Convention

| Pattern | Example |
|---------|---------|
| `<feature>_test_data.json` | `login_test_data.json` |
| `<feature>_test_data.csv` | `checkout_test_data.csv` |

## Rules

### DO:
- Use descriptive keys that reflect the test scenario (e.g., `"invalid_credentials"`)
- Include an `"id"` field in each data record to use as pytest parametrize `ids`
- Keep data files small and focused — one file per feature area
- Load data files exclusively through the `load_test_data` fixture in `tests/conftest.py`

### DO NOT:
- Store real production credentials or PII in data files
- Hardcode data inside test functions — always load from here
- Use CSV for structured nested data — use JSON instead

---

> Last reviewed: 2026-06-26
