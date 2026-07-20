"""Custom OpenAI-compatible endpoint provider.

Points the OpenAI SDK at any `base_url` (vLLM, LiteLLM, LocalAI, Together,
Groq, etc.). Inherits all request/response translation from OpenAIProvider;
only construction, metadata, and validation differ. The API key is optional —
many self-hosted servers ignore it.
"""

from __future__ import annotations

from ..config import ProviderConfig
from ..core.metadata import Availability, HealthResult, ProviderMetadata, ValidationResult
from ..registry import register_provider
from .openai_provider import OpenAIProvider


@register_provider("openai_compatible")
class OpenAICompatibleProvider(OpenAIProvider):
    def __init__(self, config: ProviderConfig):
        super().__init__(config)

    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            name="openai_compatible", label="Custom (OpenAI-compatible)", kind="cloud",
            requires_api_key=False, default_model=self._cfg.default_model or "",
            homepage="", description="Any OpenAI-compatible /v1 endpoint (vLLM, LiteLLM, Groq, …).",
        )

    def validate_config(self) -> ValidationResult:
        if not self._cfg.base_url:
            return ValidationResult(False, "OPENAI_COMPAT_BASE_URL is not set.")
        return ValidationResult(True, f"Endpoint {self._cfg.base_url}")

    def health_check(self) -> HealthResult:
        if not self._cfg.base_url:
            return HealthResult(Availability.NEEDS_CONFIG, "OPENAI_COMPAT_BASE_URL is not set.")
        try:
            ids = [m.id for m in getattr(self._client().models.list(), "data", [])][:20]
            return HealthResult(Availability.AVAILABLE, "Reachable.", models=ids)
        except Exception as exc:
            return HealthResult(Availability.UNREACHABLE, str(exc))

    def _client(self):
        from openai import OpenAI
        if not self._cfg.base_url:
            from ..core.errors import ConfigurationError
            raise ConfigurationError("OPENAI_COMPAT_BASE_URL is not set.")
        return OpenAI(api_key=self._cfg.api_key or "not-needed",
                      base_url=self._cfg.base_url, timeout=self._cfg.timeout)
