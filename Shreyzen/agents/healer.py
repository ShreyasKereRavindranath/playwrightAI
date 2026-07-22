"""
Healer — Capability: AI Test Healing / Auto-Repair.

Reads a pytest failure (a log file, or an error string + source file), classifies
the failure, and proposes a concrete, reviewable fix. It complements the runtime
locator recovery in utils/ai_self_heal.py: that heals *during* a run, while this
agent heals *after* a run (in CI or locally) and can emit a unified diff.

    result = Healer().heal_from_log("logs_and_reports/pytest.log")
    if result.fix_kind == "diff":
        Healer().apply(result)      # optional — writes the patched file

The offline rule engine handles the common Playwright failure modes deterministically
(strict-mode violations, hardcoded waits, fragile locators, URL/assert mismatches),
so the healer is useful without an API key. When a key is present it additionally
asks the LLM for a richer diff.
"""

from __future__ import annotations

import difflib
import logging
import re
from pathlib import Path
from typing import List, Optional, Tuple

from agents.base_agent import BaseAgent
from agents.schemas import HealDiagnosis, HealResult

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are a senior QA automation engineer repairing failing Playwright Python tests. "
    "Identify the root cause and produce a minimal, targeted fix. Return ONLY a unified "
    "diff (--- a/file / +++ b/file) — no prose."
)

_PROMPT = """
A Playwright test failed. Propose a minimal fix as a unified diff.

## Diagnosis
Category: {category}
Root cause: {root_cause}
Failing symbol: {symbol}

## Error context
{error_context}

## Source file ({filename})
```python
{source_code}
```

## Rules
- Locators: data-testid > aria role > stable id > name > semantic CSS. Never XPath/:nth-child/raw text.
- Never introduce time.sleep or wait_for_timeout.
- Keep the change surgical.
Return the unified diff only:
"""

# ── Failure classification patterns (ordered — first match wins) ──────────────
_PATTERNS: List[Tuple[str, str]] = [
    ("strict_mode_violation", r"strict mode violation"),
    ("locator_timeout", r"TimeoutError|Timeout \d+ms exceeded"),
    ("url_mismatch", r"to_have_url|Expected URL|not_to_have_url"),
    ("assertion_mismatch", r"AssertionError|to_have_text|to_contain_text|to_be_visible"),
    ("import_or_collection_error", r"ImportError|ModuleNotFoundError|SyntaxError|errors during collection"),
    ("hardcoded_wait", r"time\.sleep|wait_for_timeout"),
]

_FRAGILE_LOCATOR = re.compile(r"(//|nth-child|\btext=)")


