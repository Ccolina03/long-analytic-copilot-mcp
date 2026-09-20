"""
Tests for the model router.

All hermetic: providers are stubs, so no network call and no API key is needed.
"""

from __future__ import annotations

import pytest

from llm.provider import LLMProvider, LLMResponse, NullLLM
from llm.registry import get_model
from llm.router import ModelRouter, default_router, reset_default_router


class StubProvider(LLMProvider):
    """A provider whose availability the test controls."""

    def __init__(self, name: str, available: bool = True, free: bool = False):
        self.name = name
        self._available = available
        self.is_free = free

    def is_available(self) -> bool:
        return self._available

    def complete(self, messages, *, model, **kwargs) -> LLMResponse:
        return LLMResponse(text="stub", model=model, provider=self.name)


@pytest.fixture
def clean_env(monkeypatch):
    """Remove every SME_LLM_* var so tests don't inherit developer config."""
    import os
    for key in list(os.environ):
        if key.startswith("SME_LLM_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SME_LLM_PREFER_FREE", "0")  # rank on price by default
    reset_default_router()
    yield
    reset_default_router()


@pytest.fixture
def all_providers():
    return {
        "ollama": StubProvider("ollama", available=True, free=True),
        "groq": StubProvider("groq", available=True),
        "deepseek": StubProvider("deepseek", available=True),
        "gemini": StubProvider("gemini", available=True),
        "openrouter": StubProvider("openrouter", available=False),
    }


@pytest.fixture
def hosted_only():
    """No local Ollama — the common cloud-only deployment."""
    return {
        "ollama": StubProvider("ollama", available=False, free=True),
        "groq": StubProvider("groq", available=True),
        "deepseek": StubProvider("deepseek", available=True),
        "gemini": StubProvider("gemini", available=False),
        "openrouter": StubProvider("openrouter", available=False),
    }


class TestAvailability:
    def test_only_available_providers_are_reported(self, clean_env, all_providers):
        available = ModelRouter(all_providers).available_providers()
        assert "groq" in available
        assert "openrouter" not in available

    def test_availability_is_cached(self, clean_env):
        class CountingProvider(StubProvider):
            def __init__(self):
                super().__init__("groq", available=True)
                self.probes = 0

            def is_available(self):
                self.probes += 1
                return True

        provider = CountingProvider()
        router = ModelRouter({"groq": provider})
        router.available_providers()
        router.available_providers()
        router.resolve("nano")
        assert provider.probes == 1, "availability should be probed once"

    def test_invalidate_clears_the_cache(self, clean_env):
        provider = StubProvider("groq", available=True)
        router = ModelRouter({"groq": provider})
        assert router.available_providers() == ["groq"]

        provider._available = False
        assert router.available_providers() == ["groq"], "still cached"

        router.invalidate_availability()
        assert router.available_providers() == []

    def test_provider_raising_during_probe_counts_as_unavailable(self, clean_env):
        class ExplodingProvider(StubProvider):
            def is_available(self):
                raise OSError("socket blew up")

        router = ModelRouter({"groq": ExplodingProvider("groq")})
        assert router.available_providers() == []
        assert router.resolve("nano").is_null


class TestCheapestSelection:
    def test_picks_cheapest_hosted_model_at_tier(self, clean_env, hosted_only):
        r = ModelRouter(hosted_only).resolve("nano")
        assert not r.is_null
        # Groq llama-3.1-8b at $0.05/$0.08 is the cheapest paid token available.
        assert r.model.id == "llama-3.1-8b-instant"
        assert r.model.provider == "groq"

    def test_deep_tier_picks_a_deep_model(self, clean_env, hosted_only):
        r = ModelRouter(hosted_only).resolve("deep")
        assert r.model.tier == "deep"

    def test_never_downgrades_below_requested_tier(self, clean_env, hosted_only):
        """A nano model must never be substituted for deep design review."""
        from llm.registry import tier_index
        r = ModelRouter(hosted_only).resolve("deep")
        assert tier_index(r.model.tier) >= tier_index("deep")

    def test_unavailable_provider_models_are_excluded(self, clean_env):
        only_deepseek = {
            "ollama": StubProvider("ollama", available=False, free=True),
            "groq": StubProvider("groq", available=False),
            "deepseek": StubProvider("deepseek", available=True),
        }
        r = ModelRouter(only_deepseek).resolve("standard")
        assert r.model.provider == "deepseek"

    def test_upgrades_tier_when_nothing_at_requested_tier(self, clean_env):
        """DeepSeek has no nano model, so a nano request must upgrade."""
        only_deepseek = {"deepseek": StubProvider("deepseek", available=True)}
        r = ModelRouter(only_deepseek).resolve("nano")
        assert not r.is_null
        assert r.model.provider == "deepseek"
        assert "upgraded" in r.reason

    def test_invalid_tier_raises(self, clean_env, all_providers):
        with pytest.raises(ValueError, match="unknown tier"):
            ModelRouter(all_providers).resolve("supreme")


