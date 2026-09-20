"""Tests for per-ticket cost accounting and budget enforcement."""

from __future__ import annotations

import pytest

from llm.budget import BudgetExceeded, BudgetLedger, TicketBudget
from llm.provider import LLMResponse, TokenUsage


def resp(cost: float, *, model="m", provider="p",
         inp=1000, out=200, cached=0) -> LLMResponse:
    return LLMResponse(
        text="x", model=model, provider=provider,
        usage=TokenUsage(input_tokens=inp, output_tokens=out,
                         cached_input_tokens=cached),
        cost_usd=cost,
    )


class TestTokenUsage:
    def test_total_is_input_plus_output(self):
        assert TokenUsage(input_tokens=100, output_tokens=50).total_tokens == 150

    def test_uncached_input_excludes_cached(self):
        u = TokenUsage(input_tokens=1000, cached_input_tokens=800)
        assert u.uncached_input_tokens == 200

    def test_uncached_never_negative(self):
        u = TokenUsage(input_tokens=100, cached_input_tokens=500)
        assert u.uncached_input_tokens == 0

    def test_addition_sums_all_fields(self):
        total = (TokenUsage(10, 20, 5) + TokenUsage(1, 2, 1))
        assert (total.input_tokens, total.output_tokens,
                total.cached_input_tokens) == (11, 22, 6)


class TestRecording:
    def test_records_a_call(self):
        b = TicketBudget("T-1")
        rec = b.record(agent_id="mirrormaker", tier="deep", response=resp(0.001))
        assert b.call_count == 1
        assert rec.agent_id == "mirrormaker"
        assert rec.tier == "deep"

    def test_accumulates_cost(self):
        b = TicketBudget("T-1")
        b.record(agent_id="a", tier="nano", response=resp(0.001))
        b.record(agent_id="a", tier="nano", response=resp(0.002))
        assert b.total_cost_usd == pytest.approx(0.003)

    def test_accumulates_tokens(self):
        b = TicketBudget("T-1")
        b.record(agent_id="a", tier="nano", response=resp(0.0, inp=1000, out=100))
        b.record(agent_id="a", tier="nano", response=resp(0.0, inp=2000, out=300))
        assert b.total_usage.input_tokens == 3000
        assert b.total_usage.output_tokens == 400

    def test_purpose_is_retained_for_attribution(self):
        b = TicketBudget("T-1")
        b.record(agent_id="a", tier="deep", response=resp(0.0),
                 purpose="principal_review:round2")
        assert b.calls[0].purpose == "principal_review:round2"


class TestAttribution:
    def test_cost_by_agent(self):
        b = TicketBudget("T-1")
        b.record(agent_id="mirrormaker", tier="standard", response=resp(0.005))
        b.record(agent_id="kafka-clients", tier="deep", response=resp(0.020))
        b.record(agent_id="kafka-clients", tier="deep", response=resp(0.010))
        assert b.cost_by_agent() == {
            "mirrormaker": pytest.approx(0.005),
            "kafka-clients": pytest.approx(0.030),
        }

    def test_cost_by_tier_shows_where_money_goes(self):
        """The expected shape: deep tier dominates spend despite fewer calls."""
        b = TicketBudget("T-1")
        for _ in range(20):
            b.record(agent_id="a", tier="nano", response=resp(0.0001))
        for _ in range(3):
            b.record(agent_id="b", tier="deep", response=resp(0.010))
        by_tier = b.cost_by_tier()
        assert by_tier["deep"] > by_tier["nano"]

    def test_cost_by_model(self):
        b = TicketBudget("T-1")
        b.record(agent_id="a", tier="nano",
                 response=resp(0.001, provider="groq", model="llama-3.1-8b-instant"))
        assert "groq/llama-3.1-8b-instant" in b.cost_by_model()

    def test_empty_budget_has_empty_attribution(self):
        b = TicketBudget("T-1")
        assert b.cost_by_agent() == {}
        assert b.total_cost_usd == 0.0


