"""
AI Self-Healing Locators — Capability #3 (complete implementation)

When a Playwright TimeoutError fires on a locator, this module:
  1. Snapshots the live DOM (first 10 000 chars)
  2. Sends it to an LLM with the human-readable intent
  3. Returns a candidate Playwright locator string
  4. Caches the healing in data/healing_log.json for Page Object maintenance

Usage in BasePage:
    self.safe_click(locator, intent="click the checkout button")
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_HEAL_LOG = Path("data/healing_log.json")

_SYSTEM = (
    "You are a Playwright locator expert. A locator timed out. "
    "Given the page HTML and the intent, return ONE Playwright locator string. "
    "Priority: data-testid > aria-role > stable id > name attribute > CSS class. "
    "Never use XPath. Never use :nth-child. Return only the raw locator — no explanation, no code block."
)

_PROMPT = """
## Intent (what the test is trying to interact with)
{intent}

## Page HTML snapshot (first 10 000 chars)
{html}

Locator:"""


class AISelfHeal:
    """LLM-powered locator recovery with persistent healing log."""

    def __init__(self, page) -> None:
        self.page = page

    # ── Public API ────────────────────────────────────────────────────────────

    def heal(self, intent: str, page_html: str) -> Optional[str]:
        """Return a candidate locator string, or None if healing fails."""
        from utils.llm_client import LLMClient

        llm = LLMClient()
        # Provider-neutral gate: works with any configured provider (OpenAI,
        # Anthropic, Gemini, Ollama, LM Studio), not just OPENAI_API_KEY.
        if not llm.available:
            logger.error("AI healing enabled but no LLM provider is configured/usable.")
            return None

        candidate = llm.complete(
            prompt=_PROMPT.format(intent=intent, html=page_html[:10_000]),
            system=_SYSTEM,
            max_tokens=200,
            temperature=0,
        )

        candidate = self._clean(candidate)
        if not candidate:
            logger.warning("AI self-heal: LLM returned empty response for intent '%s'", intent)
            return None

        # Validate the candidate by attempting to create a locator
        try:
            self.page.locator(candidate).count()  # dry-run — doesn't wait
        except Exception as exc:
            logger.warning("AI self-heal: candidate '%s' is invalid: %s", candidate, exc)
            return None

        logger.warning("AI self-heal CANDIDATE: '%s' — UPDATE YOUR PAGE OBJECT", candidate)
        self._log_healing(intent, candidate)
        return candidate

    @staticmethod
    def _clean(candidate: Optional[str]) -> str:
        """Strip code fences / quotes / stray prose an LLM may wrap around the locator."""
        if not candidate:
            return ""
        text = candidate.strip()
        if text.startswith("```"):
            # drop the opening fence (optionally ```lang) and the closing fence
            lines = [ln for ln in text.splitlines() if not ln.strip().startswith("```")]
            text = "\n".join(lines).strip()
        text = text.splitlines()[0].strip() if text else ""
        # remove wrapping quotes if the model quoted the whole thing
        if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"', "`"):
            text = text[1:-1].strip()
        return text

    # ── Healing log ───────────────────────────────────────────────────────────

    def _log_healing(self, intent: str, candidate: str) -> None:
        """Append the healed locator to data/healing_log.json for human review."""
        _HEAL_LOG.parent.mkdir(parents=True, exist_ok=True)
        log: list = []
        if _HEAL_LOG.exists():
            try:
                log = json.loads(_HEAL_LOG.read_text())
            except Exception:
                log = []

        log.append({
            "timestamp": datetime.now().isoformat(),
            "intent": intent,
            "healed_locator": candidate,
            "status": "PENDING_REVIEW",
            "page_url": self.page.url,
        })
        _HEAL_LOG.write_text(json.dumps(log, indent=2))
        logger.info("Healing appended to %s", _HEAL_LOG)

    @staticmethod
    def get_pending_reviews() -> list:
        """Return all healing entries that still need Page Object updates."""
        if not _HEAL_LOG.exists():
            return []
        try:
            log = json.loads(_HEAL_LOG.read_text())
            return [e for e in log if e.get("status") == "PENDING_REVIEW"]
        except Exception:
            return []

    @staticmethod
    def mark_reviewed(intent: str) -> None:
        """Mark a healing entry as reviewed (call after updating the Page Object)."""
        if not _HEAL_LOG.exists():
            return
        try:
            log = json.loads(_HEAL_LOG.read_text())
            for entry in log:
                if entry.get("intent") == intent:
                    entry["status"] = "REVIEWED"
            _HEAL_LOG.write_text(json.dumps(log, indent=2))
        except Exception as exc:
            logger.warning("Could not mark healing as reviewed: %s", exc)
