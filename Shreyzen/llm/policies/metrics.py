"""Lightweight metrics + logging sink for LLM calls (provider-neutral).

Besides the process-wide aggregate counters, this exposes two extension points
the application layer hooks into (see utils/llm_observability.py):

  • sinks  — callbacks invoked with a per-call CallRecord after every completion,
             used to estimate cost, persist usage, and accumulate budget totals.
  • guards — callbacks invoked *before* a call; raising from one aborts the call,
             which is how the cost/latency budget guardrails stop runaway spend.

The `llm` package stays dependency-free: it never imports the app. The app
registers its callbacks here at startup instead.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable

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


@dataclass
class CallRecord:
    """Everything observable about one completed LLM call."""

    provider: str
    operation: str = "complete"
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0
    ok: bool = True
    cached: bool = False
    # Filled in by sinks (which can inspect the call stack / pricing): the calling
    # feature and the estimated USD cost.
    feature: str = ""
    cost_usd: float = 0.0


class Metrics:
    """Process-wide counters, keyed by provider name, plus sink/guard hooks."""

    def __init__(self):
        self._by_provider: dict[str, ProviderMetrics] = defaultdict(ProviderMetrics)
        self._sinks: list[Callable[[CallRecord], None]] = []
        self._guards: list[Callable[[str, str, str], None]] = []

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

    # -- extension points ----------------------------------------------------

    def add_sink(self, fn: Callable[[CallRecord], None]) -> None:
        if fn not in self._sinks:
            self._sinks.append(fn)

    def add_guard(self, fn: Callable[[str, str, str], None]) -> None:
        if fn not in self._guards:
            self._guards.append(fn)

    def clear_hooks(self) -> None:
        """Drop all sinks/guards — used by tests to isolate."""
        self._sinks.clear()
        self._guards.clear()

    def check_guards(self, provider: str, operation: str, feature: str = "") -> None:
        """Run pre-call guards; a guard raising aborts the call (propagates up)."""
        for g in self._guards:
            g(provider, operation, feature)

    def emit(self, record: CallRecord) -> None:
        """Fold a completed call into the aggregates and fan out to sinks."""
        m = self._by_provider[record.provider]
        m.calls += 1
        if not record.ok:
            m.failures += 1
        m.total_latency_s += record.latency_s
        m.input_tokens += record.input_tokens
        m.output_tokens += record.output_tokens
        for sink in self._sinks:
            try:
                sink(record)
            except Exception as exc:  # a broken sink must never break a call
                logger.debug("LLM metrics sink error: %s", exc)


metrics = Metrics()
