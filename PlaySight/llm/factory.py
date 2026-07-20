"""Provider factory — builds a configured provider instance by name."""

from __future__ import annotations

from . import registry
from .config import ConfigurationManager
from .core.interfaces import LLMProvider


class ProviderFactory:
    def __init__(self, config: ConfigurationManager):
        self._config = config

    def create(self, name: str) -> LLMProvider:
        cls = registry.get_provider_class(name)
        cfg = self._config.provider_config(name)
        return cls(cfg)

    def available_names(self) -> list[str]:
        return sorted(registry.registered())
