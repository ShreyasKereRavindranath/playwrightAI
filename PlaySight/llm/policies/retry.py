"""Retry policy — Strategy for transient upstream failures."""

from __future__ import annotations

import time
from typing import Callable, TypeVar

from ..core.errors import RetryableError

T = TypeVar("T")


def with_retry(fn: Callable[[], T], *, max_retries: int = 2, base_delay: float = 0.5) -> T:
    """Run `fn`, retrying only on RetryableError with exponential backoff."""
    last: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except RetryableError as exc:
            last = exc
            if attempt == max_retries:
                break
            time.sleep(base_delay * (2 ** attempt))
    assert last is not None
    raise last
