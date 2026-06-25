"""Cost tracker — matches cc-mini's token usage and cost tracking."""

from __future__ import annotations

from typing import Any

# Claude model pricing per million tokens (input / output)
_CLAUDE_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-6": (15.0, 75.0),
    "claude-opus-4-5": (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-sonnet-4": (3.0, 15.0),
    "claude-haiku-4": (1.0, 5.0),
    "claude-opus-4-1": (15.0, 75.0),
    "claude-opus-4": (15.0, 75.0),
    "claude-3-7-sonnet": (3.0, 15.0),
    "claude-3-5-sonnet": (3.0, 15.0),
    "claude-3-5-haiku": (0.8, 4.0),
    "claude-3-haiku": (0.25, 1.25),
}

# DeepSeek pricing (approximate)
_DEEPSEEK_PRICING: dict[str, tuple[float, float]] = {
    "deepseek-v4-flash": (0.14, 0.28),
    "deepseek-v4-pro": (0.55, 2.19),
}


class CostTracker:
    """Tracks cumulative token usage and estimates cost.

    Matches cc-mini's CostTracker with input/output/cache token accounting.
    """

    def __init__(self) -> None:
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cache_read_tokens = 0
        self.total_cache_creation_tokens = 0
        self.total_cost_usd = 0.0
        self.lines_added = 0
        self.lines_removed = 0
        self.last_input_tokens = 0
        self.total_requests = 0

    def add_usage(
        self,
        model: str,
        usage: dict[str, int],
        api_duration_s: float = 0.0,
        advisor_model: str | None = None,
    ) -> None:
        """Record token usage from one API call."""
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        cache_read = usage.get("cache_read_input_tokens", 0)
        cache_create = usage.get("cache_creation_input_tokens", 0)

        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_cache_read_tokens += cache_read
        self.total_cache_creation_tokens += cache_create
        self.last_input_tokens = input_tokens
        self.total_requests += 1

        # Estimate cost
        pricing = self._get_pricing(model)
        if pricing is not None:
            input_price, output_price = pricing
            billable_input = input_tokens - cache_read
            cost = (billable_input / 1_000_000) * input_price + (output_tokens / 1_000_000) * output_price
            if cache_create > 0:
                cost += (cache_create / 1_000_000) * (input_price * 0.25)
            self.total_cost_usd += cost

    def add_lines_changed(self, added: int, removed: int) -> None:
        self.lines_added += added
        self.lines_removed += removed

    def format_cost(self) -> str:
        return (
            f"Tokens: {self.total_input_tokens:,} in / {self.total_output_tokens:,} out  "
            f"·  Cost: ${self.total_cost_usd:.4f}  "
            f"·  Requests: {self.total_requests}"
        )

    @staticmethod
    def _get_pricing(model: str) -> tuple[float, float] | None:
        for prefix, prices in _CLAUDE_PRICING.items():
            if model.startswith(prefix):
                return prices
        for prefix, prices in _DEEPSEEK_PRICING.items():
            if model.startswith(prefix):
                return prices
        return (3.0, 15.0)  # default Claude pricing
