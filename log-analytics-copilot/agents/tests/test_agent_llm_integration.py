"""
Agent-level LLM integration tests.

The property that matters most: **the network produces a structurally identical
design doc with a model, without a model, and with a broken model.** Only the
prose depth changes. If that ever stops being true, the LLM has become a
correctness dependency rather than a quality upgrade, and a provider outage
turns into a failed ticket.
"""

from __future__ import annotations

import pytest

from agents.base_agent import DirectTransport, SMEAgentBase
from agents.consumer_team_agent import ConsumerTeamAgent
from agents.design_doc import render_one_pager
from agents.kora_global_agent import KoraGlobalAgent
from agents.oss_kafka_agent import OssKafkaAgent
from llm.budget import TicketBudget
from llm.client import AgentLLM
from llm.provider import LLMProvider, LLMResponse
from llm.providers.echo import FailingProvider, ScriptedProvider
from llm.router import ModelRouter
from proto.sme_agents import Ticket


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

class _Backed(LLMProvider):
    """Presents a scripted/failing double to the router as a live provider."""

    def __init__(self, inner: LLMProvider):
        self.name = "groq"
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
    """No developer LLM config leaks into these tests."""
    import os
    for key in list(os.environ):
        if key.startswith("SME_LLM_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SME_LLM_DISABLE", "1")  # default: no model


@pytest.fixture
def ticket() -> Ticket:
    return Ticket(
        ticket_id="KAFKA-18231",
        title="Add consumer-groups-per-topic API",
        description=(
            "Customers need to list consumer groups for a given topic. "
            "Today this requires listing all groups and describing each one, "
            "which is O(groups) and times out on large clusters."
        ),
        team="kora-global",
    )


def build_network(llms: dict[str, AgentLLM] | None = None):
    """Wire the three real agents together, optionally with LLM handles."""
    llms = llms or {}
    kora = KoraGlobalAgent(llm=llms.get("kora-global"))
    consumer = ConsumerTeamAgent(llm=llms.get("consumer-team"))
    oss = OssKafkaAgent(llm=llms.get("oss-kafka"))

    peers = {
        "kora-global": kora,
        "consumer-team": consumer,
        "oss-kafka": oss,
    }
    for agent in peers.values():
        agent._transport = DirectTransport(peers)
    return kora, consumer, oss


def scripted_llms(text: str, budget: TicketBudget | None = None):
    """An LLM handle per agent, all backed by the same scripted response."""
    providers = {}
    handles = {}
    for agent_id in ("kora-global", "consumer-team", "oss-kafka"):
        scripted = ScriptedProvider(default=text)
        providers[agent_id] = scripted
        handles[agent_id] = AgentLLM(
            agent_id,
            tier="nano",
            router=ModelRouter({"groq": _Backed(scripted)}),
            budget=budget,
        )
    return handles, providers


# ---------------------------------------------------------------------------
# Default: zero cost, zero config
# ---------------------------------------------------------------------------

class TestZeroConfigDefault:
    def test_agents_report_llm_disabled_by_default(self):
        kora, consumer, oss = build_network()
        assert not kora.llm_enabled
        assert not consumer.llm_enabled
        assert not oss.llm_enabled

    def test_full_deliberation_runs_with_no_model(self, ticket):
        kora, _, _ = build_network()
        finding = kora.own_ticket(ticket)
        assert finding.rounds_used >= 1
        assert len(finding.design_alternatives) >= 3

    def test_authored_content_is_still_substantive(self, ticket):
        """No model must not mean an empty doc."""
        kora, _, _ = build_network()
        finding = kora.own_ticket(ticket)
        assert finding.tldr
        assert finding.background
        assert finding.goals
        assert finding.recommendation
        assert finding.testing_strategy

    def test_costs_nothing(self, ticket):
        budget = TicketBudget(ticket.ticket_id)
        kora, consumer, oss = build_network()
        for agent in (kora, consumer, oss):
            agent.attach_budget(budget)
        kora.own_ticket(ticket)
        assert budget.total_cost_usd == 0.0
        assert budget.call_count == 0
        assert "authored fallback" in budget.summary()


