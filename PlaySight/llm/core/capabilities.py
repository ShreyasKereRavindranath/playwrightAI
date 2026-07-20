"""Provider capabilities — the vocabulary used to gracefully disable features."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Capability(str, Enum):
    """A feature a provider may or may not support.

    `LLMService` consults these to strip unsupported fields from a request
    instead of letting the provider error out.
    """

    CHAT = "chat"
    STREAMING = "streaming"
    SYSTEM_PROMPT = "system_prompt"
    TEMPERATURE = "temperature"
    MAX_TOKENS = "max_tokens"
    JSON_MODE = "json_mode"
    STRUCTURED_OUTPUT = "structured_output"
    TOOLS = "tools"
    VISION = "vision"
    EMBEDDINGS = "embeddings"
    REASONING = "reasoning"


@dataclass(frozen=True)
class ProviderCapabilities:
    """Immutable set of capabilities a provider advertises."""

    supported: frozenset[Capability]

    def has(self, capability: Capability) -> bool:
        return capability in self.supported

    def as_list(self) -> list[str]:
        return sorted(c.value for c in self.supported)

    @classmethod
    def of(cls, *capabilities: Capability) -> "ProviderCapabilities":
        return cls(frozenset(capabilities))