class TestLimitEnforcement:
    def test_staying_under_limit_does_not_raise(self):
        b = TicketBudget("T-1", limit_usd=0.10)
        b.record(agent_id="a", tier="nano", response=resp(0.05))
        assert b.total_cost_usd == pytest.approx(0.05)

    def test_exceeding_limit_raises(self):
        b = TicketBudget("T-1", limit_usd=0.10)
        b.record(agent_id="a", tier="nano", response=resp(0.09))
        with pytest.raises(BudgetExceeded) as exc:
            b.record(agent_id="a", tier="nano", response=resp(0.05))
        assert exc.value.ticket_id == "T-1"
        assert exc.value.limit == 0.10

    def test_ledger_still_records_the_overrunning_call(self):
        """The call was made and billed, so it must appear in the ledger."""
        b = TicketBudget("T-1", limit_usd=0.01)
        with pytest.raises(BudgetExceeded):
            b.record(agent_id="a", tier="deep", response=resp(0.5))
        assert b.call_count == 1
        assert b.total_cost_usd == pytest.approx(0.5)

    def test_exactly_at_limit_is_allowed(self):
        b = TicketBudget("T-1", limit_usd=0.10)
        b.record(agent_id="a", tier="nano", response=resp(0.10))
        assert b.remaining_usd == 0.0

    def test_would_exceed_predicts_without_recording(self):
        b = TicketBudget("T-1", limit_usd=0.10)
        b.record(agent_id="a", tier="nano", response=resp(0.08))
        assert b.would_exceed(0.05)
        assert not b.would_exceed(0.01)
        assert b.call_count == 1, "would_exceed must not record anything"

    def test_remaining_never_goes_negative(self):
        b = TicketBudget("T-1", limit_usd=0.10)
        with pytest.raises(BudgetExceeded):
            b.record(agent_id="a", tier="deep", response=resp(1.0))
        assert b.remaining_usd == 0.0

    def test_free_models_never_trip_the_budget(self):
        b = TicketBudget("T-1", limit_usd=0.001)
        for _ in range(500):
            b.record(agent_id="a", tier="deep", response=resp(0.0))
        assert b.total_cost_usd == 0.0
        assert b.call_count == 500


class TestCacheHitRate:
    def test_zero_when_nothing_cached(self):
        b = TicketBudget("T-1")
        b.record(agent_id="a", tier="nano", response=resp(0.0, inp=1000, cached=0))
        assert b.cache_hit_rate == 0.0

    def test_zero_when_no_calls(self):
        assert TicketBudget("T-1").cache_hit_rate == 0.0

    def test_full_when_everything_cached(self):
        b = TicketBudget("T-1")
        b.record(agent_id="a", tier="nano", response=resp(0.0, inp=1000, cached=1000))
        assert b.cache_hit_rate == 1.0

    def test_reflects_multi_round_prefix_reuse(self):
        """Round 1 is a cache miss; rounds 2 and 3 should mostly hit."""
        b = TicketBudget("T-1")
        b.record(agent_id="a", tier="deep", response=resp(0.0, inp=5000, cached=0))
        b.record(agent_id="a", tier="deep", response=resp(0.0, inp=5500, cached=5000))
        b.record(agent_id="a", tier="deep", response=resp(0.0, inp=6000, cached=5000))
        assert b.cache_hit_rate > 0.6


class TestSummary:
    def test_summary_of_empty_budget_explains_the_fallback(self):
        text = TicketBudget("T-1").summary()
        assert "0 LLM calls" in text
        assert "authored fallback" in text

    def test_summary_includes_cost_agents_and_tiers(self):
        b = TicketBudget("T-42", limit_usd=0.50)
        b.record(agent_id="mirrormaker", tier="standard", response=resp(0.004))
        b.record(agent_id="kafka-clients", tier="deep", response=resp(0.021))
        text = b.summary()
        assert "T-42" in text
        assert "mirrormaker" in text
        assert "kafka-clients" in text
        assert "deep" in text
        assert "0.0250" in text

    def test_summary_is_multiline_and_readable(self):
        b = TicketBudget("T-1")
        b.record(agent_id="a", tier="nano", response=resp(0.001))
        assert len(b.summary().splitlines()) >= 3


class TestBudgetLedger:
    def test_creates_a_budget_per_ticket(self):
        ledger = BudgetLedger()
        assert ledger.for_ticket("T-1") is ledger.for_ticket("T-1")
        assert ledger.for_ticket("T-2") is not ledger.for_ticket("T-1")
        assert ledger.ticket_count == 2

    def test_applies_the_default_limit(self):
        assert BudgetLedger(default_limit_usd=0.25).for_ticket("T-1").limit_usd == 0.25

    def test_per_ticket_limit_overrides_default(self):
        ledger = BudgetLedger(default_limit_usd=0.25)
        assert ledger.for_ticket("T-1", limit_usd=2.0).limit_usd == 2.0

    def test_totals_across_tickets(self):
        ledger = BudgetLedger()
        ledger.for_ticket("T-1").record(agent_id="a", tier="nano", response=resp(0.01))
        ledger.for_ticket("T-2").record(agent_id="a", tier="nano", response=resp(0.03))
        assert ledger.total_cost_usd == pytest.approx(0.04)

    def test_cost_per_ticket_is_the_unit_economics_number(self):
        ledger = BudgetLedger()
        for i in range(4):
            ledger.for_ticket(f"T-{i}").record(
                agent_id="a", tier="nano", response=resp(0.01))
        assert ledger.cost_per_ticket() == pytest.approx(0.01)

    def test_cost_per_ticket_is_zero_with_no_tickets(self):
        assert BudgetLedger().cost_per_ticket() == 0.0

    def test_summary_with_no_tickets(self):
        assert "No tickets" in BudgetLedger().summary()

    def test_summary_reports_average(self):
        ledger = BudgetLedger()
        ledger.for_ticket("T-1").record(agent_id="a", tier="nano", response=resp(0.02))
        assert "average per ticket" in ledger.summary()
