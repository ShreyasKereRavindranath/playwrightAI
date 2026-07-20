"""Configuration manager for LLM providers.

Resolves each provider's settings from three sources, in precedence order:
    1. environment variables (highest)
    2. config/llm_providers.json  (committed template lives in .example)
    3. OS keyring via a CredentialStore (optional, for secrets)

Also owns the *selected provider*, persisted to config/llm_selection.json so the
user's choice is remembered and switchable at runtime. Cloud providers only need
an API key when they are the selected/used provider — validation is lazy.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_DIR = _ROOT / "config"
_PROVIDERS_FILE = _CONFIG_DIR / "llm_providers.json"
_SELECTION_FILE = _CONFIG_DIR / "llm_selection.json"

DEFAULT_PROVIDER = "openai"

# Per-provider defaults. Model IDs are overridable via env/file.
_DEFAULTS: dict[str, dict] = {
    "openai": {"env_key": "OPENAI_API_KEY", "env_model": "OPENAI_MODEL",
               "default_model": os.getenv("AI_MODEL", "gpt-4o-mini")},
    "anthropic": {"env_key": "ANTHROPIC_API_KEY", "env_model": "ANTHROPIC_MODEL",
                  "default_model": "claude-opus-4-8"},
    "gemini": {"env_key": "GEMINI_API_KEY", "env_key_alt": "GOOGLE_API_KEY",
               "env_model": "GEMINI_MODEL", "default_model": "gemini-2.5-flash"},
    "ollama": {"env_host": "OLLAMA_HOST", "env_model": "OLLAMA_MODEL",
               "default_host": "http://127.0.0.1:11434", "default_model": "llama3.1"},
    "lmstudio": {"env_host": "LMSTUDIO_HOST", "env_model": "LMSTUDIO_MODEL",
                 "default_host": "http://127.0.0.1:1234/v1", "default_model": ""},
    "openai_compatible": {"env_key": "OPENAI_COMPAT_API_KEY", "env_host": "OPENAI_COMPAT_BASE_URL",
                          "env_model": "OPENAI_COMPAT_MODEL", "default_model": ""},
}


@dataclass
class ProviderConfig:
    """Everything a provider needs to construct itself."""

    name: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None      # OpenAI-compatible / LM Studio endpoint
    host: Optional[str] = None          # local runtime base (Ollama)
    default_model: str = ""
    max_tokens: int = 2000
    timeout: float = 60.0
    max_retries: int = 2
    extra: dict = field(default_factory=dict)


class ConfigurationManager:
    def __init__(self, credential_store=None):
        self._store = credential_store
        self._file_cache: Optional[dict] = None

    # -- file helpers --------------------------------------------------------

    def _providers_file(self) -> dict:
        if self._file_cache is None:
            try:
                self._file_cache = json.loads(_PROVIDERS_FILE.read_text())
            except Exception:
                self._file_cache = {}
        return self._file_cache

    def _resolve(self, provider: str, env_var: Optional[str], file_key: str) -> Optional[str]:
        """env var → providers file → credential store."""
        if env_var:
            val = os.getenv(env_var)
            if val:
                return val
        file_section = self._providers_file().get(provider, {})
        if file_section.get(file_key):
            return file_section[file_key]
        if self._store and env_var:
            return self._store.get(env_var)
        return None

    # -- selection (remembered) ---------------------------------------------

    def selected_provider(self) -> str:
        env = os.getenv("AI_PROVIDER")
        if env:
            return env.strip().lower()
        try:
            return json.loads(_SELECTION_FILE.read_text()).get("provider", DEFAULT_PROVIDER)
        except Exception:
            return DEFAULT_PROVIDER

    def set_selected_provider(self, name: str) -> None:
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _SELECTION_FILE.write_text(json.dumps({"provider": name}, indent=2))

    def set_model(self, provider: str, model: str) -> None:
        """Persist a model override for a provider into config/llm_providers.json."""
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = self._providers_file()
        data.setdefault(provider, {})["model"] = model
        _PROVIDERS_FILE.write_text(json.dumps(data, indent=2))
        self._file_cache = data  # keep cache in sync

    # -- per-provider config -------------------------------------------------

    def provider_config(self, name: str) -> ProviderConfig:
        spec = _DEFAULTS.get(name, {})
        section = self._providers_file().get(name, {})
        max_tokens = int(os.getenv("AI_MAX_TOKENS", section.get("max_tokens", 2000)))

        api_key = None
        if spec.get("env_key"):
            api_key = self._resolve(name, spec["env_key"], "api_key")
            if not api_key and spec.get("env_key_alt"):
                api_key = self._resolve(name, spec["env_key_alt"], "api_key")

        host = None
        if spec.get("env_host"):
            host = (os.getenv(spec["env_host"]) or section.get("host")
                    or section.get("base_url") or spec.get("default_host"))

        model = (os.getenv(spec.get("env_model", "")) if spec.get("env_model") else None) \
            or section.get("model") or spec.get("default_model", "")

        # For OpenAI-compatible / LM Studio the "host" is really the base_url.
        base_url = host if name in ("openai_compatible", "lmstudio") else section.get("base_url")

        return ProviderConfig(
            name=name,
            api_key=api_key,
            base_url=base_url,
            host=host,
            default_model=model,
            max_tokens=max_tokens,
            timeout=float(os.getenv("AI_TIMEOUT", section.get("timeout", 60.0))),
            max_retries=int(os.getenv("AI_MAX_RETRIES", section.get("max_retries", 2))),
            extra=section.get("extra", {}),
        )