class Healer(BaseAgent):
    """pytest failure → diagnosis + fix proposal."""

    # ── Public API ──────────────────────────────────────────────────────────────

    def heal(
        self,
        error_text: str,
        source_file: Optional[str] = None,
        source_code: Optional[str] = None,
        test_id: str = "unknown",
    ) -> HealResult:
        """Diagnose one failure and propose a fix."""
        if source_file and source_code is None and Path(source_file).exists():
            source_code = Path(source_file).read_text(encoding="utf-8", errors="ignore")

        diagnosis = self.diagnose(error_text, source_file, source_code, test_id)

        # Prefer the LLM diff when we have both a key and the source to patch.
        if self.use_llm and source_code:
            fix = self._heal_llm(diagnosis, error_text, source_file or "unknown", source_code)
            if fix:
                return fix

        return self._heal_offline(diagnosis, source_code)

    def heal_from_log(self, log_path: str, max_failures: int = 3) -> List[HealResult]:
        """Parse a pytest log and heal each failure (capped for sanity)."""
        text = Path(log_path).read_text(encoding="utf-8", errors="ignore")
        results: List[HealResult] = []
        for failure in self._split_failures(text)[:max_failures]:
            src_file, src_code = self._locate_source(failure["body"])
            results.append(self.heal(
                error_text=failure["body"],
                source_file=src_file or None,
                source_code=src_code or None,
                test_id=failure["test"],
            ))
        return results

    def apply(self, result: HealResult) -> bool:
        """Apply a unified-diff fix to disk. Returns True on success."""
        if result.fix_kind != "diff" or not result.suggested_fix:
            logger.warning("Healer.apply: nothing to apply (fix_kind=%s).", result.fix_kind)
            return False
        target = result.diagnosis.file
        if not target or not Path(target).exists():
            logger.warning("Healer.apply: target file missing: %s", target)
            return False
        patched = self._apply_unified_diff(Path(target).read_text(encoding="utf-8"), result.suggested_fix)
        if patched is None:
            logger.warning("Healer.apply: diff did not apply cleanly to %s.", target)
            return False
        Path(target).write_text(patched, encoding="utf-8")
        result.applied = True
        logger.info("Healer.apply: patched %s", target)
        return True

    # ── Diagnosis ─────────────────────────────────────────────────────────────

    def diagnose(
        self,
        error_text: str,
        source_file: Optional[str] = None,
        source_code: Optional[str] = None,
        test_id: str = "unknown",
    ) -> HealDiagnosis:
        category = "unknown"
        for name, pattern in _PATTERNS:
            if re.search(pattern, error_text, re.IGNORECASE):
                category = name
                break

        symbol = self._extract_locator(error_text)
        root_cause, confidence = self._explain(category, symbol, error_text)

        return HealDiagnosis(
            category=category,
            test_id=test_id,
            file=source_file,
            failing_symbol=symbol,
            root_cause=root_cause,
            confidence=confidence,
        )

    # ── Offline rule-based fix ──────────────────────────────────────────────────

    def _heal_offline(self, dx: HealDiagnosis, source_code: Optional[str]) -> HealResult:
        symbol = dx.failing_symbol

        if dx.category == "strict_mode_violation":
            # Deterministic, correct fix when we can see the offending locator call.
            diff = self._diff_add_first(dx.file, source_code, symbol) if (source_code and symbol) else None
            if diff:
                return HealResult(
                    diagnosis=dx, generated_by="rule", fix_kind="diff", suggested_fix=diff,
                    explanation="Locator matched multiple elements. Narrowed to the first match "
                                "with .first. If the wrong element is chosen, tighten the locator "
                                "with a data-test attribute instead.",
                )
            return HealResult(
                diagnosis=dx, generated_by="rule", fix_kind="locator",
                suggested_fix=f"# Narrow the locator so it resolves to one element, e.g. "
                              f"page.locator({symbol!r}).first  or add a data-test attribute.",
                explanation=f"Locator '{symbol or 'the locator'}' matched multiple elements (strict "
                            "mode). Disambiguate with .first or a more specific selector in the Page Object.",
            )

        if dx.category == "hardcoded_wait":
            diff = self._diff_remove_hardcoded_waits(dx.file, source_code) if source_code else None
            if diff:
                return HealResult(
                    diagnosis=dx, generated_by="rule", fix_kind="diff", suggested_fix=diff,
                    explanation="Removed hardcoded wait(s). Replace with an explicit condition, e.g. "
                                "expect(locator).to_be_visible() or page.wait_for_url(...).",
                )
            return HealResult(
                diagnosis=dx, generated_by="rule", fix_kind="none",
                explanation="A hardcoded wait (time.sleep / wait_for_timeout) is causing flakiness. "
                            "Replace it with an explicit Playwright condition such as "
                            "expect(locator).to_be_visible() or page.wait_for_load_state().",
            )

        if dx.category == "locator_timeout":
            if symbol and _FRAGILE_LOCATOR.search(symbol):
                return HealResult(
                    diagnosis=dx, generated_by="rule", fix_kind="locator",
                    suggested_fix=self._recommend_locator(symbol),
                    explanation=f"Locator '{symbol}' uses a fragile strategy. Replace it in the Page "
                                "Object with a data-test / role-based locator (see suggestion).",
                )
            return HealResult(
                diagnosis=dx, generated_by="rule", fix_kind="none",
                explanation=f"Element for '{symbol or 'the locator'}' was never found within the timeout. "
                            "Verify the locator still matches the current DOM, that navigation "
                            "reached the expected page, and that no overlay is intercepting it. "
                            "Enable ENABLE_AI_HEALING for runtime recovery, or update the Page Object.",
            )

        if dx.category == "url_mismatch":
            return HealResult(
                diagnosis=dx, generated_by="rule", fix_kind="none",
                explanation="URL assertion failed. Confirm the expected route and that the preceding "
                            "action actually navigated (add a wait_for_url before asserting).",
            )

        if dx.category == "assertion_mismatch":
            return HealResult(
                diagnosis=dx, generated_by="rule", fix_kind="none",
                explanation="Assertion mismatch: the observed value differs from the expected. Check "
                            "whether the app behaviour or the test data changed, then update whichever "
                            "is stale — do not weaken the assertion to make it pass.",
            )

        if dx.category == "import_or_collection_error":
            return HealResult(
                diagnosis=dx, generated_by="rule", fix_kind="none",
                explanation="Collection/import error — the test file failed to load. Run "
                            "`pytest --co` to see the traceback, and fix the import or syntax error.",
            )

        return HealResult(
            diagnosis=dx, generated_by="rule", fix_kind="none",
            explanation="Could not classify this failure automatically. Inspect the traceback and the "
                        "Playwright trace under logs_and_reports/.",
        )

    # ── LLM fix ─────────────────────────────────────────────────────────────────

    def _heal_llm(self, dx: HealDiagnosis, error_text: str, filename: str, source_code: str) -> Optional[HealResult]:
        diff = self._llm.complete(
            prompt=_PROMPT.format(
                category=dx.category,
                root_cause=dx.root_cause,
                symbol=dx.failing_symbol or "n/a",
                error_context=error_text[:2000],
                filename=filename,
                source_code=source_code[:4000],
            ),
            system=_SYSTEM,
            max_tokens=1000,
        )
        diff = self.strip_code_fences(diff)
        if not diff or "---" not in diff:
            return None
        return HealResult(
            diagnosis=dx, generated_by="llm", fix_kind="diff", suggested_fix=diff,
            explanation="LLM-proposed fix. Review the diff before applying.",
        )

    # ── Explanations ─────────────────────────────────────────────────────────────

    @staticmethod
    def _explain(category: str, symbol: Optional[str], error_text: str) -> Tuple[str, float]:
        table = {
            "strict_mode_violation": (f"Locator '{symbol}' resolved to more than one element.", 0.9),
            "locator_timeout": (f"Locator '{symbol}' was not found/actionable before the timeout.", 0.8),
            "url_mismatch": ("The page URL did not match the expected pattern.", 0.7),
            "assertion_mismatch": ("An expect() assertion failed — observed value ≠ expected.", 0.6),
            "hardcoded_wait": ("A hardcoded wait was used, causing flakiness.", 0.85),
            "import_or_collection_error": ("The test module failed to import/collect.", 0.9),
            "unknown": ("Failure could not be classified from the log.", 0.2),
        }
        return table.get(category, table["unknown"])

    # ── Log / source parsing ──────────────────────────────────────────────────

    @staticmethod
    def _split_failures(log_text: str) -> List[dict]:
        """Split a pytest log into per-failure blocks with their tracebacks.

        Prefers the `=== FAILURES ===` section, whose blocks are delimited by
        underscore headers (`____ test_name ____`) and carry the real traceback.
        Falls back to the summary `FAILED ...` lines, then the whole log.
        """
        section = re.search(
            r"=+ FAILURES =+\n(.*?)(?=\n=+ (?:short test summary|warnings summary|slowest|\d+ (?:passed|failed))|\Z)",
            log_text, re.DOTALL,
        )
        if section:
            body = section.group(1)
            # Split on the underscore headers that name each failing test.
            # Prepend a newline so the very first header is also treated as a delimiter.
            parts = re.split(r"\n_{3,} (.+?) _{3,}\n", "\n" + body)
            failures: List[dict] = []
            # parts = [pre, name1, body1, name2, body2, ...]
            for i in range(1, len(parts) - 1, 2):
                failures.append({"test": parts[i].strip(), "body": parts[i + 1].strip()[:3000]})
            if failures:
                return failures

        # Fallback: summary lines (no traceback, but still classifiable by message).
        summary = re.findall(r"^(FAILED .+)$", log_text, re.MULTILINE)
        if summary:
            return [{"test": s.strip(), "body": log_text[:3000]} for s in summary[:1]]

        return [{"test": "unknown", "body": log_text[:3000]}]

    @staticmethod
    def _locate_source(error_body: str) -> Tuple[str, str]:
        """Find the innermost framework .py file referenced in the traceback."""
        matches = re.findall(r'File "([^"]+\.py)"', error_body)
        for path in reversed(matches):
            p = Path(path)
            if p.exists() and ("pages/" in path or "tests/" in path):
                return str(p), p.read_text(encoding="utf-8", errors="ignore")
        return "", ""

    @staticmethod
    def _extract_locator(error_text: str) -> Optional[str]:
        # pytest logs escape quotes (\"), so normalize before matching. Prefer the
        # selector inside locator("...") — non-greedy so it stops at the first close.
        text = error_text.replace('\\"', '"')
        for pat in (
            r'locator\("(.+?)"\)',
            r"locator\('(.+?)'\)",
            r"(get_by_\w+\([^)]*\))",
        ):
            m = re.search(pat, text)
            if m:
                return m.group(1).strip()
        return None

    @staticmethod
    def _recommend_locator(fragile: str) -> str:
        if "//" in fragile:
            return "# Replace XPath with: page.get_by_role(<role>, name=<accessible name>)"
        if "nth-child" in fragile:
            return "# Replace :nth-child with a data-test attribute or .filter(has_text=...)"
        if "text=" in fragile:
            return "# Replace raw text= with get_by_role(..., name=...) or a data-test attribute"
        return "# Prefer page.locator('[data-test=\"...\"]')"

    # ── Diff builders ─────────────────────────────────────────────────────────

    @staticmethod
    def _diff_add_first(path: Optional[str], source: str, selector: str) -> Optional[str]:
        """Append .first to the locator/get_by_* call that uses `selector`.

        Turns e.g.  self.page.locator('.btn')  →  self.page.locator('.btn').first
        (a Locator method call), rather than mutating the selector string itself.
        """
        for line in source.splitlines():
            for call in re.findall(r"(?:locator|get_by_\w+)\([^\n]*?\)", line):
                if selector in call:
                    return Healer._unified(path or "source.py", source,
                                           source.replace(call, f"{call}.first", 1))
        return None

    @staticmethod
    def _diff_remove_hardcoded_waits(path: Optional[str], source: str) -> Optional[str]:
        lines = source.splitlines(keepends=True)
        kept = [ln for ln in lines if not re.search(r"time\.sleep\(|wait_for_timeout\(", ln)]
        if len(kept) == len(lines):
            return None
        return Healer._unified(path or "source.py", "".join(lines), "".join(kept))

    @staticmethod
    def _unified(path: str, before: str, after: str) -> str:
        diff = difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}", tofile=f"b/{path}",
        )
        return "".join(diff)

    @staticmethod
    def _apply_unified_diff(source: str, diff: str) -> Optional[str]:
        """Apply the single-hunk diffs this healer emits.

        We only produce two shapes, so we handle exactly those:
          (a) 1:1 line replacement (e.g. locator → locator.first)
          (b) pure line removal (e.g. hardcoded waits)
        Returns None if the diff doesn't match the source (→ manual review).
        """
        removals = [ln[1:].rstrip("\n") for ln in diff.splitlines()
                    if ln.startswith("-") and not ln.startswith("---")]
        additions = [ln[1:].rstrip("\n") for ln in diff.splitlines()
                     if ln.startswith("+") and not ln.startswith("+++")]

        # (a) balanced replacement — swap each old line for its new counterpart.
        if additions and len(removals) == len(additions):
            text = source
            for old, new in zip(removals, additions):
                if old not in text:
                    return None
                text = text.replace(old, new, 1)
            return text

        # (b) pure removal — drop the removed lines.
        if removals and not additions:
            targets = set(removals)
            kept = [ln for ln in source.splitlines(keepends=True) if ln.rstrip("\n") not in targets]
            return "".join(kept)

        return None