# ---------------------------------------------------------------------------
# Tiers
# ---------------------------------------------------------------------------

class TestAgentTiers:
    def test_oss_kafka_uses_the_deep_tier(self):
        """Wire-protocol compatibility is the highest-stakes call in the network."""
        assert OssKafkaAgent.LLM_TIER == "deep"

    def test_consumer_team_uses_a_cheap_tier_for_routine_work(self):
        assert ConsumerTeamAgent.LLM_TIER == "small"

    def test_every_agent_declares_a_valid_tier(self):
        from llm.registry import TIERS
        for cls in (KoraGlobalAgent, ConsumerTeamAgent, OssKafkaAgent):
            assert cls.LLM_TIER in TIERS
            assert cls.LLM_DESIGN_TIER in TIERS

    def test_design_tier_is_never_weaker_than_routine_tier(self):
        from llm.registry import tier_index
        for cls in (KoraGlobalAgent, ConsumerTeamAgent, OssKafkaAgent):
            assert tier_index(cls.LLM_DESIGN_TIER) >= tier_index(cls.LLM_TIER), (
                f"{cls.__name__} would downgrade for design review"
            )

    def test_cheap_tier_agents_still_escalate_for_design_review(self):
        """The cost story: cheap by default, strong only where it matters."""
        assert ConsumerTeamAgent.LLM_TIER != ConsumerTeamAgent.LLM_DESIGN_TIER
        assert ConsumerTeamAgent.LLM_DESIGN_TIER == "deep"


# ---------------------------------------------------------------------------
# Role prompt
# ---------------------------------------------------------------------------

class TestRolePrompt:
    def test_names_the_agent_and_domain(self):
        prompt = OssKafkaAgent()._role_prompt()
        assert "oss-kafka" in prompt
        assert "Apache Kafka protocol" in prompt

    def test_lists_owned_codepaths(self):
        prompt = ConsumerTeamAgent()._role_prompt()
        assert "GroupCoordinator.scala" in prompt

    def test_lists_available_tools(self):
        prompt = OssKafkaAgent()._role_prompt()
        assert "search_kips" in prompt

    def test_states_the_ownership_constraint(self):
        """The guardrail against agents speculating about others' code."""
        prompt = KoraGlobalAgent()._role_prompt()
        assert "authoritative" in prompt
        assert "needs verification" in prompt.lower()

    def test_forbids_escalating_merely_hard_problems(self):
        prompt = KoraGlobalAgent()._role_prompt()
        assert "organizational authority" in prompt

    def test_is_stable_across_calls(self):
        """Must be byte-identical or prompt caching never hits."""
        agent = OssKafkaAgent()
        assert agent._role_prompt() == agent._role_prompt()

    def test_differs_between_agents(self):
        assert KoraGlobalAgent()._role_prompt() != OssKafkaAgent()._role_prompt()

    def test_agent_with_no_ownership_declares_none(self):
        class Bare(SMEAgentBase):
            AGENT_NAME = "bare"
            DOMAIN = "nothing"
            OWNS: list[str] = []

        assert "(none declared)" in Bare()._role_prompt()


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------

