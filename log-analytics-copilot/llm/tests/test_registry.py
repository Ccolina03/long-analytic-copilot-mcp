"""Tests for the model registry: tiers, pricing, and cost math."""

from __future__ import annotations

import datetime as _dt

import pytest

from llm.registry import (
    CATALOG,
    TIERS,
    ModelSpec,
    deepseek_is_peak,
    free_models,
    get_model,
    models_for_tier,
    providers,
    tier_index,
)


class TestCatalogIntegrity:
    def test_catalog_is_not_empty(self):
        assert len(CATALOG) > 0

    def test_every_model_has_a_valid_tier(self):
        for m in CATALOG:
            assert m.tier in TIERS, f"{m.id} has invalid tier {m.tier}"

    def test_model_ids_are_unique_per_provider(self):
        seen = set()
        for m in CATALOG:
            key = (m.provider, m.id)
            assert key not in seen, f"duplicate model {key}"
            seen.add(key)

    def test_every_tier_has_at_least_one_model(self):
        """A tier with no model would make that tier unroutable."""
        for tier in TIERS:
            exact = [m for m in CATALOG if m.tier == tier]
            assert exact, f"no model registered at tier {tier!r}"

    def test_prices_are_non_negative(self):
        for m in CATALOG:
            assert m.input_per_mtok >= 0
            assert m.output_per_mtok >= 0
            assert m.cached_input_per_mtok >= 0

    def test_cached_input_never_costs_more_than_uncached(self):
        for m in CATALOG:
            if m.cached_input_per_mtok > 0:
                assert m.cached_input_per_mtok <= m.input_per_mtok, (
                    f"{m.id}: cache hit priced above cache miss"
                )

    def test_rejects_unknown_tier(self):
        with pytest.raises(ValueError, match="tier must be one of"):
            ModelSpec(id="x", provider="p", tier="galaxy-brain",
                      input_per_mtok=1.0, output_per_mtok=1.0)

    def test_free_tier_providers_are_represented(self):
        """The zero-cost story depends on these existing."""
        assert "ollama" in providers(), "need a free local provider"
        assert "groq" in providers(), "need a provider with a free hosted tier"


class TestFreeModels:
    def test_ollama_models_are_free(self):
        for m in CATALOG:
            if m.provider == "ollama":
                assert m.is_free
                assert m.blended_cost_per_mtok() == 0.0

    def test_free_models_cover_every_tier(self):
        """Every tier must be servable at $0.00, or 'run it free' is a lie."""
        free_tiers = {m.tier for m in free_models()}
        assert free_tiers == set(TIERS), f"tiers without a free option: {set(TIERS) - free_tiers}"

    def test_paid_models_are_not_free(self):
        groq_nano = get_model("llama-3.1-8b-instant", "groq")
        assert groq_nano is not None
        assert not groq_nano.is_free


class TestBlendedCost:
    def test_blended_cost_weights_input_heavier_by_default(self):
        """Agent workloads are input-heavy, so the default ratio favors input."""
        m = ModelSpec(id="t", provider="p", tier="nano",
                      input_per_mtok=1.0, output_per_mtok=5.0)
        # 0.75 * 1.0 + 0.25 * 5.0 = 2.0
        assert m.blended_cost_per_mtok() == pytest.approx(2.0)

    def test_blended_cost_all_input(self):
        m = ModelSpec(id="t", provider="p", tier="nano",
                      input_per_mtok=2.0, output_per_mtok=99.0)
        assert m.blended_cost_per_mtok(output_ratio=0.0) == pytest.approx(2.0)

    def test_blended_cost_all_output(self):
        m = ModelSpec(id="t", provider="p", tier="nano",
                      input_per_mtok=2.0, output_per_mtok=99.0)
        assert m.blended_cost_per_mtok(output_ratio=1.0) == pytest.approx(99.0)

    def test_rejects_out_of_range_ratio(self):
        m = ModelSpec(id="t", provider="p", tier="nano",
                      input_per_mtok=1.0, output_per_mtok=1.0)
        with pytest.raises(ValueError):
            m.blended_cost_per_mtok(output_ratio=1.5)
        with pytest.raises(ValueError):
            m.blended_cost_per_mtok(output_ratio=-0.1)

    def test_expensive_output_can_outrank_cheap_input(self):
        """Ranking on input price alone would pick the wrong model here."""
        cheap_in_pricey_out = ModelSpec(
            id="a", provider="p", tier="nano",
            input_per_mtok=0.10, output_per_mtok=10.0)
        flat = ModelSpec(
            id="b", provider="p", tier="nano",
            input_per_mtok=0.50, output_per_mtok=0.50)
        assert cheap_in_pricey_out.input_per_mtok < flat.input_per_mtok
        assert cheap_in_pricey_out.blended_cost_per_mtok() > flat.blended_cost_per_mtok()


