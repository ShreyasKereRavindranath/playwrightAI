"""Provider registry with automatic discovery.

Providers self-register with the `@register_provider("name")` decorator. Adding
a provider therefore requires only a new module in `llm/providers/` plus the
decorator — `discover()` imports every module in that package so the decorators
run. No other part of the application changes.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Type

from .core.errors import ProviderNotFound
from .core.interfaces import LLMProvider

_REGISTRY: dict[str, Type[LLMProvider]] = {}
_DISCOVERED = False


def register_provider(name: str):
    """Class decorator that registers a provider under a stable name."""
    def decorator(cls: Type[LLMProvider]) -> Type[LLMProvider]:
        _REGISTRY[name] = cls
        return cls
    return decorator


def discover() -> None:
    """Import every module under llm.providers so their decorators execute."""
    global _DISCOVERED
    if _DISCOVERED:
        return
    from llm import providers  # local import to avoid a cycle at module load
    for module in pkgutil.iter_modules(providers.__path__):
        if module.name.startswith("_"):
            continue
        importlib.import_module(f"llm.providers.{module.name}")
    _DISCOVERED = True


def registered() -> dict[str, Type[LLMProvider]]:
    discover()
    return dict(_REGISTRY)


def get_provider_class(name: str) -> Type[LLMProvider]:
    reg = registered()
    if name not in reg:
        raise ProviderNotFound(
            f"Unknown provider '{name}'. Registered: {', '.join(sorted(reg)) or '(none)'}"
        )
    return reg[name]
