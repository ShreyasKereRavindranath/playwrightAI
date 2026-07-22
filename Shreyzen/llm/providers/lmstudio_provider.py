"""LM Studio provider — OpenAI-compatible transport with auto-connect."""

from __future__ import annotations

import logging

from ..config import ProviderConfig
from ..core.errors import ProviderUnavailable
from ..core.metadata import Availability, HealthResult, ProviderMetadata, ValidationResult
from ..core.models import LLMRequest, LLMResponse
from ..local.lmstudio_bootstrap import LMStudioBootstrapper
from ..registry import register_provider
from .openai_compatible_provider import OpenAICompatibleProvider

logger = logging.getLogger("llm.lmstudio")


@register_provider("lmstudio")
class LMStudioProvider(OpenAICompatibleProvider):
    def __init__(self, config: ProviderConfig):
        if not config.base_url:
            config.base_url = "http://127.0.0.1:1234/v1"
        super().__init__(config)
        self._boot = LMStudioBootstrapper(config.base_url)

    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            name="lmstudio", label="LM Studio (local)", kind="local",
            requires_api_key=False, default_model=self._cfg.default_model or "",
            homepage="https://lmstudio.ai",
            description="Local models via LM Studio's OpenAI-compatible server.",
        )

    def validate_config(self) -> ValidationResult:
        return ValidationResult(True, f"LM Studio endpoint {self._cfg.base_url}")

    def health_check(self) -> HealthResult:
        if self._boot.is_running():
            return HealthResult(Availability.AVAILABLE, "LM Studio reachable.",
                                models=self._boot.loaded_models())
        return self._boot.ensure_ready(self._cfg.default_model, progress=logger.info)

    def complete(self, request: LLMRequest) -> LLMResponse:
        self._ensure(request)
        return super().complete(request)

    # -- internals -----------------------------------------------------------

    def _ensure(self, request: LLMRequest) -> None:
        if not self._boot.is_running():
            result = self._boot.ensure_ready(self._cfg.default_model, progress=logger.info)
            if result.availability != Availability.AVAILABLE:
                raise ProviderUnavailable(result.detail)
        # LM Studio serves whichever model is loaded; if none configured, use it.
        if not request.model and not self._cfg.default_model:
            loaded = self._boot.loaded_models()
            if loaded:
                request.model = loaded[0]
