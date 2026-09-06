"""Budget — token / cost / turn accounting, extracted from the Agent's inline
fields (_check_budget / _get_current_cost_usd) so every runtime instance owns
its limits instead of sharing them."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BudgetStatus:
    exceeded: bool
    reason: str | None = None


class Budget:
    """Tracks token usage and estimated USD cost against cost/turn limits.

    The cost formula mirrors Agent._get_current_cost_usd: base input $3/Mtok,
    cache read 0.1x, cache write 1.25x, output $15/Mtok."""

    def __init__(self, *, max_cost_usd: float | None = None, max_turns: int | None = None):
        self.max_cost_usd = max_cost_usd
        self.max_turns = max_turns
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read_tokens = 0
        self.cache_creation_tokens = 0

    def record_tokens(
        self,
        *,
        input: int = 0,
        output: int = 0,
        cache_read: int = 0,
        cache_creation: int = 0,
    ) -> None:
        for value in (input, output, cache_read, cache_creation):
            if value < 0:
                raise ValueError(f"token counts must be non-negative, got {value}")
        self.input_tokens += input
        self.output_tokens += output
        self.cache_read_tokens += cache_read
        self.cache_creation_tokens += cache_creation

    @property
    def cost_usd(self) -> float:
        M = 1_000_000
        return (
            (self.input_tokens / M) * 3
            + (self.cache_read_tokens / M) * 0.3
            + (self.cache_creation_tokens / M) * 3.75
            + (self.output_tokens / M) * 15
        )

    def check(self, turns: int) -> BudgetStatus:
        if self.max_cost_usd is not None and self.cost_usd >= self.max_cost_usd:
            return BudgetStatus(
                True, f"Cost limit reached (${self.cost_usd:.4f} >= ${self.max_cost_usd})"
            )
        if self.max_turns is not None and turns >= self.max_turns:
            return BudgetStatus(True, f"Turn limit reached ({turns} >= {self.max_turns})")
        return BudgetStatus(False)
