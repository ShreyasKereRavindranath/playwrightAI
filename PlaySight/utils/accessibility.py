"""
Accessibility Audit — Capability #7

Injects axe-core into the live page and runs WCAG 2.1 AA checks.
Violations are saved as JSON and optionally fail the test.

axe-core is loaded from CDN. For offline environments, download it once:
    python tools/setup_framework.py --download-axe

Reports: logs_and_reports/a11y/run_{RUN_TS}/{test_name}.json
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_AXE_CDN    = "https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.9.1/axe.min.js"
_AXE_LOCAL  = Path(__file__).parent / "axe.min.js"
_A11Y_BASE  = Path("logs_and_reports/a11y")

_AXE_RUNNER = """
async () => {
    const results = await axe.run(document, {
        runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa'] }
    });
    return {
        violations: results.violations.map(v => ({
            id:          v.id,
            impact:      v.impact,
            description: v.description,
            help:        v.help,
            helpUrl:     v.helpUrl,
            nodes:       v.nodes.length
        })),
        passes:     results.passes.length,
        incomplete: results.incomplete.length
    };
}
"""


class AccessibilityAuditor:
    """Inject axe-core and run WCAG checks on the live page."""

    def __init__(self, run_ts: str):
        self._report_dir = _A11Y_BASE / f"run_{run_ts}"

    # ── Public API ────────────────────────────────────────────────────────────

    def audit(self, page, context_name: str) -> dict:
        """Run accessibility audit and return results dict.

        Args:
            page:         Live Playwright page object.
            context_name: Human-readable label (used for report filename).

        Returns dict with keys:
            violations  : list of violation dicts
            passes      : int
            incomplete  : int
            critical    : list (filtered violations)
            serious     : list
            report_path : str
        """
        from config.config import Config

        try:
            self._inject_axe(page)
        except Exception as exc:
            logger.warning("axe-core injection failed (%s): %s", context_name, exc)
            return {"violations": [], "passes": 0, "incomplete": 0,
                    "critical": [], "serious": [], "report_path": None}

        try:
            raw = page.evaluate(_AXE_RUNNER)
        except Exception as exc:
            logger.warning("axe.run() failed (%s): %s", context_name, exc)
            return {"violations": [], "passes": 0, "incomplete": 0,
                    "critical": [], "serious": [], "report_path": None}

        violations = raw.get("violations", [])
        critical   = [v for v in violations if v.get("impact") == "critical"]
        serious    = [v for v in violations if v.get("impact") == "serious"]
        moderate   = [v for v in violations if v.get("impact") == "moderate"]
        minor      = [v for v in violations if v.get("impact") == "minor"]

        report_path = self._save_report(context_name, raw)

        fail_levels = {s.strip() for s in Config.ACCESSIBILITY_FAIL_ON.lower().split(",")}
        blocking    = []
        if "critical" in fail_levels: blocking.extend(critical)
        if "serious"  in fail_levels: blocking.extend(serious)
        if "moderate" in fail_levels: blocking.extend(moderate)
        if "minor"    in fail_levels: blocking.extend(minor)

        if violations:
            logger.warning(
                "A11y [%s]: %d violation(s) — critical=%d, serious=%d, moderate=%d, minor=%d | report: %s",
                context_name, len(violations),
                len(critical), len(serious), len(moderate), len(minor),
                report_path,
            )
        else:
            logger.info("A11y [%s]: no violations found ✓", context_name)

        return {
            "violations":   violations,
            "passes":       raw.get("passes", 0),
            "incomplete":   raw.get("incomplete", 0),
            "critical":     critical,
            "serious":      serious,
            "report_path":  report_path,
            "blocking":     blocking,
        }

    # ── axe injection ─────────────────────────────────────────────────────────

    def _inject_axe(self, page) -> None:
        """Add axe-core script to the page; local file preferred over CDN."""
        if _AXE_LOCAL.exists():
            page.add_script_tag(path=str(_AXE_LOCAL))
        else:
            page.add_script_tag(url=_AXE_CDN)

    # ── Report persistence ────────────────────────────────────────────────────

    def _save_report(self, context_name: str, data: dict) -> Optional[str]:
        try:
            self._report_dir.mkdir(parents=True, exist_ok=True)
            safe = context_name.replace("::", "__").replace("/", "_").replace(" ", "_")
            path = self._report_dir / f"{safe}.json"
            path.write_text(json.dumps(data, indent=2))
            return str(path)
        except Exception as exc:
            logger.warning("Could not save a11y report: %s", exc)
            return None
