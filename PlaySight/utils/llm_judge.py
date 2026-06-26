"""
LLM-as-Judge for Test Assertion Quality — Capability #12

Audits existing test files and scores them on:
  - Assertion completeness (are they actually verifying the scenario?)
  - Test independence (does it depend on other tests?)
  - Naming clarity
  - Missing edge cases
  - Trivial assertions that always pass

CLI: python tools/audit_tests.py --test-dir tests/
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are a senior QA architect performing a code review of automated tests. "
    "Be specific, constructive, and prioritise findings by impact. "
    "Respond only with valid JSON as instructed."
)

_PROMPT = """
Review this pytest test file for quality issues. Score it 0-100.

## File: {filename}

```python
{code}
```

## Scoring rubric:
- Assertion completeness: do assertions actually verify the described scenario? (25 pts)
- No trivial assertions: no `assert True` or assertions that always pass? (20 pts)
- Test independence: no inter-test dependencies? (20 pts)
- Naming clarity: names describe scenario + expected outcome? (15 pts)
- Edge case coverage: are boundary/negative cases present? (20 pts)

Return JSON:
{{
  "file": "{filename}",
  "score": <0-100>,
  "grade": "<A|B|C|D|F>",
  "summary": "<one sentence>",
  "findings": [
    {{
      "severity": "<critical|high|medium|low>",
      "test_name": "<function name or 'file-level'>",
      "issue": "<description>",
      "suggestion": "<how to fix>"
    }}
  ],
  "missing_coverage": ["<scenario not covered>"]
}}
"""

_GRADE = {range(90, 101): "A", range(75, 90): "B", range(60, 75): "C",
          range(45, 60): "D", range(0, 45): "F"}


def _letter(score: int) -> str:
    for r, g in _GRADE.items():
        if score in r:
            return g
    return "F"


class LLMJudge:
    """AI-powered test quality auditor."""

    def audit_file(self, test_file_path: str) -> Optional[dict]:
        """Audit a single test file. Returns quality report dict or None."""
        from utils.llm_client import LLMClient

        path = Path(test_file_path)
        if not path.exists():
            logger.error("Test file not found: %s", test_file_path)
            return None

        code = path.read_text(encoding="utf-8")
        llm  = LLMClient()
        if not llm.available:
            logger.warning("LLMJudge: OPENAI_API_KEY not set — skipping audit.")
            return None

        result = llm.complete_json(
            prompt=_PROMPT.format(filename=path.name, code=code[:8_000]),
            system=_SYSTEM,
        )
        if not result:
            return None

        # Ensure grade matches score
        if "score" in result:
            result["grade"] = _letter(result["score"])

        logger.info(
            "Audit [%s]: score=%s grade=%s findings=%d",
            path.name,
            result.get("score"),
            result.get("grade"),
            len(result.get("findings", [])),
        )
        return result

    def audit_directory(self, test_dir: str = "tests/") -> list:
        """Audit all test_*.py files in a directory. Returns list of reports."""
        test_path = Path(test_dir)
        files     = sorted(test_path.rglob("test_*.py"))
        reports   = []
        for f in files:
            logger.info("Auditing: %s", f)
            report = self.audit_file(str(f))
            if report:
                reports.append(report)
        return reports

    @staticmethod
    def save_report(reports: list, output_path: str = "logs_and_reports/audit_report.json") -> str:
        """Persist audit results to JSON."""
        dest = Path(output_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(reports, indent=2))
        logger.info("Audit report saved: %s", dest)
        return str(dest)
