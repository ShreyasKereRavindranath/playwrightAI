"""LLMService — the single facade the whole application talks to.

Responsibilities:
  * own the *selected* provider (create lazily via the factory, remember choice)
  * negotiate capabilities — strip request fields the provider can't handle,
    so unsupported features degrade gracefully instead of erroring
  * apply retry + metrics policies around every call
  * expose provider discovery/validation/health for the UI

The rest of the app must depend on this class, never on a provider module.
"""

from __future__ import annotations

import copy
import json
import logging
import re
import time
from dataclasses import replace
from typing import Iterator, Optional

from .config import ConfigurationManager
from .core.capabilities import Capability
from .core.errors import LLMError
from .core.interfaces import LLMProvider
from .core.metadata import HealthResult, ProviderMetadata, ValidationResult
from .core.models import LLMRequest, LLMResponse, StreamChunk
from .factory import ProviderFactory
from .policies.metrics import CallRecord, metrics
from .policies.retry import with_retry

logger = logging.getLogger("llm")


class LLMService:
    def __init__(self, config: Optional[ConfigurationManager] = None):
        self._config = config or ConfigurationManager()
        self._factory = ProviderFactory(self._config)
        self._active: Optional[LLMProvider] = None
        self._active_name: Optional[str] = None

    # -- provider lifecycle --------------------------------------------------

    def provider(self) -> LLMProvider:
        name = self._config.selected_provider()
        if self._active is None or self._active_name != name:
            self._active = self._factory.create(name)
            self._active_name = name
        return self._active

    def current_provider_name(self) -> str:
        return self._config.selected_provider()

    def select_provider(self, name: str) -> ValidationResult:
        provider = self._factory.create(name)          # raises ProviderNotFound
        result = provider.validate_config()
        self._config.set_selected_provider(name)        # remember regardless
        self._active, self._active_name = provider, name
        return result

    # -- discovery / status (for the UI) ------------------------------------

    def list_providers(self) -> list[dict]:
        out = []
        for name in self._factory.available_names():
            try:
                p = self._factory.create(name)
                md: ProviderMetadata = p.metadata()
                out.append({
                    "name": md.name, "label": md.label, "kind": md.kind,
                    "requires_api_key": md.requires_api_key,
                    "default_model": md.default_model, "description": md.description,
                    "capabilities": p.capabilities().as_list(),
                    "selected": name == self.current_provider_name(),
                })
            except Exception as exc:  # never let one bad provider break the list
                logger.debug("Skipping provider %s in listing: %s", name, exc)
        return out

    def validate(self) -> ValidationResult:
        return self.provider().validate_config()

    def health(self) -> HealthResult:
        return self.provider().health_check()

    def available(self) -> bool:
        try:
            return self.provider().validate_config().ok
        except Exception:
            return False

    def capabilities(self):
        return self.provider().capabilities()

    # -- model selection -----------------------------------------------------

    _MODEL_FALLBACKS = {
        "openai": ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "o4-mini"],
        "anthropic": ["claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5"],
        "gemini": ["gemini-2.5-flash", "gemini-2.5-pro"],
        "ollama": ["llama3.1", "qwen2.5", "mistral"],
        "lmstudio": [],
        "openai_compatible": [],
    }

    # Cost-ordered escalation ladders (cheapest → strongest) used by routing:
    # try the cheap model first, escalate only when it returns weak/empty output.
    _ESCALATION = {
        "openai": ["gpt-4o-mini", "gpt-4o"],
        "anthropic": ["claude-haiku-4-5", "claude-sonnet-5", "claude-opus-4-8"],
        "gemini": ["gemini-2.5-flash", "gemini-2.5-pro"],
    }
    # Order in which to fail over to a *different* provider when the selected one
    # errors or can't produce output. Only configured providers are tried.
    _FAILOVER = ["openai", "anthropic", "gemini", "ollama", "lmstudio", "openai_compatible"]

    def current_model(self) -> str:
        try:
            return self.provider().metadata().default_model
        except Exception:
            return ""

    def available_models(self) -> list[str]:
        """Live model list from the provider; falls back to a curated list."""
        name = self.current_provider_name()
        models: list[str] = []
        try:
            h = self.provider().health_check()
            models = list(h.models or [])
        except Exception:
            models = []
        current = self.current_model()
        for m in ([current] + self._MODEL_FALLBACKS.get(name, [])):
            if m and m not in models:
                models.append(m)
        return models

    def set_model(self, model: str) -> None:
        self._config.set_model(self.current_provider_name(), model)
        self._active = None  # rebuild so the new model takes effect

    # -- core calls ----------------------------------------------------------

    def complete(self, request: LLMRequest) -> LLMResponse:
        """One completion on the currently-selected provider (fully instrumented)."""
        return self._complete_on(self.current_provider_name(), request)

    def _provider_for(self, name: str) -> LLMProvider:
        """The cached provider for the selected name, else an ad-hoc one (failover)."""
        if name == self._config.selected_provider():
            return self.provider()
        return self._factory.create(name)

    def _complete_on(self, name: str, request: LLMRequest) -> LLMResponse:
        """Run one completion on a named provider, with guard + timing + emit."""
        provider = self._provider_for(name)
        req = self._negotiate(provider, request)
        # Pre-call guardrails (cost/latency budget). A guard raising aborts the
        # call; callers via LLMClient degrade gracefully to their offline path.
        metrics.check_guards(name, "complete", "")
        start = time.monotonic()
        ok = True
        resp: Optional[LLMResponse] = None
        try:
            resp = with_retry(lambda: provider.complete(req),
                              max_retries=self._config.provider_config(name).max_retries)
            return resp
        except Exception:
            ok = False
            raise
        finally:
            usage = resp.usage if resp else None
            metrics.emit(CallRecord(
                provider=name,
                operation="complete",
                model=(resp.model if resp and resp.model else (req.model or "")),
                input_tokens=(usage.input_tokens if usage else 0),
                output_tokens=(usage.output_tokens if usage else 0),
                latency_s=time.monotonic() - start,
                ok=ok,
            ))

    def stream(self, request: LLMRequest) -> Iterator[StreamChunk]:
        provider = self.provider()
        req = self._negotiate(provider, replace(request, stream=True))
        with metrics.track(self._active_name or "?", "stream"):
            yield from provider.stream(req)

    def embed(self, texts: list[str]) -> list[list[float]]:
        provider = self.provider()
        with metrics.track(self._active_name or "?", "embed"):
            return provider.embed(texts)

    # -- convenience mirrors of the legacy LLMClient surface ----------------

    def complete_text(self, prompt: str, system: Optional[str] = None,
                      model: Optional[str] = None, max_tokens: Optional[int] = None,
                      temperature: float = 0.2) -> str:
        def run(name: str, model_name: Optional[str]) -> str:
            req = LLMRequest.simple(prompt, system=system, model=model_name,
                                    max_tokens=max_tokens, temperature=temperature)
            return self._complete_on(name, req).text
        return self._route(run, lambda t: not (t or "").strip(), model, "")

    def complete_json(self, prompt: str, system: Optional[str] = None,
                      model: Optional[str] = None) -> dict:
        sys_full = (system or "") + "\nAlways respond with valid JSON only — no markdown, no prose."
        def run(name: str, model_name: Optional[str]) -> dict:
            req = LLMRequest.simple(prompt, system=sys_full, model=model_name,
                                    temperature=0.1, json_mode=True)
            return _parse_json(self._complete_on(name, req).text)
        return self._route(run, lambda d: not d, model, {})

    # -- routing & fallback --------------------------------------------------
    # When LLM_ROUTING_ENABLED, a completion tries the cheapest model first and
    # escalates only if the output is weak/empty; if the whole provider errors or
    # stays weak, it fails over to the next configured provider. Every attempt is
    # a normal instrumented call, so cost/latency of each hop shows in llm_usage.

    def _routing_enabled(self) -> bool:
        try:
            from config.config import Config
            return bool(Config.LLM_ROUTING_ENABLED)
        except Exception:  # pragma: no cover - config always importable in practice
            return False

    def _escalation_for(self, name: str) -> list:
        # [None] → "use the provider's default/configured model" (single shot).
        return list(self._ESCALATION.get(name) or [None])

    def _is_configured(self, name: str) -> bool:
        try:
            return self._factory.create(name).validate_config().ok
        except Exception:
            return False

    def _route(self, run, is_weak, explicit_model, empty):
        """Drive `run(provider, model)` through escalation + failover.

        `run` performs one completion and returns the caller's result type;
        `is_weak(result)` marks an empty/unusable result that should escalate;
        `empty` is returned if every candidate fails. Budget-guard aborts are
        re-raised (they must stop the whole chain, not trigger failover).
        """
        sel = self.current_provider_name()
        if not self._routing_enabled():
            return run(sel, explicit_model)

        last = empty
        # 1. Selected provider — honor an explicit model, else escalate cheap→strong.
        for model in ([explicit_model] if explicit_model else self._escalation_for(sel)):
            got, result = self._try(run, sel, model)
            if got and not is_weak(result):
                return result
            if got:
                last = result

        # 2. Fail over to other configured providers (their cheapest model).
        for name in self._FAILOVER:
            if name == sel or not self._is_configured(name):
                continue
            model = self._escalation_for(name)[0]
            got, result = self._try(run, name, model)
            if got and not is_weak(result):
                logger.info("LLM routing: recovered via failover %s/%s", name, model)
                return result
            if got:
                last = result
        return last

    @staticmethod
    def _try(run, name, model):
        """Run one candidate; (True, result) on success, (False, None) on error.

        A budget-guard abort (LLMBudgetExceeded) is re-raised so the chain stops
        immediately instead of burning the fallback providers too.
        """
        try:
            return True, run(name, model)
        except Exception as exc:
            if type(exc).__name__ == "LLMBudgetExceeded":
                raise
            logger.warning("LLM routing: %s/%s errored (%s) — trying next", name, model, exc)
            return False, None

    # -- capability negotiation ---------------------------------------------

    def _negotiate(self, provider: LLMProvider, request: LLMRequest) -> LLMRequest:
        caps = provider.capabilities()
        req = copy.deepcopy(request)
        if req.temperature is not None and not caps.has(Capability.TEMPERATURE):
            logger.debug("%s ignores temperature — dropping it", self._active_name)
            req.temperature = None
        if req.tools and not caps.has(Capability.TOOLS):
            req.tools = None
        if req.reasoning and not caps.has(Capability.REASONING):
            req.reasoning = False
        if req.json_mode and not (caps.has(Capability.JSON_MODE) or caps.has(Capability.STRUCTURED_OUTPUT)):
            # No native JSON mode — the system instruction added in complete_json
            # is the fallback; leave the flag off so the provider doesn't error.
            req.json_mode = False
        if not caps.has(Capability.VISION) and any(m.images for m in req.messages):
            for m in req.messages:
                m.images = []
        return req


def _parse_json(text: str) -> dict:
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:
        pass
    match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            return {}
    return {}


# Process-wide default instance (the app imports this).
_default: Optional[LLMService] = None


def get_service() -> LLMService:
    global _default
    if _default is None:
        _default = LLMService()
        # Best-effort: install the app's cost/latency observability + budget
        # guardrails onto the metrics hooks. Late, guarded import keeps the llm
        # package free of any dependency on the application layer.
        try:
            from utils.llm_observability import install
            install()
        except Exception:  # pragma: no cover - observability must never break LLM
            pass
    return _default
