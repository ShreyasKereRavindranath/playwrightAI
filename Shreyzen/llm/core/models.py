"""Provider-neutral request/response value objects.

Nothing in this module imports a provider SDK. Every provider translates
to/from these types, so the rest of the application only ever sees them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Message:
    """A single chat message.

    `images` holds base64 data URIs or URLs; providers that lack the VISION
    capability ignore it.
    """

    role: str  # "system" | "user" | "assistant"
    content: str
    images: list[str] = field(default_factory=list)


@dataclass
class LLMRequest:
    """A normalized completion request.

    Fields the selected provider does not support are stripped by
    `LLMService` before the provider ever sees them (graceful degradation).
    """

    messages: list[Message]
    system: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    json_mode: bool = False
    json_schema: Optional[dict] = None
    tools: Optional[list[dict]] = None
    stream: bool = False
    reasoning: bool = False
    extra: dict = field(default_factory=dict)

    @classmethod
    def simple(cls, prompt: str, system: Optional[str] = None, **kw) -> "LLMRequest":
        """Convenience constructor for a single user turn."""
        return cls(messages=[Message(role="user", content=prompt)], system=system, **kw)


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class LLMResponse:
    text: str
    model: str = ""
    provider: str = ""
    usage: Usage = field(default_factory=Usage)
    finish_reason: str = ""
    raw: Any = None


@dataclass
class StreamChunk:
    text: str = ""
    done: bool = False
