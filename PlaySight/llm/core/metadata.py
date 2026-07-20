"""Provider metadata, configuration-validation, and health-check value objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Availability(str, Enum):
    """Coarse status a provider reports for UI badges and fallback logic."""

    AVAILABLE = "available"          # ready to use right now
    NEEDS_CONFIG = "needs_config"    # missing API key or setting
    UNREACHABLE = "unreachable"      # configured but the endpoint didn't respond
    NOT_INSTALLED = "not_installed"  # local runtime is not installed
    ERROR = "error"                  # unexpected failure during the probe


@dataclass
class ProviderMetadata:
    name: str               # stable id, e.g. "openai"
    label: str              # human label, e.g. "OpenAI ChatGPT"
    kind: str               # "cloud" | "local"
    requires_api_key: bool
    default_model: str
    homepage: str = ""
    description: str = ""


@dataclass
class ValidationResult:
    ok: bool
    detail: str = ""


@dataclass
class HealthResult:
    availability: Availability
    detail: str = ""
    models: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.availability == Availability.AVAILABLE
