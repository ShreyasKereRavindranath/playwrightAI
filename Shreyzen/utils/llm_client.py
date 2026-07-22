"""
Central LLM client — backward-compatibility shim over the provider abstraction.

Historically this wrapped the OpenAI SDK directly. It now delegates to
`llm.service.LLMService`, which routes to whichever provider the user selected
(OpenAI, Anthropic, Gemini, Ollama, LM Studio, or a custom endpoint). The public
surface — `complete`, `complete_json`, `available` — and the *non-raising*
failure contract (return "" / {} on error) are preserved exactly, so every
existing consumer (agents, ai_self_heal, ai_summary, llm_judge,
test_data_generator) keeps working with no changes.

New code should prefer `llm.service.get_service()` directly.
"""

import logging
from typing import Optional

from llm.service import LLMService, get_service

logger = logging.getLogger(__name__)

_SYSTEM_DEFAULT = (
    "You are an expert QA automation engineer helping maintain a Playwright Python test framework. "
    "Be concise, accurate, and always output valid Python or JSON as instructed."
)


class LLMClient:
    """Thin, provider-agnostic client used by all AI features."""

    def __init__(self, service: Optional[LLMService] = None):
        # `service` is injectable for tests; defaults to the shared instance.
        self._service = service or get_service()

    def complete(
        self,
        prompt: str,
        system: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 0.2,
    ) -> str:
        """Return a plain-text completion or empty string on failure."""
        if not self.available:
            return ""
        try:
            return self._service.complete_text(
                prompt,
                system=system or _SYSTEM_DEFAULT,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except Exception as exc:
            logger.error("LLM completion failed: %s", exc)
            return ""

    def complete_json(
        self,
        prompt: str,
        system: Optional[str] = None,
        model: Optional[str] = None,
    ) -> dict:
        """Return a parsed JSON dict from the LLM, or empty dict on failure."""
        if not self.available:
            return {}
        try:
            return self._service.complete_json(
                prompt, system=system or _SYSTEM_DEFAULT, model=model
            )
        except Exception as exc:
            logger.error("LLM JSON completion failed: %s", exc)
            return {}

    @property
    def available(self) -> bool:
        """True if the selected provider is configured and usable."""
        try:
            return self._service.available()
        except Exception:
            return False
