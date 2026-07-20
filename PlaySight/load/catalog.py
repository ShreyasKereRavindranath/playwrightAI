"""
Static catalog of load-test scenarios and profiles + the pure load-planning math.

This module deliberately imports **nothing from Locust**, so it can be used by
the runner UI, the CLI, the CI entrypoints, and the unit tests without needing
the load engine installed. `load/shapes.py` combines `plan()` with Locust's
`LoadTestShape` to actually drive virtual users.

Terminology
-----------
* **Scenario** — *what* the virtual users do (CRUD, a full journey, security probes).
* **Profile**  — *how* the load is shaped over time (smoke, load, stress, …).
* **VUs**      — virtual users; the peak concurrency the profile ramps toward.
"""

from dataclasses import dataclass, asdict, field
from typing import Optional


# ── Scenarios (the Locust user classes in load/locustfile.py) ────────────────

@dataclass(frozen=True)
class Scenario:
    key: str            # stable id used on the CLI / API
    user_class: str     # Locust User subclass name in locustfile.py
    label: str
    icon: str
    blurb: str


SCENARIOS: dict[str, Scenario] = {
    "crud": Scenario(
        key="crud", user_class="BookingCrudUser",
        label="API CRUD", icon="🗂️",
        blurb="Create · Read · List · Update (PUT) · Patch (PATCH) · Delete against the booking API.",
    ),
    "journey": Scenario(
        key="journey", user_class="UserJourneyUser",
        label="User Journey", icon="🧭",
        blurb="A full end-to-end journey per user: auth → create → read → update → patch → delete → verify.",
    ),
    "security": Scenario(
        key="security", user_class="SecurityUser",
        label="Security Probes", icon="🛡️",
        blurb="Non-destructive security checks: auth bypass, missing-auth writes, injection & malformed payloads.",
    ),
}


# ── Profiles (the 6 load shapes + custom) ────────────────────────────────────

@dataclass(frozen=True)
class Profile:
    key: str
    label: str
    icon: str
    blurb: str
    default_users: int          # peak VUs
    default_duration: int       # seconds
    default_spawn_rate: float   # VUs added per second (0 → auto)
    max_fail_ratio: float       # threshold: fraction of failed requests to still pass
    p95_budget_ms: int          # threshold: 95th-percentile response-time budget
    long_running: bool = False  # soak-style profiles (UI warns before launching)


PROFILES: dict[str, Profile] = {
    "smoke": Profile(
        key="smoke", label="Smoke", icon="💨",
        blurb="Minimal load, a handful of users. Proves the system works at all before spending real load.",
        default_users=3, default_duration=30, default_spawn_rate=1,
        max_fail_ratio=0.0, p95_budget_ms=800,
    ),
    "load": Profile(
        key="load", label="Load", icon="📈",
        blurb="Ramp to the expected peak concurrency and hold. Measures behaviour under normal busy traffic.",
        default_users=50, default_duration=180, default_spawn_rate=5,
        max_fail_ratio=0.01, p95_budget_ms=1500,
    ),
    "stress": Profile(
        key="stress", label="Stress", icon="🔥",
        blurb="Step past the expected peak (up to ~2× VUs) to find where performance degrades.",
        default_users=100, default_duration=240, default_spawn_rate=10,
        max_fail_ratio=0.05, p95_budget_ms=3000,
    ),
    "spike": Profile(
        key="spike", label="Spike", icon="⚡",
        blurb="Sit at a low baseline, then slam to peak VUs suddenly and recover. Tests elasticity.",
        default_users=80, default_duration=150, default_spawn_rate=40,
        max_fail_ratio=0.05, p95_budget_ms=3000,
    ),
    "soak": Profile(
        key="soak", label="Soak", icon="🛁",
        blurb="Hold a steady moderate load for a long time to surface leaks and slow degradation.",
        default_users=30, default_duration=1800, default_spawn_rate=3,
        max_fail_ratio=0.01, p95_budget_ms=1500, long_running=True,
    ),
    "breakpoint": Profile(
        key="breakpoint", label="Breakpoint", icon="📉",
        blurb="Linearly ramp VUs with no ceiling (to ~2× target) to locate the breaking point.",
        default_users=150, default_duration=300, default_spawn_rate=5,
        max_fail_ratio=0.10, p95_budget_ms=5000,
    ),
    "custom": Profile(
        key="custom", label="Custom", icon="🎛️",
        blurb="Flat load at exactly the VU count and duration you choose. Run any scenario at any scale.",
        default_users=25, default_duration=120, default_spawn_rate=5,
        max_fail_ratio=0.02, p95_budget_ms=2000,
    ),
}


# ── Runtime parameter resolution ─────────────────────────────────────────────

@dataclass
class RunParams:
    scenario: str
    profile: str
    users: int          # peak VUs
    duration: int       # seconds
    spawn_rate: float   # VUs/sec (0 → shape decides)
    host: str

    def as_dict(self) -> dict:
        return asdict(self)


def resolve_params(
    scenario: str,
    profile: str,
    *,
    users: Optional[int] = None,
    duration: Optional[int] = None,
    spawn_rate: Optional[float] = None,
    host: str = "http://127.0.0.1:8765",
) -> RunParams:
    """Merge user overrides over a profile's defaults, validating keys."""
    if scenario not in SCENARIOS:
        raise ValueError(f"Unknown scenario '{scenario}'. Choices: {', '.join(SCENARIOS)}")
    if profile not in PROFILES:
        raise ValueError(f"Unknown profile '{profile}'. Choices: {', '.join(PROFILES)}")
    p = PROFILES[profile]
    return RunParams(
        scenario=scenario,
        profile=profile,
        users=max(1, int(users if users is not None else p.default_users)),
        duration=max(5, int(duration if duration is not None else p.default_duration)),
        spawn_rate=float(spawn_rate if spawn_rate else p.default_spawn_rate),
        host=host,
    )


# ── Pure load-planning math (shared by the Locust shape + tests) ─────────────

def plan(profile: str, peak_users: int, duration: int, elapsed: float) -> int:
    """
    Return the *target* number of virtual users at `elapsed` seconds into a run.

    Pure function — no Locust dependency — so the shape logic is unit-testable.
    `load/shapes.py:ProfileShape.tick()` calls this once per second.
    Returns 0 when the run should stop (elapsed beyond duration).
    """
    if elapsed >= duration or duration <= 0:
        return 0
    peak = max(1, peak_users)
    frac = elapsed / duration  # 0.0 → 1.0

    if profile == "smoke" or profile == "custom":
        # Flat load for the whole window.
        return peak

    if profile == "soak":
        # Short ramp-up (first 5%) then a long steady hold.
        return max(1, round(peak * min(1.0, frac / 0.05)))

    if profile == "load":
        # Ramp over the first 20%, then hold at peak.
        return max(1, round(peak * min(1.0, frac / 0.20)))

    if profile == "stress":
        # Five ascending steps, climbing past peak to ~1.5× peak.
        steps = 5
        top = peak * 1.5
        step = min(steps - 1, int(frac * steps))
        return max(1, round(top * (step + 1) / steps))

    if profile == "spike":
        # Low baseline, a sharp spike between 35%–55% of the window, then recover.
        baseline = max(1, round(peak * 0.1))
        return peak if 0.35 <= frac < 0.55 else baseline

    if profile == "breakpoint":
        # Linear ramp with no plateau, overshooting to 2× peak to find the wall.
        return max(1, round(peak * 2 * frac))

    # Unknown profile → treat as flat.
    return peak
