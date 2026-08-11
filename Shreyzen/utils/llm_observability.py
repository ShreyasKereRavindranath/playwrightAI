"""
LLM cost/latency observability + budget guardrails.

This is the application-side policy that plugs into the provider-neutral hooks
in `llm.policies.metrics`. It does four things:

  1. Observe   — time and cost every LLM call, attribute it to the calling
                 feature (heal/summary/triage/…), and persist it to
                 logs_and_reports/llm_usage.db for reporting (tools/llm_usage.py).
  2. Cost      — estimate USD per call from a pricing table (overridable via
                 config/llm_pricing.json); local providers cost $0.
  3. Guardrail — enforce per-process ceilings on spend / tokens / calls. When a
                 ceiling is hit, further calls are blocked (LLMBudgetExceeded);
                 because every AI feature routes through LLMClient — which
                 swallows errors and returns ""/{} — they degrade to their
                 deterministic offline path instead of overspending.
  4. Cache     — optional reuse of identical prompts to cut cost and latency.

`install()` is called once from llm.service.get_service(); it's idempotent and
reads Config live, so toggling flags takes effect without re-installing.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from llm.policies.metrics import CallRecord, metrics

logger = logging.getLogger(__name__)

_DB_PATH = Path("logs_and_reports/llm_usage.db")
_PRICING_OVERRIDE = Path("config/llm_pricing.json")


# ── Pricing ────────────────────────────────────────────────────────────────
# USD per 1M tokens, (input, output). Matched by longest model-name prefix, so
# version suffixes (e.g. claude-opus-4-8-20260101) resolve to their family.
# Approximate list prices — override precisely via config/llm_pricing.json.
_PRICING: dict[str, tuple[float, float]] = {
    # OpenAI
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
    "o4-mini": (1.10, 4.40),
    # Anthropic
    "claude-opus-4": (15.00, 75.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-sonnet": (3.00, 15.00),
    "claude-haiku-4-5": (0.80, 4.00),
    "claude-haiku": (0.80, 4.00),
    # Google
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-1.5-flash": (0.075, 0.30),
    # Local providers are free.
    "ollama": (0.0, 0.0),
    "lmstudio": (0.0, 0.0),
}

_pricing_cache: Optional[dict] = None


def _pricing_table() -> dict:
    global _pricing_cache
    if _pricing_cache is not None:
        return _pricing_cache
    table = dict(_PRICING)
    if _PRICING_OVERRIDE.exists():
        try:
            raw = json.loads(_PRICING_OVERRIDE.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.debug("Ignoring unparseable %s: %s", _PRICING_OVERRIDE, exc)
            raw = {}
        for model, price in raw.items():
            # Keys starting with "_" or "//" are treated as comments/metadata.
            if model.startswith(("_", "//")):
                continue
            # A single bad entry must not discard the rest of the overrides.
            try:
                if isinstance(price, dict):  # {"input": .., "output": ..}
                    table[model.lower()] = (float(price.get("input", 0)), float(price.get("output", 0)))
                elif isinstance(price, (list, tuple)):  # [input, output]
                    table[model.lower()] = (float(price[0]), float(price[1]))
                else:
                    logger.debug("Ignoring bad price entry for %r in %s", model, _PRICING_OVERRIDE)
            except (TypeError, ValueError, IndexError):
                logger.debug("Ignoring bad price entry for %r in %s", model, _PRICING_OVERRIDE)
    _pricing_cache = table
    return table


def estimate_cost(provider: str, model: str, input_tokens: int, output_tokens: int) -> float:
    """USD estimate for one call. Unknown models (and local providers) → 0.0."""
    table = _pricing_table()
    key = (model or "").lower().strip()
    price = None
    if key in table:
        price = table[key]
    else:
        # longest matching prefix wins (handles dated/version suffixes)
        matches = [k for k in table if key.startswith(k)]
        if matches:
            price = table[max(matches, key=len)]
    if price is None and (provider or "").lower() in table:
        price = table[provider.lower()]
    if price is None:
        return 0.0
    return round(input_tokens / 1_000_000 * price[0] + output_tokens / 1_000_000 * price[1], 6)


# ── Budget guardrail ──────────────────────────────────────────────────────────

class LLMBudgetExceeded(RuntimeError):
    """Raised by the guard when a per-process LLM ceiling is hit."""


@dataclass
class _Budget:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


_budget = _Budget()
_budget_lock = threading.Lock()
_budget_warned = False


def budget_state() -> dict:
    with _budget_lock:
        return {"calls": _budget.calls, "input_tokens": _budget.input_tokens,
                "output_tokens": _budget.output_tokens,
                "total_tokens": _budget.total_tokens, "cost_usd": round(_budget.cost_usd, 6)}


def reset_budget() -> None:
    global _budget_warned
    with _budget_lock:
        _budget.calls = 0
        _budget.input_tokens = _budget.output_tokens = 0
        _budget.cost_usd = 0.0
        _budget_warned = False


def _over_budget() -> Optional[str]:
    """Return a human reason if a ceiling is already reached, else None."""
    from config.config import Config
    with _budget_lock:
        if Config.LLM_MAX_COST_USD and _budget.cost_usd >= Config.LLM_MAX_COST_USD:
            return f"cost ${_budget.cost_usd:.4f} ≥ cap ${Config.LLM_MAX_COST_USD:.4f}"
        if Config.LLM_MAX_TOKENS and _budget.total_tokens >= Config.LLM_MAX_TOKENS:
            return f"tokens {_budget.total_tokens} ≥ cap {Config.LLM_MAX_TOKENS}"
        if Config.LLM_MAX_CALLS and _budget.calls >= Config.LLM_MAX_CALLS:
            return f"calls {_budget.calls} ≥ cap {Config.LLM_MAX_CALLS}"
    return None


def _guard(provider: str, operation: str, feature: str) -> None:
    global _budget_warned
    reason = _over_budget()
    if reason:
        if not _budget_warned:
            logger.warning("LLM budget reached (%s) — blocking further calls; "
                           "AI features fall back to offline until reset.", reason)
            _budget_warned = True
        raise LLMBudgetExceeded(reason)


# ── Feature attribution (stack inspection) ─────────────────────────────────────

_INFRA = ("llm.", "utils.llm_client", "utils.llm_observability", "contextlib")


def _infer_feature() -> str:
    """The first non-infra module up the stack — e.g. 'ai_summary', 'healer'."""
    depth = 1
    while depth < 40:
        try:
            frame = sys._getframe(depth)
        except ValueError:
            break
        mod = frame.f_globals.get("__name__", "") or ""
        if mod and not any(mod == p or mod.startswith(p) for p in _INFRA):
            return mod.split(".")[-1]
        depth += 1
    return "unknown"


# ── Persistent usage store ──────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_calls (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT    NOT NULL,
    provider      TEXT    DEFAULT '',
    model         TEXT    DEFAULT '',
    operation     TEXT    DEFAULT 'complete',
    feature       TEXT    DEFAULT '',
    input_tokens  INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cost_usd      REAL    DEFAULT 0.0,
    latency_ms    INTEGER DEFAULT 0,
    ok            INTEGER DEFAULT 1,
    cached        INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_llm_feature ON llm_calls (feature);
CREATE INDEX IF NOT EXISTS idx_llm_model   ON llm_calls (model);
CREATE TABLE IF NOT EXISTS llm_cache (
    key      TEXT PRIMARY KEY,
    kind     TEXT NOT NULL,
    value    TEXT NOT NULL,
    created  TEXT NOT NULL
);
"""


