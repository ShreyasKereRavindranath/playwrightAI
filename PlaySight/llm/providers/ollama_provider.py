"""Ollama local provider (HTTP API, no SDK; auto-bootstraps on first use)."""

from __future__ import annotations

import logging
from typing import Iterator

import requests

from ..config import ProviderConfig
from ..core.capabilities import Capability, ProviderCapabilities
from ..core.errors import ProviderAPIError, ProviderUnavailable, RetryableError
from ..core.interfaces import LLMProvider
from ..core.metadata import Availability, HealthResult, ProviderMetadata, ValidationResult
from ..core.models import LLMRequest, LLMResponse, StreamChunk, Usage
from ..local.ollama_bootstrap import OllamaBootstrapper
from ..registry import register_provider

logger = logging.getLogger("llm.ollama")


@register_provider("ollama")
class OllamaProvider(LLMProvider):
    def __init__(self, config: ProviderConfig):
        self._cfg = config
        self._host = (config.host or "http://127.0.0.1:11434").rstrip("/")
        self._boot = OllamaBootstrapper(self._host)

    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            name="ollama", label="Ollama (local)", kind="local",
            requires_api_key=False, default_model=self._cfg.default_model or "llama3.1",
            homepage="https://ollama.com",
            description="Local models via Ollama — auto-installs, starts, and pulls models.",
        )

    def capabilities(self) -> ProviderCapabilities:
        # Conservative set: tools/vision/reasoning are model-dependent, so left off.
        return ProviderCapabilities.of(
            Capability.CHAT, Capability.STREAMING, Capability.SYSTEM_PROMPT,
            Capability.TEMPERATURE, Capability.MAX_TOKENS, Capability.JSON_MODE,
            Capability.STRUCTURED_OUTPUT, Capability.EMBEDDINGS,
        )

    def validate_config(self) -> ValidationResult:
        # Local — no key needed; it's always "configurable".
        return ValidationResult(True, f"Local endpoint {self._host} (auto-bootstrapped on use).")

    def health_check(self) -> HealthResult:
        if self._boot.is_running():
            try:
                data = requests.get(f"{self._host}/api/tags", timeout=5).json()
                models = [m.get("name", "") for m in data.get("models", [])]
            except Exception:
                models = []
            return HealthResult(Availability.AVAILABLE, "Ollama running.", models=models)
        if self._boot.is_installed():
            return HealthResult(Availability.UNREACHABLE, "Ollama installed but not running (auto-starts on use).")
        return HealthResult(Availability.NOT_INSTALLED, "Ollama not installed (auto-installs on use).")

    def complete(self, request: LLMRequest) -> LLMResponse:
        model = request.model or self._cfg.default_model or "llama3.1"
        self._ensure(model)
        payload = self._payload(request, model, stream=False)
        try:
            resp = requests.post(f"{self._host}/api/chat", json=payload, timeout=self._cfg.timeout * 5)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            raise RetryableError(f"Ollama request failed: {exc}")
        return LLMResponse(
            text=(data.get("message", {}).get("content", "") or "").strip(),
            model=model, provider="ollama",
            usage=Usage(data.get("prompt_eval_count", 0) or 0, data.get("eval_count", 0) or 0),
            finish_reason=data.get("done_reason", "") or "", raw=data,
        )

    def stream(self, request: LLMRequest) -> Iterator[StreamChunk]:
        import json as _json
        model = request.model or self._cfg.default_model or "llama3.1"
        self._ensure(model)
        payload = self._payload(request, model, stream=True)
        try:
            with requests.post(f"{self._host}/api/chat", json=payload, stream=True,
                               timeout=self._cfg.timeout * 5) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line:
                        continue
                    evt = _json.loads(line)
                    piece = evt.get("message", {}).get("content", "")
                    if piece:
                        yield StreamChunk(text=piece)
                    if evt.get("done"):
                        yield StreamChunk(done=True)
        except requests.RequestException as exc:
            raise RetryableError(f"Ollama stream failed: {exc}")

    def embed(self, texts: list[str]) -> list[list[float]]:
        model = self._cfg.extra.get("embedding_model", self._cfg.default_model or "llama3.1")
        self._ensure(model)
        out = []
        for text in texts:
            try:
                resp = requests.post(f"{self._host}/api/embeddings",
                                     json={"model": model, "prompt": text}, timeout=self._cfg.timeout)
                resp.raise_for_status()
                out.append(resp.json().get("embedding", []))
            except requests.RequestException as exc:
                raise ProviderAPIError(f"Ollama embeddings failed: {exc}")
        return out

    # -- internals -----------------------------------------------------------

    def _ensure(self, model: str) -> None:
        if self._boot.is_running() and self._boot.has_model(model):
            return
        result = self._boot.ensure_ready(model, progress=logger.info)
        if result.availability != Availability.AVAILABLE:
            raise ProviderUnavailable(f"Ollama not ready: {result.detail}")

    def _payload(self, request: LLMRequest, model: str, stream: bool) -> dict:
        messages = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        for m in request.messages:
            messages.append({"role": m.role, "content": m.content})
        options: dict = {}
        if request.temperature is not None:
            options["temperature"] = request.temperature
        if request.max_tokens or self._cfg.max_tokens:
            options["num_predict"] = request.max_tokens or self._cfg.max_tokens
        payload: dict = {"model": model, "messages": messages, "stream": stream, "options": options}
        if request.json_schema:
            payload["format"] = request.json_schema
        elif request.json_mode:
            payload["format"] = "json"
        return payload