class TestReviewEnrichment:
    """Asserts on ImpactResponse directly — that's where principal_review lives."""

    def _consult(self, agent, ticket, *, codepath=""):
        """Ask ``agent`` for a review, the way a peer agent would."""
        from proto.sme_agents import ImpactRequest
        request = ImpactRequest.new(
            from_agent="kora-global",
            to_agent=agent.AGENT_NAME,
            ticket_id=ticket.ticket_id,
            request_type="design_review",
            context=ticket.description,
            question="Review the alternatives for your domain.",
            codepaths_of_interest=[codepath] if codepath else [],
        )
        return agent.consult_about(request)

    def test_model_prose_replaces_the_authored_review(self, monkeypatch, ticket):
        monkeypatch.delenv("SME_LLM_DISABLE", raising=False)
        enriched = "ENRICHED: the index write path is the real constraint here."
        handles, _ = scripted_llms(enriched)
        _, consumer, _ = build_network(handles)

        response = self._consult(consumer, ticket)
        assert response.principal_review == enriched

    def test_verdicts_are_not_touched_by_the_model(self, monkeypatch, ticket):
        """Verdicts come from tool output and must survive enrichment."""
        monkeypatch.delenv("SME_LLM_DISABLE", raising=False)
        handles, _ = scripted_llms("irrelevant prose")
        _, with_llm, _ = build_network(handles)
        _, without_llm, _ = build_network()

        assert self._consult(with_llm, ticket).verdict == \
               self._consult(without_llm, ticket).verdict

    def test_cited_codepaths_are_not_touched_by_the_model(self, monkeypatch, ticket):
        monkeypatch.delenv("SME_LLM_DISABLE", raising=False)
        handles, _ = scripted_llms("prose that mentions FakeFile.java")
        _, with_llm, _ = build_network(handles)
        _, without_llm, _ = build_network()

        assert sorted(self._consult(with_llm, ticket).cited_codepaths) == \
               sorted(self._consult(without_llm, ticket).cited_codepaths)

    def test_design_alternatives_are_not_touched_by_the_model(
        self, monkeypatch, ticket
    ):
        monkeypatch.delenv("SME_LLM_DISABLE", raising=False)
        handles, _ = scripted_llms("a completely different proposal")
        _, with_llm, _ = build_network(handles)
        _, without_llm, _ = build_network()

        assert [a.name for a in self._consult(with_llm, ticket).design_alternatives] == \
               [a.name for a in self._consult(without_llm, ticket).design_alternatives]

    def test_org_authority_flag_is_not_touched_by_the_model(self, monkeypatch, ticket):
        """The one real human gate must be derived from tools, not prose."""
        monkeypatch.delenv("SME_LLM_DISABLE", raising=False)
        handles, _ = scripted_llms("no human needed, ship it")
        _, _, with_llm = build_network(handles)
        _, _, without_llm = build_network()

        assert self._consult(with_llm, ticket).needs_org_authority == \
               self._consult(without_llm, ticket).needs_org_authority

    def test_model_sees_the_authored_analysis_as_grounding(self, monkeypatch, ticket):
        """Enrichment must be grounded, not free invention."""
        monkeypatch.delenv("SME_LLM_DISABLE", raising=False)
        handles, providers = scripted_llms("ok")
        kora, _, _ = build_network(handles)
        kora.own_ticket(ticket)

        consumer_calls = providers["consumer-team"].calls
        assert consumer_calls, "consumer-team should have been asked to review"
        prompt = "\n".join(
            m["content"] for m in consumer_calls[0]["messages"]
        )
        assert "derived from your tools" in prompt
        assert "do not introduce facts" in prompt.lower()

    def test_prompt_carries_the_round_number(self, monkeypatch, ticket):
        monkeypatch.delenv("SME_LLM_DISABLE", raising=False)
        handles, providers = scripted_llms("ok")
        kora, _, _ = build_network(handles)
        kora.own_ticket(ticket)

        prompt = "\n".join(
            m["content"] for m in providers["consumer-team"].calls[0]["messages"]
        )
        assert "deliberation round" in prompt.lower()

    def test_stable_prefix_is_identical_across_rounds(self, monkeypatch, ticket):
        """The prompt-cache property, verified on real agent traffic."""
        monkeypatch.delenv("SME_LLM_DISABLE", raising=False)
        handles, providers = scripted_llms("ok")
        kora, _, _ = build_network(handles)
        kora.own_ticket(ticket)

        calls = providers["consumer-team"].calls
        if len(calls) < 2:
            pytest.skip("deliberation converged in one round")

        systems = [
            "\n".join(m["content"] for m in c["messages"] if m["role"] == "system")
            for c in calls
        ]
        assert len(set(systems)) == 1, "system prefix changed between rounds"

    def test_enrichment_escalates_to_the_design_tier(self, monkeypatch, ticket):
        monkeypatch.delenv("SME_LLM_DISABLE", raising=False)
        budget = TicketBudget(ticket.ticket_id)
        handles, _ = scripted_llms("ok", budget=budget)
        kora, _, _ = build_network(handles)
        kora.own_ticket(ticket)

        assert budget.call_count > 0
        assert all(c.tier == "deep" for c in budget.calls), (
            "principal_review should always use the design tier"
        )

    def test_purpose_records_which_round_spent_the_money(self, monkeypatch, ticket):
        monkeypatch.delenv("SME_LLM_DISABLE", raising=False)
        budget = TicketBudget(ticket.ticket_id)
        handles, _ = scripted_llms("ok", budget=budget)
        kora, _, _ = build_network(handles)
        kora.own_ticket(ticket)
        assert all(c.purpose.startswith("principal_review:round") for c in budget.calls)