class LLMUsageStore:
    """SQLite store of individual LLM calls + the optional response cache."""

    def __init__(self, db_path: Optional[Path] = None):
        self._db = db_path or _DB_PATH
        self._db.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _now(self) -> str:
        from datetime import datetime
        return datetime.now().isoformat(timespec="seconds")

    def record(self, rec: CallRecord) -> None:
        try:
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO llm_calls (ts, provider, model, operation, feature, "
                    "input_tokens, output_tokens, cost_usd, latency_ms, ok, cached) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (self._now(), rec.provider, rec.model, rec.operation, rec.feature,
                     rec.input_tokens, rec.output_tokens, rec.cost_usd,
                     int(rec.latency_s * 1000), int(rec.ok), int(rec.cached)),
                )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("LLMUsageStore.record failed: %s", exc)

    # -- reporting queries ---------------------------------------------------

    def totals(self) -> dict:
        try:
            with self._conn() as conn:
                r = conn.execute(
                    "SELECT COUNT(*) n, SUM(ok) oks, SUM(cached) cached, "
                    "SUM(input_tokens) itok, SUM(output_tokens) otok, "
                    "SUM(cost_usd) cost, AVG(latency_ms) avg_ms FROM llm_calls"
                ).fetchone()
            n = r["n"] or 0
            return {"calls": n, "ok": r["oks"] or 0, "failures": n - (r["oks"] or 0),
                    "cached": r["cached"] or 0, "input_tokens": r["itok"] or 0,
                    "output_tokens": r["otok"] or 0, "cost_usd": round(r["cost"] or 0.0, 6),
                    "avg_latency_ms": round(r["avg_ms"] or 0.0, 1)}
        except Exception as exc:  # pragma: no cover
            logger.debug("LLMUsageStore.totals failed: %s", exc)
            return {"calls": 0}

    def by(self, dimension: str) -> list:
        if dimension not in ("provider", "model", "feature"):
            raise ValueError("dimension must be provider | model | feature")
        try:
            with self._conn() as conn:
                rows = conn.execute(
                    f"SELECT {dimension} AS key, COUNT(*) calls, SUM(cached) cached, "
                    "SUM(input_tokens) itok, SUM(output_tokens) otok, "
                    "SUM(cost_usd) cost, AVG(latency_ms) avg_ms "
                    f"FROM llm_calls GROUP BY {dimension} ORDER BY cost DESC, calls DESC"
                ).fetchall()
            return [{"key": r["key"] or "?", "calls": r["calls"], "cached": r["cached"] or 0,
                     "input_tokens": r["itok"] or 0, "output_tokens": r["otok"] or 0,
                     "cost_usd": round(r["cost"] or 0.0, 6),
                     "avg_latency_ms": round(r["avg_ms"] or 0.0, 1)} for r in rows]
        except Exception as exc:  # pragma: no cover
            logger.debug("LLMUsageStore.by failed: %s", exc)
            return []

    def latency_percentiles(self) -> dict:
        try:
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT latency_ms FROM llm_calls WHERE cached=0 ORDER BY latency_ms"
                ).fetchall()
            vals = [r["latency_ms"] for r in rows]
            return _percentiles(vals)
        except Exception as exc:  # pragma: no cover
            logger.debug("LLMUsageStore.latency_percentiles failed: %s", exc)
            return {}

    def recent(self, limit: int = 20) -> list:
        try:
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT ts, provider, model, feature, input_tokens, output_tokens, "
                    "cost_usd, latency_ms, ok, cached FROM llm_calls "
                    "ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:  # pragma: no cover
            logger.debug("LLMUsageStore.recent failed: %s", exc)
            return []

    # -- response cache ------------------------------------------------------

    def cache_get(self, key: str) -> Optional[str]:
        try:
            with self._conn() as conn:
                row = conn.execute("SELECT value FROM llm_cache WHERE key=?", (key,)).fetchone()
            return row["value"] if row else None
        except Exception:  # pragma: no cover
            return None

    def cache_set(self, key: str, kind: str, value: str) -> None:
        try:
            with self._conn() as conn:
                conn.execute("INSERT OR REPLACE INTO llm_cache (key, kind, value, created) "
                             "VALUES (?,?,?,?)", (key, kind, value, self._now()))
        except Exception as exc:  # pragma: no cover
            logger.debug("LLMUsageStore.cache_set failed: %s", exc)


