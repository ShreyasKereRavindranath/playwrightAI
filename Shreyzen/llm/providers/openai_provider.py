"""OpenAI ChatGPT provider.

Replicates the framework's original OpenAI behavior (chat completions,
`response_format=json_object`, gpt-4o-mini default) behind the common interface.
The `openai` SDK is imported lazily inside methods so the module can be
discovered/registered even when the SDK isn't installed.
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


@register_provider("openai")
class OpenAIProvider(LLMProvider):
    def __init__(self, config: ProviderConfig):
        self._cfg = config
        self._embed_model = config.extra.get("embedding_model", "text-embedding-3-small")

    # -- descriptors ---------------------------------------------------------

    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            name="openai", label="OpenAI ChatGPT", kind="cloud",
            requires_api_key=True, default_model=self._cfg.default_model or "gpt-4o-mini",
            homepage="https://platform.openai.com",
            description="OpenAI chat models (GPT-4o family) via the official SDK.",
        )

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities.of(
            Capability.CHAT, Capability.STREAMING, Capability.SYSTEM_PROMPT,
            Capability.TEMPERATURE, Capability.MAX_TOKENS, Capability.JSON_MODE,
            Capability.STRUCTURED_OUTPUT, Capability.TOOLS, Capability.VISION,
            Capability.EMBEDDINGS, Capability.REASONING,
        )

    def validate_config(self) -> ValidationResult:
        if not self._cfg.api_key:
            return ValidationResult(False, "OPENAI_API_KEY is not set.")
        return ValidationResult(True, "API key present.")

    def health_check(self) -> HealthResult:
        if not self._cfg.api_key:
            return HealthResult(Availability.NEEDS_CONFIG, "OPENAI_API_KEY is not set.")
        try:
            client = self._client()
            models = client.models.list()
            ids = [m.id for m in getattr(models, "data", [])][:20]
            return HealthResult(Availability.AVAILABLE, "Reachable.", models=ids)
        except Exception as exc:
            return HealthResult(Availability.UNREACHABLE, str(exc))

    # -- calls ---------------------------------------------------------------

    def complete(self, request: LLMRequest) -> LLMResponse:
        client = self._client()
        kwargs = self._build_kwargs(request)
        try:
            resp = client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise self._map_error(exc)
        choice = resp.choices[0]
        usage = getattr(resp, "usage", None)
        return LLMResponse(
            text=(choice.message.content or "").strip(),
            model=resp.model, provider="openai",
            usage=Usage(getattr(usage, "prompt_tokens", 0) or 0,
                        getattr(usage, "completion_tokens", 0) or 0),
            finish_reason=choice.finish_reason or "", raw=resp,
        )

    def stream(self, request: LLMRequest) -> Iterator[StreamChunk]:
        client = self._client()
        kwargs = self._build_kwargs(request)
        kwargs["stream"] = True
        try:
            for chunk in client.chat.completions.create(**kwargs):
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    yield StreamChunk(text=delta)
            yield StreamChunk(done=True)
        except Exception as exc:
            raise self._map_error(exc)

    def embed(self, texts: list[str]) -> list[list[float]]:
        client = self._client()
        try:
            resp = client.embeddings.create(model=self._embed_model, input=texts)
        except Exception as exc:
            raise self._map_error(exc)
        return [d.embedding for d in resp.data]

    # -- internals -----------------------------------------------------------

    def _client(self):
        from openai import OpenAI
        if not self._cfg.api_key:
            raise ProviderAPIError("OPENAI_API_KEY is not set.")
        kwargs = {"api_key": self._cfg.api_key, "timeout": self._cfg.timeout}
        if self._cfg.base_url:
            kwargs["base_url"] = self._cfg.base_url
        return OpenAI(**kwargs)

    def _build_kwargs(self, request: LLMRequest) -> dict:
        messages = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        for m in request.messages:
            if m.images:
                content = [{"type": "text", "text": m.content}]
                for img in m.images:
                    content.append({"type": "image_url", "image_url": {"url": img}})
                messages.append({"role": m.role, "content": content})
            else:
                messages.append({"role": m.role, "content": m.content})

        kwargs: dict = {
            "model": request.model or self._cfg.default_model or "gpt-4o-mini",
            "messages": messages,
            "max_tokens": request.max_tokens or self._cfg.max_tokens,
        }
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.json_schema:
            kwargs["response_format"] = {"type": "json_schema",
                                         "json_schema": {"name": "response", "schema": request.json_schema}}
        elif request.json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if request.tools:
            kwargs["tools"] = request.tools
        return kwargs

    def _map_error(self, exc: Exception) -> Exception:
        name = type(exc).__name__
        if name in ("RateLimitError", "APIConnectionError", "APITimeoutError", "InternalServerError"):
            return RetryableError(f"OpenAI transient error: {exc}")
        status = getattr(exc, "status_code", None)
        if isinstance(status, int) and status >= 500:
            return RetryableError(f"OpenAI server error {status}: {exc}")
        return ProviderAPIError(f"OpenAI error: {exc}")