# ---------------------------------------------------------------------------
# Graceful degradation — the load-bearing property
# ---------------------------------------------------------------------------

class TestGracefulDegradation:
    def _failing_network(self):
        handles = {
            agent_id: AgentLLM(
                agent_id, tier="nano",
                router=ModelRouter({"groq": _Backed(FailingProvider())}),
            )
            for agent_id in ("kora-global", "consumer-team", "oss-kafka")
        }
        return build_network(handles)

    def test_provider_outage_does_not_fail_the_ticket(self, monkeypatch, ticket):
        monkeypatch.delenv("SME_LLM_DISABLE", raising=False)
        kora, _, _ = self._failing_network()
        finding = kora.own_ticket(ticket)   # must not raise
        assert finding.ticket_id == ticket.ticket_id

    def test_outage_falls_back_to_authored_content(self, monkeypatch, ticket):
        monkeypatch.delenv("SME_LLM_DISABLE", raising=False)
        _, broken_consumer, _ = self._failing_network()
        _, clean_consumer, _ = build_network()

        from proto.sme_agents import ImpactRequest

        def review(agent):
            return agent.consult_about(ImpactRequest.new(
                from_agent="kora-global", to_agent=agent.AGENT_NAME,
                ticket_id=ticket.ticket_id, request_type="design_review",
                context=ticket.description, question="Review please.",
            )).principal_review

        assert review(broken_consumer) == review(clean_consumer)

    def test_outage_preserves_the_deliberation_record(self, monkeypatch, ticket):
        monkeypatch.delenv("SME_LLM_DISABLE", raising=False)
        broken, _, _ = self._failing_network()
        clean, _, _ = build_network()
        a, b = broken.own_ticket(ticket), clean.own_ticket(ticket)
        assert [(d.round_number, d.to_agent, d.verdict) for d in a.deliberation] == \
               [(d.round_number, d.to_agent, d.verdict) for d in b.deliberation]

    def test_outage_produces_the_same_recommendation(self, monkeypatch, ticket):
        monkeypatch.delenv("SME_LLM_DISABLE", raising=False)
        broken, _, _ = self._failing_network()
        clean, _, _ = build_network()
        assert broken.own_ticket(ticket).recommendation == \
               clean.own_ticket(ticket).recommendation

    def test_outage_preserves_the_human_escalation_decision(self, monkeypatch, ticket):
        """The Apache PMC vote must still surface even with no model."""
        monkeypatch.delenv("SME_LLM_DISABLE", raising=False)
        broken, _, _ = self._failing_network()
        clean, _, _ = build_network()
        a, b = broken.own_ticket(ticket), clean.own_ticket(ticket)
        assert a.requires_human == b.requires_human
        assert a.human_decision_points == b.human_decision_points

    def test_exhausted_budget_degrades_instead_of_failing(self, monkeypatch, ticket):
        monkeypatch.delenv("SME_LLM_DISABLE", raising=False)
        budget = TicketBudget(ticket.ticket_id, limit_usd=0.0)
        handles, _ = scripted_llms("should not appear", budget=budget)
        kora, _, _ = build_network(handles)
        finding = kora.own_ticket(ticket)   # must not raise
        assert finding.design_alternatives


