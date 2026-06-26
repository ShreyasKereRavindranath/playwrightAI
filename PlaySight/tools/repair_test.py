#!/usr/bin/env python3
"""
Auto Test Repair on CI Failure — Capability #13

Reads the pytest failure log, extracts error context, and proposes
a concrete fix using an LLM. Outputs a unified diff and optionally
applies it. Designed for CI pipelines.

Usage:
    python tools/repair_test.py                           # uses latest pytest.log
    python tools/repair_test.py --log path/to/pytest.log
    python tools/repair_test.py --log pytest.log --apply  # apply the fix
    python tools/repair_test.py --error "TimeoutError on [data-test='submit']" --file pages/checkout_page.py
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.llm_client import LLMClient

_SYSTEM = (
    "You are a senior QA automation engineer fixing failing Playwright Python tests. "
    "Analyse the error, identify the root cause, and produce a minimal targeted fix. "
    "Return ONLY a unified diff (--- a/file +++ b/file format) or a Python code block "
    "if the fix is a full file replacement. No prose, no explanations."
)

_PROMPT = """
A Playwright test is failing. Analyse the error and suggest a fix.

## Error context from log:
{error_context}

## Relevant source file ({filename}):
```python
{source_code}
```

## Framework conventions:
- Locators must be @property methods in Page Object classes
- Use data-testid > aria role > stable id > name > CSS
- Never use XSS / sleep / hardcoded waits
- All interactions go through BasePage helpers (self.click, self.fill etc.)
- Assertions use expect() from playwright.sync_api in test files only

Propose the minimal fix as a unified diff:
"""


def extract_errors(log_text: str) -> list:
    """Extract FAILED test blocks from pytest log output."""
    pattern = re.compile(
        r"(FAILED .+?)\n(.+?)(?=FAILED|\Z)",
        re.DOTALL,
    )
    errors = []
    for match in pattern.finditer(log_text):
        errors.append({
            "test": match.group(1).strip(),
            "body": match.group(2).strip()[:3_000],
        })
    return errors or [{"test": "unknown", "body": log_text[:3_000]}]


def find_relevant_file(error_body: str) -> tuple:
    """Try to identify the failing file from the traceback."""
    matches = re.findall(r'File "([^"]+\.py)"', error_body)
    for path in reversed(matches):  # innermost frame first
        p = Path(path)
        if p.exists() and ("pages/" in path or "tests/" in path):
            return str(p), p.read_text()
    return "", ""


def main():
    parser = argparse.ArgumentParser(description="AI-powered test repair on CI failure")
    parser.add_argument("--log",   default="logs_and_reports/pytest.log",
                        help="Path to pytest log file")
    parser.add_argument("--error", default="", help="Paste error text directly")
    parser.add_argument("--file",  default="", help="Source file to repair")
    parser.add_argument("--apply", action="store_true",
                        help="Attempt to apply the fix using patch")
    args = parser.parse_args()

    llm = LLMClient()
    if not llm.available:
        print("ERROR: OPENAI_API_KEY not set in config/.env")
        sys.exit(1)

    if args.error:
        errors = [{"test": "manual input", "body": args.error}]
        src_file, src_code = args.file, Path(args.file).read_text() if args.file else ("", "")
    else:
        log_path = Path(args.log)
        if not log_path.exists():
            print(f"ERROR: Log file not found: {log_path}")
            sys.exit(1)
        log_text = log_path.read_text(errors="ignore")
        errors   = extract_errors(log_text)
        src_file, src_code = find_relevant_file(log_text)

    print(f"\n🔧 Analysing {len(errors)} failure(s)...\n")

    for i, err in enumerate(errors[:3], 1):  # cap at 3 failures
        print(f"── Failure {i}: {err['test']}")
        filename = src_file or "unknown"
        code     = src_code or "(source not found — add --file flag)"

        fix = llm.complete(
            prompt=_PROMPT.format(
                error_context=err["body"][:2_000],
                filename=filename,
                source_code=code[:4_000],
            ),
            system=_SYSTEM,
            max_tokens=1_000,
        )

        print(fix)

        if args.apply and fix and filename and Path(filename).exists():
            patch_file = Path("logs_and_reports/repair.patch")
            patch_file.write_text(fix)
            result = subprocess.run(
                ["patch", "-p0", str(filename)],
                input=fix, text=True, capture_output=True,
            )
            if result.returncode == 0:
                print(f"\n✅ Patch applied to {filename}")
            else:
                print(f"\n⚠  Auto-apply failed: {result.stderr}. Review fix manually.")
        print()

    print("Review all proposed fixes carefully before merging.")


if __name__ == "__main__":
    main()
