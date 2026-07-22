"""
AI Test Summary Generator — Capability #1

Reads the pytest session outcome and generates a plain-English executive
summary using an LLM. Injected at the top of the HTML report and also
returned as a string for Slack/Teams notifications.

Requires: OPENAI_API_KEY + AI_SUMMARY=true in config/.env
"""

import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are a senior QA lead writing a brief executive summary for a software test run. "
    "Write for a business audience — clear, concise, actionable. "
    "Maximum 5 sentences. Mention what failed and why it matters. "
    "If all tests passed, say so confidently. Never use bullet points."
)

_PROMPT = """
Test run completed. Here are the results:

Total tests  : {total}
Passed       : {passed}
Failed       : {failed}
Errors       : {error}
Skipped      : {skipped}
Duration     : {duration:.1f}s
Run ID       : {run_ts}

Failed tests :
{failed_list}

Flaky tests (historically unstable):
{flaky_list}

Write the executive summary now:
"""


class AISummaryGenerator:

    def generate(
        self,
        passed: int,
        failed: int,
        error: int,
        skipped: int,
        duration: float,
        run_ts: str,
        failed_tests: Optional[list] = None,
        flaky_tests:  Optional[list] = None,
    ) -> str:
        """Return a plain-English summary string, or empty string if LLM unavailable."""
        from utils.llm_client import LLMClient

        llm = LLMClient()
        if not llm.available:
            logger.info("AI_SUMMARY skipped — OPENAI_API_KEY not set.")
            return ""

        failed_list = "\n".join(
            f"  - {t}" for t in (failed_tests or [])
        ) or "  (none)"

        flaky_list = "\n".join(
            f"  - {t['test_id']} ({t['flake_rate']*100:.0f}% flake)"
            for t in (flaky_tests or [])
        ) or "  (none)"

        prompt = _PROMPT.format(
            total    = passed + failed + error + skipped,
            passed   = passed,
            failed   = failed,
            error    = error,
            skipped  = skipped,
            duration = duration,
            run_ts   = run_ts,
            failed_list = failed_list,
            flaky_list  = flaky_list,
        )

        summary = llm.complete(prompt=prompt, system=_SYSTEM, max_tokens=300, temperature=0.3)
        logger.info("AI summary generated (%d chars).", len(summary))
        return summary

    def inject_into_html_report(self, summary: str, report_path: str) -> None:
        """Prepend the AI summary banner to the pytest-html report."""
        path = Path(report_path)
        if not path.exists() or not summary:
            return

        html = path.read_text(encoding="utf-8")
        banner = f"""
<div style="background:#1a1a2e;color:#e8e8e8;padding:18px 24px;border-left:6px solid #4fc3f7;
            margin:0;font-family:system-ui,sans-serif;font-size:14px;line-height:1.6;">
  <strong style="color:#4fc3f7;font-size:15px;">🤖 AI Executive Summary</strong><br>
  {_escape(summary)}
</div>
"""
        # Insert right after <body>
        patched = re.sub(r"(<body[^>]*>)", r"\1" + banner, html, count=1)
        if patched != html:
            path.write_text(patched, encoding="utf-8")
            logger.info("AI summary injected into %s", report_path)
        else:
            logger.warning("Could not find <body> in report to inject AI summary.")


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\n", "<br>")
    )
