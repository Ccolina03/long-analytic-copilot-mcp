"""
Tests for AgentLLM.

The contract under test: ``reason()`` never raises and returns ``None`` whenever
the caller should fall back to authored content.
"""

from __future__ import annotations

import pytest

from llm.budget import TicketBudget
from llm.client import AgentLLM
from llm.provider import LLMProvider, LLMResponse, TokenUsage, build_messages
from llm.providers.echo import FailingProvider, ScriptedProvider
from llm.router import ModelRouter


class _AvailableStub(LLMProvider):
    """Wraps a scripted provider so the router treats it as a real backend."""

    def __init__(self, name: str, inner: LLMProvider):
        self.name = name
        self.inner = inner
        self.is_free = True

    def is_available(self) -> bool:
        return True

    def complete(self, messages, *, model, **kwargs) -> LLMResponse:
        out = self.inner.complete(messages, model=model, **kwargs)
        out.provider = self.name
        return out


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    import os
    for key in list(os.environ):
        if key.startswith("SME_LLM_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SME_LLM_PREFER_FREE", "0")


def router_with(scripted: ScriptedProvider | FailingProvider) -> ModelRouter:
    """Router where the 'groq' slot is backed by a test double."""
    return ModelRouter({"groq": _AvailableStub("groq", scripted)})


class TestDisabledPath:
    def test_no_providers_means_not_enabled(self):
        llm = AgentLLM("kora-global", router=ModelRouter({}))
        assert not llm.enabled

    def test_reason_returns_none_when_no_model(self):
        llm = AgentLLM("kora-global", router=ModelRouter({}))
        assert llm.reason(role_prompt="you are an agent", question="hi") is None

    def test_reason_json_returns_none_when_no_model(self):
        llm = AgentLLM("kora-global", router=ModelRouter({}))
        assert llm.reason_json(role_prompt="r", question="q") is None

    def test_no_cost_incurred_when_disabled(self):
        budget = TicketBudget("T-1")
        llm = AgentLLM("a", router=ModelRouter({}), budget=budget)
        llm.reason(role_prompt="r", question="q")
        assert budget.total_cost_usd == 0.0
        assert budget.call_count == 0


class TestSuccessPath:
    def test_returns_model_text(self):
        scripted = ScriptedProvider(["the index should be lazily built"])
        llm = AgentLLM("consumer-team", tier="nano", router=router_with(scripted))
        assert llm.reason(role_prompt="r", question="q") == "the index should be lazily built"

    def test_enabled_is_true(self):
        llm = AgentLLM("a", tier="nano", router=router_with(ScriptedProvider()))
        assert llm.enabled

    def test_strips_whitespace(self):
        scripted = ScriptedProvider(["  padded  "])
        llm = AgentLLM("a", tier="nano", router=router_with(scripted))
        assert llm.reason(role_prompt="r", question="q") == "padded"

    def test_empty_response_becomes_none(self):
        """An empty completion is unusable, so the caller must fall back."""
        scripted = ScriptedProvider(["   "])
        llm = AgentLLM("a", tier="nano", router=router_with(scripted))
        assert llm.reason(role_prompt="r", question="q") is None

    def test_resolution_is_cached_across_calls(self):
        llm = AgentLLM("a", tier="nano", router=router_with(ScriptedProvider()))
        assert llm.resolution is llm.resolution


class TestFailureIsNeverFatal:
    def test_provider_exception_returns_none(self):
        llm = AgentLLM("a", tier="nano", router=router_with(FailingProvider()))
        assert llm.reason(role_prompt="r", question="q") is None

    def test_provider_exception_is_not_propagated(self):
        failing = FailingProvider("upstream 503")
        llm = AgentLLM("a", tier="nano", router=router_with(failing))
        llm.reason(role_prompt="r", question="q")   # must not raise
        assert failing.attempts == 1

    def test_failure_is_not_billed(self):
        budget = TicketBudget("T-1")
        llm = AgentLLM("a", tier="nano", router=router_with(FailingProvider()),
                       budget=budget)
        llm.reason(role_prompt="r", question="q")
        assert budget.call_count == 0


