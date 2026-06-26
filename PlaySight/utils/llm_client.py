"""
Central LLM client — all AI capabilities route through here.

Supports OpenAI-compatible APIs. Handles retries, missing key gracefully,
and provides both plain-text and structured JSON completion modes.
"""

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_SYSTEM_DEFAULT = (
    "You are an expert QA automation engineer helping maintain a Playwright Python test framework. "
    "Be concise, accurate, and always output valid Python or JSON as instructed."
)


class LLMClient:
    """Thin wrapper around OpenAI chat completions used by all AI features."""

    def __init__(self):
        from config.config import Config
        self._api_key = Config.OPENAI_API_KEY
        self._model = Config.AI_MODEL
        self._max_tokens = Config.AI_MAX_TOKENS

    def _client(self):
        if not self._api_key:
            raise EnvironmentError(
                "OPENAI_API_KEY is not set. Set it in config/.env to use AI features."
            )
        from openai import OpenAI
        return OpenAI(api_key=self._api_key)

    def complete(
        self,
        prompt: str,
        system: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 0.2,
    ) -> str:
        """Return a plain-text LLM completion or empty string on failure."""
        try:
            response = self._client().chat.completions.create(
                model=model or self._model,
                messages=[
                    {"role": "system", "content": system or _SYSTEM_DEFAULT},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=max_tokens or self._max_tokens,
                temperature=temperature,
            )
            return response.choices[0].message.content.strip()
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
        system_msg = (system or _SYSTEM_DEFAULT) + "\nAlways respond with valid JSON only — no markdown, no prose."
        try:
            response = self._client().chat.completions.create(
                model=model or self._model,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=self._max_tokens,
                temperature=0.1,
                response_format={"type": "json_object"},
            )
            raw = response.choices[0].message.content.strip()
            return json.loads(raw)
        except Exception as exc:
            logger.error("LLM JSON completion failed: %s", exc)
            return {}

    @property
    def available(self) -> bool:
        """True if an API key is configured."""
        return bool(self._api_key)