class TestCostForCall:
    def test_cost_of_one_million_input_tokens_equals_input_price(self):
        m = ModelSpec(id="t", provider="p", tier="nano",
                      input_per_mtok=0.15, output_per_mtok=0.60)
        assert m.cost_for(1_000_000, 0) == pytest.approx(0.15)

    def test_cost_of_one_million_output_tokens_equals_output_price(self):
        m = ModelSpec(id="t", provider="p", tier="nano",
                      input_per_mtok=0.15, output_per_mtok=0.60)
        assert m.cost_for(0, 1_000_000) == pytest.approx(0.60)

    def test_cost_is_zero_for_free_model(self):
        m = get_model("qwen2.5:14b", "ollama")
        assert m is not None
        assert m.cost_for(500_000, 100_000) == 0.0

    def test_cached_tokens_are_billed_at_the_cache_rate(self):
        m = ModelSpec(id="t", provider="p", tier="nano",
                      input_per_mtok=0.15, output_per_mtok=0.60,
                      cached_input_per_mtok=0.003)
        full = m.cost_for(1_000_000, 0)
        all_cached = m.cost_for(1_000_000, 0, cached_input_tokens=1_000_000)
        assert full == pytest.approx(0.15)
        assert all_cached == pytest.approx(0.003)
        assert all_cached < full / 10

    def test_partial_cache_hit_splits_the_bill(self):
        m = ModelSpec(id="t", provider="p", tier="nano",
                      input_per_mtok=1.0, output_per_mtok=1.0,
                      cached_input_per_mtok=0.0)
        # Half cached at $0.00, half at $1.00/M
        assert m.cost_for(1_000_000, 0, cached_input_tokens=500_000) == pytest.approx(0.5)

    def test_cached_exceeding_input_does_not_go_negative(self):
        m = ModelSpec(id="t", provider="p", tier="nano",
                      input_per_mtok=1.0, output_per_mtok=1.0,
                      cached_input_per_mtok=0.1)
        assert m.cost_for(100, 0, cached_input_tokens=500) >= 0

    def test_deliberation_round_on_deepseek_is_sub_cent(self):
        """Sanity check the actual unit economics claim.

        A deliberation round sends roughly a 6k-token prompt and gets back
        about 800 tokens. On deepseek-flash that must cost well under a cent,
        otherwise the whole 'cheap multi-agent' premise is wrong.
        """
        m = get_model("deepseek-flash", "deepseek")
        assert m is not None
        cost = m.cost_for(6_000, 800)
        assert cost < 0.01, f"one round cost ${cost:.5f}, expected < $0.01"


class TestModelsForTier:
    def test_returns_models_sorted_cheapest_first(self):
        models = models_for_tier("standard")
        costs = [m.blended_cost_per_mtok() for m in models]
        assert costs == sorted(costs)

    def test_includes_stronger_tiers_by_default(self):
        """A deep model can do nano work when nothing cheaper is available."""
        nano = models_for_tier("nano", allow_stronger=True)
        assert any(m.tier == "deep" for m in nano)

    def test_excludes_stronger_tiers_when_disallowed(self):
        nano = models_for_tier("nano", allow_stronger=False)
        assert all(m.tier == "nano" for m in nano)

    def test_never_includes_weaker_tiers(self):
        """A nano model must never be offered for deep design review."""
        deep = models_for_tier("deep")
        assert all(m.tier == "deep" for m in deep)

    def test_free_local_model_ranks_first_for_every_tier(self):
        for tier in TIERS:
            first = models_for_tier(tier)[0]
            assert first.is_free, f"tier {tier} does not rank a free model first"

    def test_unknown_tier_raises(self):
        with pytest.raises(ValueError, match="unknown tier"):
            models_for_tier("turbo")


class TestTierIndex:
    def test_tiers_are_ordered_weakest_to_strongest(self):
        assert tier_index("nano") < tier_index("small")
        assert tier_index("small") < tier_index("standard")
        assert tier_index("standard") < tier_index("deep")

    def test_unknown_tier_raises(self):
        with pytest.raises(ValueError):
            tier_index("nope")


class TestGetModel:
    def test_finds_model_by_id(self):
        assert get_model("deepseek-flash") is not None

    def test_provider_filter_is_respected(self):
        assert get_model("deepseek-flash", "deepseek") is not None
        assert get_model("deepseek-flash", "groq") is None

    def test_unknown_model_returns_none(self):
        assert get_model("gpt-9-ultra") is None


class TestDeepSeekPeakPricing:
    """Peak is 01:00-04:00 and 06:00-10:00 UTC on weekdays (US nighttime)."""

    def _utc(self, year, month, day, hour):
        return _dt.datetime(year, month, day, hour, tzinfo=_dt.timezone.utc)

    def test_weekday_inside_first_peak_window(self):
        assert deepseek_is_peak(self._utc(2026, 9, 21, 2))   # Monday 02:00

    def test_weekday_inside_second_peak_window(self):
        assert deepseek_is_peak(self._utc(2026, 9, 21, 7))   # Monday 07:00

    def test_weekday_between_the_two_windows_is_off_peak(self):
        assert not deepseek_is_peak(self._utc(2026, 9, 21, 5))

    def test_us_working_hours_are_off_peak(self):
        """The practical payoff: a US team pays the off-peak rate."""
        assert not deepseek_is_peak(self._utc(2026, 9, 21, 18))  # Mon 11am PT

    def test_weekends_are_always_off_peak(self):
        assert not deepseek_is_peak(self._utc(2026, 9, 19, 2))   # Saturday
        assert not deepseek_is_peak(self._utc(2026, 9, 20, 7))   # Sunday

    def test_window_boundaries_are_half_open(self):
        assert deepseek_is_peak(self._utc(2026, 9, 21, 1))       # start inclusive
        assert not deepseek_is_peak(self._utc(2026, 9, 21, 4))   # end exclusive
