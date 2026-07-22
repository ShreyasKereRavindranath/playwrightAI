"""Anthropic Claude provider.

Notes grounded in the current Claude API (not memory):
  * `system` is a top-level parameter, not a message role.
  * `max_tokens` is required — always sent.
  * `temperature` is NOT advertised as a capability: current Claude models
    (Opus 4.7+/Sonnet 5) reject sampling params, so `LLMService` strips it.
  * Reasoning uses adaptive thinking (`thinking={"type": "adaptive"}`).
  * Anthropic has no embeddings endpoint — EMBEDDINGS is not advertised.
  * Structured output uses `output_config.format`; there is no `json_object`
    mode, so free-form JSON relies on a system instruction + parsing.
The `anthropic` SDK is imported lazily.
"""

from __future__ import annotations

from typing import Iterator

from ..config import ProviderConfig
from ..core.capabilities import Capability, ProviderCapabilities
from ..core.errors import ProviderAPIError, RetryableError
from ..core.interfaces import LLMProvider
from ..core.metadata import Availability, HealthResult, ProviderMetadata, ValidationResult
from ..core.models import LLMRequest, LLMResponse, StreamChunk, Usage
from ..registry import register_provider


@register_provider("anthropic")
class AnthropicProvider(LLMProvider):
    def __init__(self, config: ProviderConfig):
        self._cfg = config

    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            name="anthropic", label="Anthropic Claude", kind="cloud",
            requires_api_key=True, default_model=self._cfg.default_model or "claude-opus-4-8",
            homepage="https://platform.claude.com",
            description="Anthropic Claude models via the official SDK.",
        )

    def capabilities(self) -> ProviderCapabilities:
        # No TEMPERATURE (rejected by current models) and no EMBEDDINGS (no endpoint).
        return ProviderCapabilities.of(
            Capability.CHAT, Capability.STREAMING, Capability.SYSTEM_PROMPT,
            Capability.MAX_TOKENS, Capability.STRUCTURED_OUTPUT, Capability.TOOLS,
            Capability.VISION, Capability.REASONING,
        )

    def validate_config(self) -> ValidationResult:
        if not self._cfg.api_key:
            return ValidationResult(False, "ANTHROPIC_API_KEY is not set.")
        return ValidationResult(True, "API key present.")

    def health_check(self) -> HealthResult:
        if not self._cfg.api_key:
            return HealthResult(Availability.NEEDS_CONFIG, "ANTHROPIC_API_KEY is not set.")
        try:
            client = self._client()
            models = client.models.list()
            ids = [m.id for m in getattr(models, "data", [])][:20]
            return HealthResult(Availability.AVAILABLE, "Reachable.", models=ids)
        except Exception as exc:
            return HealthResult(Availability.UNREACHABLE, str(exc))

    def complete(self, request: LLMRequest) -> LLMResponse:
        client = self._client()
        kwargs = self._build_kwargs(request)
        try:
            resp = client.messages.create(**kwargs)
        except Exception as exc:
            raise self._map_error(exc)
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        usage = getattr(resp, "usage", None)
        return LLMResponse(
            text=text.strip(), model=resp.model, provider="anthropic",
            usage=Usage(getattr(usage, "input_tokens", 0) or 0,
                        getattr(usage, "output_tokens", 0) or 0),
            finish_reason=getattr(resp, "stop_reason", "") or "", raw=resp,
        )

    def stream(self, request: LLMRequest) -> Iterator[StreamChunk]:
        client = self._client()
        kwargs = self._build_kwargs(request)
        try:
            with client.messages.stream(**kwargs) as stream:
                for text in stream.text_stream:
                    yield StreamChunk(text=text)
            yield StreamChunk(done=True)
        except Exception as exc:
            raise self._map_error(exc)

    # -- internals -----------------------------------------------------------

    def _client(self):
        import anthropic
        if not self._cfg.api_key:
            raise ProviderAPIError("ANTHROPIC_API_KEY is not set.")
        return anthropic.Anthropic(api_key=self._cfg.api_key, timeout=self._cfg.timeout)

    def _build_kwargs(self, request: LLMRequest) -> dict:
        messages = []
        for m in request.messages:
            if m.role == "system":
                continue  # system is a top-level param on Anthropic
            if m.images:
                content = [{"type": "text", "text": m.content}]
                for img in m.images:
                    if img.startswith("http"):
                        content.append({"type": "image", "source": {"type": "url", "url": img}})
                messages.append({"role": m.role, "content": content})
            else:
                messages.append({"role": m.role, "content": m.content})

        kwargs: dict = {
            "model": request.model or self._cfg.default_model or "claude-opus-4-8",
            "max_tokens": request.max_tokens or self._cfg.max_tokens,
            "messages": messages,
        }
        if request.system:
            kwargs["system"] = request.system
        if request.reasoning:
            kwargs["thinking"] = {"type": "adaptive"}
        if request.json_schema:
            kwargs["output_config"] = {"format": {"type": "json_schema", "schema": request.json_schema}}
        if request.tools:
            kwargs["tools"] = request.tools
        return kwargs

    def _map_error(self, exc: Exception) -> Exception:
        name = type(exc).__name__
        if name in ("RateLimitError", "APIConnectionError", "APITimeoutError", "InternalServerError"):
            return RetryableError(f"Anthropic transient error: {exc}")
        status = getattr(exc, "status_code", None)
        if isinstance(status, int) and status >= 500:
            return RetryableError(f"Anthropic server error {status}: {exc}")
        return ProviderAPIError(f"Anthropic error: {exc}")