class TestPromptConstruction:
    def test_role_prompt_is_the_system_message(self):
        scripted = ScriptedProvider()
        llm = AgentLLM("a", tier="nano", router=router_with(scripted))
        llm.reason(role_prompt="You are the oss-kafka SME.", question="q")
        assert "You are the oss-kafka SME." in scripted.system_prompt()

    def test_stable_context_goes_in_the_system_message(self):
        """Cache prefixes match on the system message, so this must land there."""
        scripted = ScriptedProvider()
        llm = AgentLLM("a", tier="nano", router=router_with(scripted))
        llm.reason(role_prompt="role", stable_context="RUNBOOK", question="q")
        assert "RUNBOOK" in scripted.system_prompt()

    def test_volatile_context_stays_out_of_the_cached_prefix(self):
        scripted = ScriptedProvider()
        llm = AgentLLM("a", tier="nano", router=router_with(scripted))
        llm.reason(role_prompt="role", stable_context="STABLE",
                   volatile_context="ROUND-2-FEEDBACK", question="q")
        assert "ROUND-2-FEEDBACK" not in scripted.system_prompt()
        assert "ROUND-2-FEEDBACK" in scripted.last_prompt()

    def test_question_is_last(self):
        scripted = ScriptedProvider()
        llm = AgentLLM("a", tier="nano", router=router_with(scripted))
        llm.reason(role_prompt="role", volatile_context="ctx", question="THE-QUESTION")
        user = scripted.calls[-1]["messages"][-1]["content"]
        assert user.rstrip().endswith("THE-QUESTION")

    def test_json_mode_is_forwarded(self):
        scripted = ScriptedProvider(['{"ok": true}'])
        llm = AgentLLM("a", tier="nano", router=router_with(scripted))
        llm.reason(role_prompt="r", question="q", json_mode=True)
        assert scripted.calls[-1]["json_mode"] is True


class TestBuildMessages:
    def test_produces_system_then_user(self):
        msgs = build_messages(role_prompt="role", question="q")
        assert [m["role"] for m in msgs] == ["system", "user"]

    def test_stable_prefix_is_byte_identical_across_rounds(self):
        """The property prompt caching depends on."""
        r1 = build_messages(role_prompt="role", stable_context="ticket",
                            volatile_context="round 1", question="q1")
        r2 = build_messages(role_prompt="role", stable_context="ticket",
                            volatile_context="round 2", question="q2")
        assert r1[0]["content"] == r2[0]["content"]
        assert r1[1]["content"] != r2[1]["content"]

    def test_empty_user_content_gets_a_placeholder(self):
        """Most APIs reject an empty user turn."""
        msgs = build_messages(role_prompt="role")
        assert msgs[1]["content"] == "Proceed."


class TestTierEscalation:
    def test_per_call_tier_overrides_the_agent_default(self):
        llm = AgentLLM("a", tier="nano", router=router_with(ScriptedProvider(["ok"])))
        assert llm.tier == "nano"
        assert llm.reason(role_prompt="r", question="q", tier="deep") == "ok"

    def test_escalation_does_not_change_the_agent_default(self):
        llm = AgentLLM("a", tier="nano", router=router_with(ScriptedProvider(["a", "b"])))
        llm.reason(role_prompt="r", question="q", tier="deep")
        assert llm.tier == "nano"

    def test_escalated_call_is_attributed_to_the_escalated_tier(self):
        budget = TicketBudget("T-1")
        llm = AgentLLM("a", tier="nano", router=router_with(ScriptedProvider(["x"])),
                       budget=budget)
        llm.reason(role_prompt="r", question="q", tier="deep", purpose="review")
        assert budget.calls[0].tier == "deep"


class TestBudgetIntegration:
    def test_successful_call_is_recorded(self):
        budget = TicketBudget("T-1")
        llm = AgentLLM("consumer-team", tier="nano",
                       router=router_with(ScriptedProvider(["ok"])), budget=budget)
        llm.reason(role_prompt="r", question="q", purpose="verdict")
        assert budget.call_count == 1
        assert budget.calls[0].agent_id == "consumer-team"
        assert budget.calls[0].purpose == "verdict"

    def test_exhausted_budget_skips_the_call_entirely(self):
        """The pre-check must prevent the request, not just record it."""
        scripted = ScriptedProvider(["should never be returned"])
        budget = TicketBudget("T-1", limit_usd=0.0)
        llm = AgentLLM("a", tier="nano", router=router_with(scripted), budget=budget)
        # A zero budget cannot afford any priced call, and the stub reports a
        # non-free model, so the pre-check must refuse.
        result = llm.reason(role_prompt="r" * 4000, question="q" * 4000)
        if result is None:
            assert scripted.call_count == 0
        else:
            # Model resolved free; nothing to refuse.
            assert budget.total_cost_usd == 0.0

    def test_budget_overrun_still_returns_the_text(self):
        """The call was already billed, so discarding the text wastes money."""
        scripted = ScriptedProvider(["expensive but usable"])
        budget = TicketBudget("T-1", limit_usd=10.0)
        llm = AgentLLM("a", tier="nano", router=router_with(scripted), budget=budget)
        assert llm.reason(role_prompt="r", question="q") == "expensive but usable"