class TestPreferFree:
    def test_prefers_free_local_model_when_enabled(
        self, clean_env, monkeypatch, all_providers
    ):
        monkeypatch.setenv("SME_LLM_PREFER_FREE", "1")
        r = ModelRouter(all_providers).resolve("standard")
        assert r.model.provider == "ollama"
        assert r.model.is_free
        assert r.model.cost_for(1_000_000, 1_000_000) == 0.0

    def test_free_preference_applies_to_deep_tier_too(
        self, clean_env, monkeypatch, all_providers
    ):
        monkeypatch.setenv("SME_LLM_PREFER_FREE", "1")
        r = ModelRouter(all_providers).resolve("deep")
        assert r.model.is_free

    def test_falls_back_to_paid_when_no_free_provider_available(
        self, clean_env, monkeypatch, hosted_only
    ):
        monkeypatch.setenv("SME_LLM_PREFER_FREE", "1")
        r = ModelRouter(hosted_only).resolve("standard")
        assert not r.is_null
        assert not r.model.is_free

    def test_disabling_free_preference_ranks_purely_on_price(
        self, clean_env, monkeypatch, all_providers
    ):
        monkeypatch.setenv("SME_LLM_PREFER_FREE", "0")
        r = ModelRouter(all_providers).resolve("standard")
        # Free is still $0.00 so it wins on price too — but via ranking, and
        # the reason string must reflect that path.
        assert "SME_LLM_PREFER_FREE" not in r.reason


class TestNoProvidersAvailable:
    def test_resolves_to_null_with_actionable_guidance(self, clean_env):
        nothing = {"groq": StubProvider("groq", available=False)}
        r = ModelRouter(nothing).resolve("standard")
        assert r.is_null
        assert isinstance(r.provider, NullLLM)
        assert r.model is None
        # The message has to tell a new user what to actually do.
        assert "GROQ_API_KEY" in r.reason
        assert "ollama" in r.reason.lower()

    def test_null_resolution_describes_itself(self, clean_env):
        r = ModelRouter({}).resolve("deep")
        assert "no model" in r.describe()

    def test_empty_provider_map_does_not_raise(self, clean_env):
        assert ModelRouter({}).resolve("nano").is_null


class TestDisableSwitch:
    def test_disable_forces_null_even_with_providers(
        self, clean_env, monkeypatch, all_providers
    ):
        monkeypatch.setenv("SME_LLM_DISABLE", "1")
        r = ModelRouter(all_providers).resolve("deep", "kafka-clients")
        assert r.is_null
        assert "SME_LLM_DISABLE" in r.reason


class TestPerAgentPinning:
    def test_pin_selects_the_named_model(self, clean_env, monkeypatch, all_providers):
        monkeypatch.setenv("SME_LLM_MODEL_kafka_clients", "groq/llama-3.3-70b-versatile")
        r = ModelRouter(all_providers).resolve("nano", "kafka-clients")
        assert r.model.id == "llama-3.3-70b-versatile"
        assert "pinned" in r.reason

    def test_pin_overrides_free_preference(
        self, clean_env, monkeypatch, all_providers
    ):
        """An explicit pin is an instruction, not a suggestion."""
        monkeypatch.setenv("SME_LLM_PREFER_FREE", "1")
        monkeypatch.setenv("SME_LLM_MODEL_kafka_clients", "deepseek/deepseek-v4-pro")
        r = ModelRouter(all_providers).resolve("deep", "kafka-clients")
        assert r.model.id == "deepseek-v4-pro"
        assert not r.model.is_free

    def test_hyphenated_agent_id_maps_to_underscored_env_var(
        self, clean_env, monkeypatch, all_providers
    ):
        monkeypatch.setenv("SME_LLM_MODEL_group_coordinator", "groq/openai/gpt-oss-20b")
        r = ModelRouter(all_providers).resolve("nano", "group-coordinator")
        assert r.model.id == "openai/gpt-oss-20b"

    def test_pin_without_provider_prefix_still_resolves(
        self, clean_env, monkeypatch, all_providers
    ):
        monkeypatch.setenv("SME_LLM_MODEL_mirrormaker", "deepseek-flash")
        r = ModelRouter(all_providers).resolve("nano", "mirrormaker")
        assert r.model.id == "deepseek-flash"

    def test_unknown_pinned_model_degrades_to_null(
        self, clean_env, monkeypatch, all_providers
    ):
        """A typo must not silently fall back to a different model."""
        monkeypatch.setenv("SME_LLM_MODEL_mirrormaker", "groq/gpt-42-turbo")
        r = ModelRouter(all_providers).resolve("nano", "mirrormaker")
        assert r.is_null
        assert "not in the registry" in r.reason

    def test_pin_to_unavailable_provider_degrades_to_null(
        self, clean_env, monkeypatch, all_providers
    ):
        all_providers["deepseek"]._available = False
        monkeypatch.setenv("SME_LLM_MODEL_mirrormaker", "deepseek/deepseek-v4-pro")
        r = ModelRouter(all_providers).resolve("deep", "mirrormaker")
        assert r.is_null
        assert "unavailable" in r.reason

    def test_pin_for_one_agent_does_not_affect_another(
        self, clean_env, monkeypatch, all_providers
    ):
        monkeypatch.setenv("SME_LLM_MODEL_kafka_clients", "deepseek/deepseek-v4-pro")
        router = ModelRouter(all_providers)
        assert router.resolve("deep", "kafka-clients").model.id == "deepseek-v4-pro"
        assert router.resolve("nano", "group-coordinator").model.id != "deepseek-v4-pro"


