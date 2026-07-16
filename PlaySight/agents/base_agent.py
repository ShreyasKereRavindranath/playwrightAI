"""
BaseAgent — shared plumbing for the Planner, Generator, and Healer.

Responsibilities:
- Own a single LLMClient instance and expose whether the LLM is usable.
- Decide per-call whether to use the LLM path or the deterministic offline
  fallback (governed by the `offline` flag and key availability).
- Provide small, well-tested text helpers (slugify, snake_case, code fence
  stripping) that every agent needs.

Design rule: agents NEVER crash because a key is missing. If the LLM is
unavailable or errors out, the agent falls back to its offline implementation
and records generated_by="offline" on the result so callers can tell.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from utils.llm_client import LLMClient

logger = logging.getLogger(__name__)


class BaseAgent:
    """Common base for all PlaySight AI agents."""

    def __init__(self, llm: Optional[LLMClient] = None, offline: bool = False) -> None:
        """
        Args:
            llm: inject a client (useful for tests); defaults to a fresh LLMClient.
            offline: force the deterministic path even when a key is configured.
                     Handy for reproducible demos and CI without network access.
        """
        self._llm = llm or LLMClient()
        self._forced_offline = offline

    @property
    def use_llm(self) -> bool:
        """True when we should attempt the LLM path for this agent."""
        return (not self._forced_offline) and self._llm.available

    @property
    def mode(self) -> str:
        return "llm" if self.use_llm else "offline"

    # ── Text helpers (shared by every agent) ──────────────────────────────────

    @staticmethod
    def slugify(text: str, max_len: int = 60) -> str:
        """'User can log in!' → 'user_can_log_in' (safe for identifiers/filenames)."""
        slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
        return slug[:max_len].rstrip("_") or "scenario"

    @staticmethod
    def strip_code_fences(text: str) -> str:
        """Remove ```python / ``` fences an LLM sometimes wraps code in."""
        if not text:
            return ""
        fence = re.match(r"^\s*```[a-zA-Z]*\n(.*)\n```\s*$", text, re.DOTALL)
        return fence.group(1) if fence else text.strip()