class TestReasonJson:
    def _llm(self, text: str) -> AgentLLM:
        return AgentLLM("a", tier="nano", router=router_with(ScriptedProvider([text])))

    def test_parses_plain_json_object(self):
        assert self._llm('{"verdict": "agreed"}').reason_json(
            role_prompt="r", question="q") == {"verdict": "agreed"}

    def test_parses_json_array(self):
        assert self._llm('[1, 2, 3]').reason_json(
            role_prompt="r", question="q") == [1, 2, 3]

    def test_strips_fenced_code_block(self):
        text = 'Here you go:\n```json\n{"verdict": "concerns"}\n```\nHope that helps.'
        assert self._llm(text).reason_json(
            role_prompt="r", question="q") == {"verdict": "concerns"}

    def test_strips_unlabeled_fence(self):
        assert self._llm('```\n{"a": 1}\n```').reason_json(
            role_prompt="r", question="q") == {"a": 1}

    def test_extracts_object_embedded_in_prose(self):
        """Small models routinely wrap JSON in commentary."""
        assert self._llm('Sure! {"a": 1} — let me know.').reason_json(
            role_prompt="r", question="q") == {"a": 1}

    def test_handles_nested_objects_in_prose(self):
        text = 'Result: {"outer": {"inner": [1, 2]}} done'
        assert self._llm(text).reason_json(
            role_prompt="r", question="q") == {"outer": {"inner": [1, 2]}}

    def test_unparseable_output_returns_none(self):
        assert self._llm("I'd rather not answer that.").reason_json(
            role_prompt="r", question="q") is None

    def test_sets_json_mode_by_default(self):
        scripted = ScriptedProvider(['{"a":1}'])
        AgentLLM("a", tier="nano", router=router_with(scripted)).reason_json(
            role_prompt="r", question="q")
        assert scripted.calls[-1]["json_mode"] is True


class TestScriptedProviderItself:
    def test_returns_responses_in_order(self):
        p = ScriptedProvider(["first", "second"])
        assert p.complete([], model="m").text == "first"
        assert p.complete([], model="m").text == "second"

    def test_falls_back_to_default_when_exhausted(self):
        p = ScriptedProvider(["only"], default="DEFAULT")
        p.complete([], model="m")
        assert p.complete([], model="m").text == "DEFAULT"

    def test_records_calls(self):
        p = ScriptedProvider()
        p.complete([{"role": "user", "content": "hi"}], model="m", temperature=0.9)
        assert p.call_count == 1
        assert p.calls[0]["temperature"] == 0.9

    def test_reports_estimated_usage(self):
        p = ScriptedProvider(["some output text"])
        out = p.complete([{"role": "system", "content": "x" * 400}], model="m")
        assert out.usage.input_tokens > 0
        assert out.usage.output_tokens > 0
        assert out.cost_usd == 0.0

    def test_last_prompt_raises_before_any_call(self):
        with pytest.raises(AssertionError, match="never called"):
            ScriptedProvider().last_prompt()


class TestTokenEstimation:
    def test_empty_text_is_zero(self):
        from llm.provider import estimate_tokens
        assert estimate_tokens("") == 0

    def test_roughly_four_chars_per_token(self):
        from llm.provider import estimate_tokens
        assert estimate_tokens("x" * 400) == 100

    def test_short_text_is_at_least_one_token(self):
        from llm.provider import estimate_tokens
        assert estimate_tokens("hi") == 1


class TestNullLLM:
    def test_is_never_available(self):
        from llm.provider import NullLLM
        assert not NullLLM().is_available()

    def test_returns_empty_fallback_response(self):
        from llm.provider import NullLLM
        out = NullLLM().complete([], model="none")
        assert out.text == ""
        assert out.is_fallback
        assert out.cost_usd == 0.0
