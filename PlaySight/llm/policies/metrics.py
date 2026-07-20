"""Lightweight metrics + logging sink for LLM calls (provider-neutral)."""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field

logger = logging.getLogger("llm")


@dataclass
class ProviderMetrics:
    calls: int = 0
    failures: int = 0
    total_latency_s: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0

    def as_dict(self) -> dict:
        avg = round(self.total_latency_s / self.calls, 3) if self.calls else 0.0
        return {"calls": self.calls, "failures": self.failures, "avg_latency_s": avg,
                "input_tokens": self.input_tokens, "output_tokens": self.output_tokens}


class Metrics:
    """Process-wide counters, keyed by provider name."""

    def __init__(self):
        self._by_provider: dict[str, ProviderMetrics] = defaultdict(ProviderMetrics)

    @contextmanager
    def track(self, provider: str, operation: str):
        m = self._by_provider[provider]
        m.calls += 1
        start = time.monotonic()
        try:
            yield
        except Exception:
            m.failures += 1
            logger.warning("LLM %s.%s failed", provider, operation)
            raise
        finally:
            m.total_latency_s += time.monotonic() - start

    def record_usage(self, provider: str, input_tokens: int, output_tokens: int) -> None:
        m = self._by_provider[provider]
        m.input_tokens += input_tokens
        m.output_tokens += output_tokens

    def snapshot(self) -> dict:
        return {name: m.as_dict() for name, m in self._by_provider.items()}


metrics = Metrics()
