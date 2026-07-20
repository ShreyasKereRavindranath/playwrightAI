"""
Locust load shape — turns the 6 profiles (+ custom) into a live VU curve.

A single `ProfileShape` reads its configuration from environment variables set
by `load/engine.py` before Locust is launched, then delegates the actual
"how many users right now?" decision to the pure `plan()` function in
`load/catalog.py`. Keeping the math in `catalog.plan()` means the shape logic is
unit-tested without needing a running Locust.

Env vars (all optional, sensible defaults):
    PLAYSIGHT_PROFILE      profile key: smoke|load|stress|spike|soak|breakpoint|custom
    PLAYSIGHT_USERS        peak virtual users (int)
    PLAYSIGHT_DURATION     total run length in seconds (int)
    PLAYSIGHT_SPAWN_RATE   VUs added/removed per second (float; 0 → auto)
"""

import os

from locust import LoadTestShape

from load.catalog import plan


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


class ProfileShape(LoadTestShape):
    """Drive virtual users according to PLAYSIGHT_* environment configuration."""

    # Read once at import time — the engine sets these in the subprocess env.
    profile = os.getenv("PLAYSIGHT_PROFILE", "smoke")
    peak_users = _env_int("PLAYSIGHT_USERS", 3)
    duration = _env_int("PLAYSIGHT_DURATION", 30)
    spawn_rate = _env_float("PLAYSIGHT_SPAWN_RATE", 0.0)

    def tick(self):
        run_time = self.get_run_time()
        target = plan(self.profile, self.peak_users, self.duration, run_time)
        if target <= 0:
            return None  # ends the test → Locust shuts the run down
        spawn = self.spawn_rate or max(1.0, self.peak_users / 10.0)
        return (target, spawn)
