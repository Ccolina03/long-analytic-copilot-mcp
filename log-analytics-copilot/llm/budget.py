"""
Per-ticket cost accounting and budget enforcement.

Two jobs:

1. **Attribution.** Every LLM call is attributed to (ticket, agent, tier,
   model), so "what did this ticket cost, and which agent spent it" is
   answerable. Without this, multi-agent deliberation is an unbounded spend:
   3 agents x 3 rounds x several calls each compounds quietly.

2. **A hard ceiling.** ``BudgetExceeded`` stops a runaway deliberation. The
   agent catches it and falls back to authored content rather than failing the
   ticket, so exceeding budget degrades quality instead of losing work.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .provider import LLMResponse, TokenUsage


class BudgetExceeded(RuntimeError):
    """Raised when a ticket's spend would pass its ceiling."""

    def __init__(self, ticket_id: str, spent: float, limit: float):
        self.ticket_id = ticket_id
        self.spent = spent
        self.limit = limit
        super().__init__(
            f"ticket {ticket_id}: spent ${spent:.4f} of ${limit:.4f} budget"
        )


@dataclass
class CallRecord:
    """One LLM call, attributed."""

    agent_id: str
    tier: str
    provider: str
    model: str
    usage: TokenUsage
    cost_usd: float
    purpose: str = ""


@dataclass
class TicketBudget:
    """Cost ledger for one ticket.

    The default ceiling of $0.50 is deliberately low. A full 3-agent, 3-round
    deliberation on cheap models lands well under a cent, so $0.50 only trips
    on a genuine runaway (a retry loop, or an accidental frontier-model pin).
    """

    ticket_id: str
    limit_usd: float = 0.50
    calls: list[CallRecord] = field(default_factory=list)

    # -- recording ---------------------------------------------------------

    def record(
        self,
        *,
        agent_id: str,
        tier: str,
        response: LLMResponse,
        purpose: str = "",
    ) -> CallRecord:
        """Record a completed call. Raises if it pushes us over the limit."""
        rec = CallRecord(
            agent_id=agent_id,
            tier=tier,
            provider=response.provider,
            model=response.model,
            usage=response.usage,
            cost_usd=response.cost_usd,
            purpose=purpose,
        )
        self.calls.append(rec)

        # Checked after appending so the ledger reflects what actually
        # happened — the call was already made and already billed.
        if self.total_cost_usd > self.limit_usd:
            raise BudgetExceeded(self.ticket_id, self.total_cost_usd, self.limit_usd)
        return rec

    def would_exceed(self, estimated_cost: float) -> bool:
        """True if a call costing ``estimated_cost`` would break the ceiling."""
        return (self.total_cost_usd + estimated_cost) > self.limit_usd

    # -- aggregates --------------------------------------------------------

    @property
    def total_cost_usd(self) -> float:
        return sum(c.cost_usd for c in self.calls)

    @property
    def total_usage(self) -> TokenUsage:
        total = TokenUsage()
        for c in self.calls:
            total = total + c.usage
        return total

    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.limit_usd - self.total_cost_usd)

    def cost_by_agent(self) -> dict[str, float]:
        out: dict[str, float] = defaultdict(float)
        for c in self.calls:
            out[c.agent_id] += c.cost_usd
        return dict(out)

    def cost_by_tier(self) -> dict[str, float]:
        out: dict[str, float] = defaultdict(float)
        for c in self.calls:
            out[c.tier] += c.cost_usd
        return dict(out)

    def cost_by_model(self) -> dict[str, float]:
        out: dict[str, float] = defaultdict(float)
        for c in self.calls:
            out[f"{c.provider}/{c.model}"] += c.cost_usd
        return dict(out)

    @property
    def cache_hit_rate(self) -> float:
        """Share of input tokens served from a prompt cache, 0.0-1.0.

        A multi-round deliberation resends the same prefix every round, so a
        healthy rate here is the main lever on cost. A low rate means the
        prompt prefix is not stable across rounds.
        """
        usage = self.total_usage
        if usage.input_tokens == 0:
            return 0.0
        return usage.cached_input_tokens / usage.input_tokens

    # -- reporting ---------------------------------------------------------

    def summary(self) -> str:
        """Human-readable cost report for the ticket."""
        if not self.calls:
            return (
                f"Ticket {self.ticket_id}: 0 LLM calls, $0.0000 "
                "(authored fallback — no model configured)"
            )

        usage = self.total_usage
        lines = [
            f"Ticket {self.ticket_id}: {self.call_count} LLM calls, "
            f"${self.total_cost_usd:.4f} of ${self.limit_usd:.2f} budget",
            f"  tokens: {usage.input_tokens:,} in "
            f"({self.cache_hit_rate:.0%} cached) / {usage.output_tokens:,} out",
        ]

        by_agent = self.cost_by_agent()
        if by_agent:
            lines.append("  by agent:")
            for agent, cost in sorted(by_agent.items(), key=lambda kv: -kv[1]):
                lines.append(f"    {agent:<20} ${cost:.4f}")

        by_tier = self.cost_by_tier()
        if by_tier:
            lines.append("  by tier:")
            for tier, cost in sorted(by_tier.items(), key=lambda kv: -kv[1]):
                lines.append(f"    {tier:<20} ${cost:.4f}")

        return "\n".join(lines)


class BudgetLedger:
    """Holds a ``TicketBudget`` per ticket, for org-wide totals."""

    def __init__(self, default_limit_usd: float = 0.50):
        self.default_limit_usd = default_limit_usd
        self._budgets: dict[str, TicketBudget] = {}

    def for_ticket(self, ticket_id: str, limit_usd: float | None = None) -> TicketBudget:
        """Get or create the budget for ``ticket_id``."""
        if ticket_id not in self._budgets:
            self._budgets[ticket_id] = TicketBudget(
                ticket_id=ticket_id,
                limit_usd=self.default_limit_usd if limit_usd is None else limit_usd,
            )
        return self._budgets[ticket_id]

    @property
    def total_cost_usd(self) -> float:
        return sum(b.total_cost_usd for b in self._budgets.values())

    @property
    def ticket_count(self) -> int:
        return len(self._budgets)

    def cost_per_ticket(self) -> float:
        """Average spend per ticket — the unit-economics number."""
        if not self._budgets:
            return 0.0
        return self.total_cost_usd / len(self._budgets)

    def summary(self) -> str:
        if not self._budgets:
            return "No tickets processed."
        return "\n".join([
            f"{self.ticket_count} tickets, ${self.total_cost_usd:.4f} total, "
            f"${self.cost_per_ticket():.4f} average per ticket",
            *(b.summary() for b in self._budgets.values()),
        ])
