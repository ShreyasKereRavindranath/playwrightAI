"""Ports (abstract interfaces) for the LLM layer.

These are the only contracts the application depends on. Concrete providers,
local bootstrappers, and credential stores live in the infrastructure layer
and implement these — the dependency arrow always points inward.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, Iterator, Optional

from .capabilities import Capability, ProviderCapabilities
from .errors import CapabilityNotSupported
from .metadata import HealthResult, ProviderMetadata, ValidationResult
from .models import LLMRequest, LLMResponse, StreamChunk


class LLMProvider(ABC):
    """The common interface every provider implements."""

    @abstractmethod
    def metadata(self) -> ProviderMetadata: ...

    @abstractmethod
    def capabilities(self) -> ProviderCapabilities: ...

    @abstractmethod
    def validate_config(self) -> ValidationResult:
        """Cheap, offline check: are required settings present / well-formed?"""

    @abstractmethod
    def health_check(self) -> HealthResult:
        """Live probe: is the provider reachable and ready right now?"""

    @abstractmethod
    def complete(self, request: LLMRequest) -> LLMResponse:
        """Return a single completion."""

    # -- optional capabilities with safe defaults ----------------------------

    def stream(self, request: LLMRequest) -> Iterator[StreamChunk]:
        """Stream tokens. Default: emit the full completion as one final chunk."""
        response = self.complete(request)
        yield StreamChunk(text=response.text, done=True)

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return embeddings. Default: unsupported."""
        raise CapabilityNotSupported(
            f"{self.metadata().name} does not support embeddings"
        )

    # -- convenience ---------------------------------------------------------

    def supports(self, capability: Capability) -> bool:
        return self.capabilities().has(capability)


class LocalBootstrapper(ABC):
    """Detects, installs, starts, and prepares a local LLM runtime."""

    @abstractmethod
    def ensure_ready(
        self, model: str, progress: Optional[Callable[[str], None]] = None
    ) -> HealthResult:
        """Make the runtime + model ready to serve, reporting progress."""


class CredentialStore(ABC):
    """Abstracts where secrets come from (env, file, OS keyring)."""

    @abstractmethod
    def get(self, key: str) -> Optional[str]: ...

    @abstractmethod
    def set(self, key: str, value: str) -> None: ...
