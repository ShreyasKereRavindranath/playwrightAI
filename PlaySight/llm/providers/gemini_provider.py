"""Google Gemini provider (via the `google-genai` SDK, imported lazily)."""

from __future__ import annotations

from typing import Iterator

from ..config import ProviderConfig
from ..core.capabilities import Capability, ProviderCapabilities
from ..core.errors import ProviderAPIError, RetryableError
from ..core.interfaces import LLMProvider
from ..core.metadata import Availability, HealthResult, ProviderMetadata, ValidationResult
from ..core.models import LLMRequest, LLMResponse, StreamChunk, Usage
from ..registry import register_provider


@register_provider("gemini")
class GeminiProvider(LLMProvider):
    def __init__(self, config: ProviderConfig):
        self._cfg = config
        self._embed_model = config.extra.get("embedding_model", "text-embedding-004")

    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            name="gemini", label="Google Gemini", kind="cloud",
            requires_api_key=True, default_model=self._cfg.default_model or "gemini-2.5-flash",
            homepage="https://ai.google.dev",
            description="Google Gemini models via the google-genai SDK.",
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
            return ValidationResult(False, "GEMINI_API_KEY (or GOOGLE_API_KEY) is not set.")
        return ValidationResult(True, "API key present.")

    def health_check(self) -> HealthResult:
        if not self._cfg.api_key:
            return HealthResult(Availability.NEEDS_CONFIG, "GEMINI_API_KEY is not set.")
        try:
            client = self._client()
            ids = [m.name for m in client.models.list()][:20]
            return HealthResult(Availability.AVAILABLE, "Reachable.", models=ids)
        except Exception as exc:
            return HealthResult(Availability.UNREACHABLE, str(exc))

    def complete(self, request: LLMRequest) -> LLMResponse:
        client = self._client()
        contents, config = self._build(request)
        try:
            resp = client.models.generate_content(
                model=request.model or self._cfg.default_model or "gemini-2.5-flash",
                contents=contents, config=config,
            )
        except Exception as exc:
            raise self._map_error(exc)
        usage = getattr(resp, "usage_metadata", None)
        return LLMResponse(
            text=(resp.text or "").strip(),
            model=request.model or self._cfg.default_model or "gemini-2.5-flash",
            provider="gemini",
            usage=Usage(getattr(usage, "prompt_token_count", 0) or 0,
                        getattr(usage, "candidates_token_count", 0) or 0),
            raw=resp,
        )

    def stream(self, request: LLMRequest) -> Iterator[StreamChunk]:
        client = self._client()
        contents, config = self._build(request)
        try:
            for chunk in client.models.generate_content_stream(
                model=request.model or self._cfg.default_model or "gemini-2.5-flash",
                contents=contents, config=config,
            ):
                if getattr(chunk, "text", None):
                    yield StreamChunk(text=chunk.text)
            yield StreamChunk(done=True)
        except Exception as exc:
            raise self._map_error(exc)

    def embed(self, texts: list[str]) -> list[list[float]]:
        client = self._client()
        try:
            resp = client.models.embed_content(model=self._embed_model, contents=texts)
        except Exception as exc:
            raise self._map_error(exc)
        return [e.values for e in resp.embeddings]

    # -- internals -----------------------------------------------------------

    def _client(self):
        from google import genai
        if not self._cfg.api_key:
            raise ProviderAPIError("GEMINI_API_KEY is not set.")
        return genai.Client(api_key=self._cfg.api_key)

    def _build(self, request: LLMRequest):
        from google.genai import types
        # Gemini takes a single contents string/list; fold the chat into text.
        parts = [f"{m.role}: {m.content}" for m in request.messages if m.role != "system"]
        contents = "\n".join(parts) if parts else (request.messages[-1].content if request.messages else "")
        cfg: dict = {}
        if request.system:
            cfg["system_instruction"] = request.system
        if request.temperature is not None:
            cfg["temperature"] = request.temperature
        if request.max_tokens or self._cfg.max_tokens:
            cfg["max_output_tokens"] = request.max_tokens or self._cfg.max_tokens
        if request.json_schema:
            cfg["response_mime_type"] = "application/json"
            cfg["response_schema"] = request.json_schema
        elif request.json_mode:
            cfg["response_mime_type"] = "application/json"
        return contents, types.GenerateContentConfig(**cfg)

    def _map_error(self, exc: Exception) -> Exception:
        text = str(exc).lower()
        if any(s in text for s in ("rate limit", "429", "resource exhausted", "unavailable", "timeout", "500", "503")):
            return RetryableError(f"Gemini transient error: {exc}")
        return ProviderAPIError(f"Gemini error: {exc}")