class TestPerAgentTierOverride:
    def test_tier_override_changes_the_requested_tier(
        self, clean_env, monkeypatch, hosted_only
    ):
        monkeypatch.setenv("SME_LLM_TIER_group_coordinator", "deep")
        r = ModelRouter(hosted_only).resolve("nano", "group-coordinator")
        assert r.tier_requested == "deep"
        assert r.model.tier == "deep"

    def test_tier_override_can_downgrade_to_save_money(
        self, clean_env, monkeypatch, hosted_only
    ):
        """Lets you measure quality loss from running the org on cheap models."""
        monkeypatch.setenv("SME_LLM_TIER_kafka_clients", "nano")
        r = ModelRouter(hosted_only).resolve("deep", "kafka-clients")
        assert r.tier_requested == "nano"
        assert r.model.blended_cost_per_mtok() < 0.2

    def test_bad_tier_override_raises(self, clean_env, monkeypatch, hosted_only):
        monkeypatch.setenv("SME_LLM_TIER_kafka_clients", "enormous")
        with pytest.raises(ValueError):
            ModelRouter(hosted_only).resolve("deep", "kafka-clients")


class TestCostCap:
    def test_cap_excludes_models_above_the_limit(
        self, clean_env, monkeypatch, hosted_only
    ):
        """A $0.30 cap admits the cheapest standard model but no deep model."""
        monkeypatch.setenv("SME_LLM_MAX_COST_PER_MTOK", "0.30")
        r = ModelRouter(hosted_only).resolve("standard")
        assert not r.is_null
        assert r.model.blended_cost_per_mtok() <= 0.30
        assert r.model.tier == "standard", "deep models are all above the cap"

    def test_cap_that_excludes_everything_degrades_to_null(
        self, clean_env, monkeypatch, hosted_only
    ):
        monkeypatch.setenv("SME_LLM_MAX_COST_PER_MTOK", "0.0001")
        r = ModelRouter(hosted_only).resolve("deep")
        assert r.is_null
        assert "exceed" in r.reason

    def test_free_models_always_pass_any_cap(
        self, clean_env, monkeypatch, all_providers
    ):
        monkeypatch.setenv("SME_LLM_MAX_COST_PER_MTOK", "0.0")
        r = ModelRouter(all_providers).resolve("deep")
        assert not r.is_null
        assert r.model.is_free

    def test_malformed_cap_is_ignored_rather_than_fatal(
        self, clean_env, monkeypatch, hosted_only
    ):
        monkeypatch.setenv("SME_LLM_MAX_COST_PER_MTOK", "cheap please")
        r = ModelRouter(hosted_only).resolve("standard")
        assert not r.is_null


class TestResolutionDescribe:
    def test_describe_names_provider_model_and_price(
        self, clean_env, hosted_only
    ):
        text = ModelRouter(hosted_only).resolve("nano").describe()
        assert "groq" in text
        assert "llama-3.1-8b-instant" in text
        assert "tier=nano" in text

    def test_describe_says_free_for_zero_cost_models(
        self, clean_env, monkeypatch, all_providers
    ):
        monkeypatch.setenv("SME_LLM_PREFER_FREE", "1")
        assert "free" in ModelRouter(all_providers).resolve("deep").describe()

    def test_model_id_is_none_when_null(self, clean_env):
        assert ModelRouter({}).resolve("nano").model_id == "none"


class TestDefaultRouter:
    def test_default_router_is_a_singleton(self, clean_env):
        assert default_router() is default_router()

    def test_reset_creates_a_new_instance(self, clean_env):
        first = default_router()
        reset_default_router()
        assert default_router() is not first

    def test_default_router_works_with_no_keys_configured(self, clean_env):
        """Must not raise in a bare environment — the common first-run case."""
        result = default_router().resolve("standard", "mirrormaker")
        assert result is not None
        assert isinstance(result.reason, str) and result.reason