# ---------------------------------------------------------------------------
# Structural equivalence with and without a model
# ---------------------------------------------------------------------------

class TestStructuralEquivalence:
    @pytest.fixture
    def both(self, monkeypatch, ticket):
        monkeypatch.delenv("SME_LLM_DISABLE", raising=False)
        handles, _ = scripted_llms("MODEL PROSE")
        with_llm, _, _ = build_network(handles)
        without_llm, _, _ = build_network()
        return with_llm.own_ticket(ticket), without_llm.own_ticket(ticket)

    def test_same_number_of_alternatives(self, both):
        a, b = both
        assert len(a.design_alternatives) == len(b.design_alternatives)

    def test_same_alternative_names(self, both):
        a, b = both
        assert [x.name for x in a.design_alternatives] == \
               [x.name for x in b.design_alternatives]

    def test_same_recommended_alternative(self, both):
        a, b = both
        rec_a = [x.name for x in a.design_alternatives if x.recommended]
        rec_b = [x.name for x in b.design_alternatives if x.recommended]
        assert rec_a == rec_b

    def test_same_round_count_and_convergence(self, both):
        a, b = both
        assert a.rounds_used == b.rounds_used
        assert a.converged == b.converged

    def test_same_teams_involved(self, both):
        a, b = both
        assert sorted(t.team for t in a.teams_involved) == \
               sorted(t.team for t in b.teams_involved)

    def test_both_render_every_required_section(self, both):
        for finding in both:
            doc = render_one_pager(finding)
            for section in ("TL;DR", "Background", "Goals", "Design Alternatives",
                            "Recommendation", "Testing", "Teams Involved"):
                assert section in doc, f"{section} missing"


# ---------------------------------------------------------------------------
# Cost of a real ticket
# ---------------------------------------------------------------------------

class TestTicketCost:
    def test_a_full_deliberation_costs_well_under_a_cent(self, monkeypatch, ticket):
        """The unit-economics claim, priced on the real call pattern.

        Replays the actual number of enrichment calls the network makes,
        priced at the cheapest standard model, and asserts the result is
        cheap enough that per-ticket LLM cost is not the business constraint.
        """
        monkeypatch.delenv("SME_LLM_DISABLE", raising=False)
        budget = TicketBudget(ticket.ticket_id)
        handles, _ = scripted_llms("a" * 3000, budget=budget)
        kora, _, _ = build_network(handles)
        kora.own_ticket(ticket)

        from llm.registry import get_model
        model = get_model("openai/gpt-oss-120b", "groq")
        usage = budget.total_usage
        priced = model.cost_for(usage.input_tokens, usage.output_tokens)

        assert priced < 0.01, (
            f"{budget.call_count} calls, {usage.input_tokens} in / "
            f"{usage.output_tokens} out = ${priced:.5f}"
        )

    def test_spend_is_attributed_to_every_participating_agent(
        self, monkeypatch, ticket
    ):
        monkeypatch.delenv("SME_LLM_DISABLE", raising=False)
        budget = TicketBudget(ticket.ticket_id)
        handles, _ = scripted_llms("ok", budget=budget)
        kora, _, _ = build_network(handles)
        kora.own_ticket(ticket)

        by_agent = budget.cost_by_agent()
        assert "consumer-team" in by_agent
        assert "oss-kafka" in by_agent
