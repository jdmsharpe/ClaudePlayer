"""Cost estimation and cumulative token/cost tracking for Claude API calls.

Extracted from game_agent.py to break the circular import between
game_agent ↔ memory_manager and to make cost tracking independently reusable.
"""

import json
import logging
import os
from typing import Optional

# Per-MTok pricing (USD) — (input, output, cache_read, cache_write_5min).
# Keys are checked in order via substring match, so more-specific entries MUST come first
# (e.g. "claude-opus-4-5" before "claude-opus-4" to avoid greedy shadowing).
# Cache write rate uses the 5-minute TTL tier (1.25x input).  The 1-hour tier (2x input)
# is not distinguishable from the usage object, so callers can optionally pass a multiplier.
# Thinking/reasoning tokens are billed at the output rate and included in output_tokens.
_MODEL_PRICING = {
    # Opus 4 family — 4.5 / 4.6: $5 / $25 per MTok
    "claude-opus-4-6":   ( 5.00,  25.00,  0.50,  6.25),
    "claude-opus-4-5":   ( 5.00,  25.00,  0.50,  6.25),
    # Opus 4.1 and original Opus 4: $15 / $75 per MTok
    "claude-opus-4-1":   (15.00,  75.00,  1.50, 18.75),
    "claude-opus-4":     (15.00,  75.00,  1.50, 18.75),
    # Opus 3 (deprecated): $15 / $75 per MTok
    "claude-opus-3":     (15.00,  75.00,  1.50, 18.75),
    # Sonnet 4 family — all versions: $3 / $15 per MTok
    "claude-sonnet-4-6": ( 3.00,  15.00,  0.30,  3.75),
    "claude-sonnet-4-5": ( 3.00,  15.00,  0.30,  3.75),
    "claude-sonnet-4":   ( 3.00,  15.00,  0.30,  3.75),
    # Haiku 4.5: $1 / $5 per MTok
    "claude-haiku-4-5":  ( 1.00,   5.00,  0.10,  1.25),
    # Haiku 3.5: $0.80 / $4 per MTok (must precede "claude-haiku-3" to avoid shadowing)
    "claude-haiku-3-5":  ( 0.80,   4.00,  0.08,  1.00),
    # Haiku 3: $0.25 / $1.25 per MTok
    "claude-haiku-3":    ( 0.25,   1.25,  0.03,  0.30),
}


def estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_create_tokens: int = 0,
) -> float:
    """Estimate USD cost for a single API call based on model pricing.

    Thinking/reasoning tokens are included in output_tokens by the API and
    billed at the standard output rate — no separate handling needed.
    Cache writes default to the 5-minute TTL rate (1.25x input).
    """
    # Match by substring so dated suffixes like "-20251001" are handled transparently.
    # Dict insertion order guarantees more-specific keys win over shorter prefixes.
    pricing = None
    for key, rates in _MODEL_PRICING.items():
        if key in model:
            pricing = rates
            break
    if pricing is None:
        logging.warning(f"Unknown model '{model}' — cost estimate unavailable, defaulting to Haiku 4.5 rates")
        pricing = _MODEL_PRICING["claude-haiku-4-5"]
    inp_rate, out_rate, cache_r_rate, cache_w_rate = pricing
    return (
        input_tokens * inp_rate
        + output_tokens * out_rate
        + cache_read_tokens * cache_r_rate
        + cache_create_tokens * cache_w_rate
    ) / 1_000_000


class CostTracker:
    """Accumulates per-turn token usage and cost, with JSON persistence.

    Args:
        stats_path: Path to session_stats.json for load/save. If None,
                    tracking is in-memory only (no persistence).
    """

    def __init__(self, stats_path: Optional[str] = None):
        self.stats_path = stats_path
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read_tokens = 0
        self.cache_create_tokens = 0
        self.cost_usd = 0.0

        if stats_path:
            self._load(stats_path)

    def record(
        self,
        model: str,
        input_tok: int,
        output_tok: int,
        cache_read: int = 0,
        cache_create: int = 0,
    ) -> float:
        """Record a single API call's usage. Returns the turn cost in USD."""
        turn_cost = estimate_cost(model, input_tok, output_tok, cache_read, cache_create)
        self.input_tokens += input_tok
        self.output_tokens += output_tok
        self.cache_read_tokens += cache_read
        self.cache_create_tokens += cache_create
        self.cost_usd += turn_cost
        return turn_cost

    def save(self):
        """Persist cumulative stats to JSON."""
        if not self.stats_path:
            return
        try:
            os.makedirs(os.path.dirname(self.stats_path), exist_ok=True)
            data = {
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "cache_read_tokens": self.cache_read_tokens,
                "cache_create_tokens": self.cache_create_tokens,
                "cost_usd": round(self.cost_usd, 6),
            }
            with open(self.stats_path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logging.warning(f"Failed to save session stats: {e}")

    def _load(self, path: str):
        """Load cumulative stats from JSON."""
        if not os.path.exists(path):
            return
        try:
            with open(path) as f:
                data = json.load(f)
            self.input_tokens = data.get("input_tokens", 0)
            self.output_tokens = data.get("output_tokens", 0)
            self.cache_read_tokens = data.get("cache_read_tokens", 0)
            self.cache_create_tokens = data.get("cache_create_tokens", 0)
            self.cost_usd = data.get("cost_usd", 0.0)
            logging.info(f"Session stats loaded: ${self.cost_usd:.4f} cumulative")
        except Exception as e:
            logging.warning(f"Failed to load session stats: {e}")
