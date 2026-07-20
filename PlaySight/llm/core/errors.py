"""Typed error hierarchy for the LLM abstraction (provider-neutral)."""

from __future__ import annotations


class LLMError(Exception):
    """Base class for every error raised by the LLM layer."""


class ProviderNotFound(LLMError):
    """Requested a provider name that is not registered."""


class ProviderUnavailable(LLMError):
    """Provider is registered but not usable (missing config / unreachable)."""


class ConfigurationError(LLMError):
    """Provider configuration is invalid or incomplete."""


class CapabilityNotSupported(LLMError):
    """Requested a feature the selected provider does not support."""


class ProviderAPIError(LLMError):
    """Wraps an error returned by an upstream provider SDK/API."""


class RetryableError(ProviderAPIError):
    """A transient failure (rate limit, 5xx, connection) safe to retry."""