def _percentiles(sorted_vals: list) -> dict:
    if not sorted_vals:
        return {}

    def pct(p: float):
        idx = min(len(sorted_vals) - 1, int(round((p / 100) * (len(sorted_vals) - 1))))
        return sorted_vals[idx]

    return {"count": len(sorted_vals), "p50_ms": pct(50), "p90_ms": pct(90),
            "p95_ms": pct(95), "max_ms": sorted_vals[-1]}


_store: Optional[LLMUsageStore] = None


def get_store() -> LLMUsageStore:
    global _store
    if _store is None:
        _store = LLMUsageStore()
    return _store


# ── The sink: cost + feature + budget + persistence ────────────────────────────

def _sink(rec: CallRecord) -> None:
    from config.config import Config
    rec.feature = rec.feature or _infer_feature()
    rec.cost_usd = estimate_cost(rec.provider, rec.model, rec.input_tokens, rec.output_tokens)
    # Real (non-cached) calls accrue against the budget ceilings.
    if not rec.cached:
        with _budget_lock:
            _budget.calls += 1
            _budget.input_tokens += rec.input_tokens
            _budget.output_tokens += rec.output_tokens
            _budget.cost_usd += rec.cost_usd
    if Config.LLM_OBSERVABILITY:
        get_store().record(rec)


# ── Response cache (opt-in), used by utils.llm_client ───────────────────────────

_mem_cache: dict[str, str] = {}


def _cache_key(kind: str, provider: str, model: str, material: str) -> str:
    h = hashlib.sha256(f"{kind}\x1f{provider}\x1f{model}\x1f{material}".encode("utf-8"))
    return h.hexdigest()


def maybe_cached(kind: str, provider: str, model: str, material: str, produce):
    """Return a cached result for identical inputs, else run `produce` and cache it.

    `kind` is 'text' or 'json'. A hit emits a cached CallRecord (so it shows in
    reports and hit-rate) and never counts against the budget. A no-op when
    LLM_CACHE_ENABLED is off. Only truthy results are cached.
    """
    from config.config import Config
    if not Config.LLM_CACHE_ENABLED:
        return produce()

    key = _cache_key(kind, provider, model, material)
    raw = _mem_cache.get(key)
    if raw is None and Config.LLM_OBSERVABILITY:
        raw = get_store().cache_get(key)
        if raw is not None:
            _mem_cache[key] = raw
    if raw is not None:
        metrics.emit(CallRecord(provider=provider, model=model, operation=kind,
                                cached=True, ok=True))
        return json.loads(raw) if kind == "json" else raw

    value = produce()
    if value:  # don't cache empty/failed results
        raw = json.dumps(value) if kind == "json" else str(value)
        _mem_cache[key] = raw
        if Config.LLM_OBSERVABILITY:
            get_store().cache_set(key, kind, raw)
    return value


# ── Installation ────────────────────────────────────────────────────────────

_installed = False


def install() -> None:
    """Register the sink + guard onto the LLM metrics hooks. Idempotent."""
    global _installed
    if _installed:
        return
    metrics.add_sink(_sink)
    metrics.add_guard(_guard)
    _installed = True
