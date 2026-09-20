"""
Phase 6 — Full multi-agent integration test.

Replays the entire Part V walkthrough with all three real agents running their
real code via DirectTransport (no gRPC server, no Docker needed).

What it proves
--------------
1. mirrormaker receives the ticket and drives the investigation itself.
2. It proposes three genuinely distinct design alternatives before consulting.
3. It deliberates with group-coordinator and kafka-clients over multiple rounds.
4. group-coordinator consults kafka-clients on its own initiative — mirrormaker never asked.
5. group-coordinator pushes back substantively, correcting alternative B on a real
   correctness point rather than rubber-stamping.
6. The agents converge on a recommendation themselves.
7. requires_human is True for exactly one reason — the Apache PMC vote — and
   is decided only at the end, not mid-deliberation.
8. Every codepath every agent cites passes that agent's ownership validation.
9. The final artifact renders as a complete 1-page engineering design doc.

This test is the permanent regression guard for the whole system.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from agents.base_agent import DirectTransport
from agents.group_coordinator_agent import GroupCoordinatorAgent
from agents.kafka_broker_agent import KafkaBrokerAgent
from agents.design_doc import render_one_pager
from agents.mirrormaker_agent import MirrorMakerAgent
from agents.kafka_clients_agent import KafkaClientsAgent
from agents.ownership_validator import validate_citations
from proto.sme_agents import Ticket

_FIXTURES = pathlib.Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# The real agent network — every agent runs its real code
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def network():
    clients = KafkaClientsAgent()
    broker = KafkaBrokerAgent()
    # group-coordinator consults kafka-clients on its own initiative.
    coordinator = GroupCoordinatorAgent(
        transport=DirectTransport({"kafka-clients": clients})
    )
    mm_transport = DirectTransport({
        "group-coordinator": coordinator,
        "kafka-broker": broker,
        "kafka-clients": clients,
    })
    mirrormaker = MirrorMakerAgent(transport=mm_transport)
    return {
        "mirrormaker": mirrormaker,
        "coordinator": coordinator,
        "broker": broker,
        "clients": clients,
        "mm_transport": mm_transport,
    }


@pytest.fixture(scope="module")
def ticket():
    raw = json.loads((_FIXTURES / "group_discovery_ticket.json").read_text())
    return Ticket.from_dict(raw)


@pytest.fixture(scope="module")
def finding(network, ticket):
    """The real Finding produced by mirrormaker driving its own ticket."""
    return network["mirrormaker"].own_ticket(ticket)


@pytest.fixture(scope="module")
def doc(finding):
    return render_one_pager(finding)


# ---------------------------------------------------------------------------
# Ownership of the ticket
# ---------------------------------------------------------------------------

class TestTicketOwnership:
    def test_mirrormaker_owns_the_finding(self, finding):
        assert finding.owning_agent == "mirrormaker"

    def test_ticket_id_is_preserved(self, finding, ticket):
        assert finding.ticket_id == ticket.ticket_id

    def test_router_would_send_this_to_mirrormaker_and_nowhere_else(self, ticket):
        from router.main import get_agent_address
        addr = get_agent_address(ticket.team)
        assert addr is not None
        assert "mirrormaker" in addr or "8001" in addr


# ---------------------------------------------------------------------------
# Design alternatives
# ---------------------------------------------------------------------------

class TestThreeAlternatives:
    def test_exactly_three_alternatives(self, finding):
        assert len(finding.design_alternatives) == 3

    def test_alternatives_are_genuinely_distinct(self, finding):
        names = [a.name for a in finding.design_alternatives]
        assert len(set(names)) == 3

    def test_every_alternative_is_argued_in_depth(self, finding):
        for alt in finding.design_alternatives:
            assert len(alt.approach) > 200, f"{alt.name}: approach too shallow"
            assert len(alt.pros) >= 3, f"{alt.name}: needs real pros"
            assert len(alt.cons) >= 3, f"{alt.name}: needs real cons"
            assert alt.blast_radius, f"{alt.name}: must declare blast radius"

    def test_exactly_one_is_recommended(self, finding):
        recommended = [a for a in finding.design_alternatives if a.recommended]
        assert len(recommended) == 1

    def test_the_other_two_are_ruled_out_with_stated_reasons(self, finding):
        ruled_out = [a for a in finding.design_alternatives if a.is_ruled_out]
        assert len(ruled_out) == 2
        for alt in ruled_out:
            assert len(alt.rejected_reason) > 20

    def test_recommended_option_is_the_broker_side_index(self, finding):
        rec = finding.recommended_alternative
        assert rec is not None
        assert "index" in rec.name.lower()

    def test_all_alternatives_were_peer_reviewed(self, finding):
        for alt in finding.design_alternatives:
            assert alt.reviewed_by, f"{alt.name} was never reviewed by a peer"


# ---------------------------------------------------------------------------
# Multi-round deliberation
# ---------------------------------------------------------------------------

class TestDeliberation:
    def test_took_more_than_one_round(self, finding):
        assert finding.rounds_used >= 2, (
            "group-coordinator raises real concerns in round 1, so convergence in "
            "round 1 would mean nobody actually pushed back"
        )

    def test_stayed_within_the_round_cap(self, finding):
        assert finding.rounds_used <= MirrorMakerAgent.MAX_DELIBERATION_ROUNDS

    def test_converged(self, finding):
        assert finding.converged is True

    def test_both_peers_were_consulted(self, finding):
        consulted = {r.to_agent for r in finding.deliberation}
        assert "group-coordinator" in consulted
        assert "kafka-clients" in consulted

    def test_consumer_team_consulted_before_oss_kafka(self, finding):
        order = [r.to_agent for r in finding.deliberation]
        assert order.index("group-coordinator") < order.index("kafka-clients")

    def test_consumer_team_consulted_oss_kafka_on_its_own_initiative(self, network, ticket):
        """mirrormaker never told group-coordinator to ask kafka-clients."""
        coordinator = network["coordinator"]
        from proto.sme_agents import ImpactRequest
        req = ImpactRequest.new(
            from_agent="mirrormaker", to_agent="group-coordinator",
            ticket_id=ticket.ticket_id, request_type="design_review",
            question="Is the index implementable?",  # says nothing about kafka-clients
            round_number=1,
        )
        resp = coordinator.consult_about(req, depth=0)
        assert "kafka-clients" in resp.follow_up_consultations

    def test_new_concerns_were_raised_during_deliberation(self, finding):
        assert any(r.new_concerns_raised for r in finding.deliberation), (
            "no agent raised a single concern — they rubber-stamped it"
        )

    def test_concerns_stop_being_raised_by_the_final_round(self, finding):
        final_round = max(r.round_number for r in finding.deliberation)
        final = [r for r in finding.deliberation if r.round_number == final_round]
        assert all(not r.new_concerns_raised for r in final)

    def test_deliberation_questions_are_specific_not_generic(self, finding):
        for record in finding.deliberation:
            assert len(record.question) > 80, (
                f"round {record.round_number} question to {record.to_agent} is too vague"
            )


# ---------------------------------------------------------------------------
# Substantive pushback — the principal-engineer test
# ---------------------------------------------------------------------------

class TestSubstantivePushback:
    def test_coordinator_corrected_mirrormaker_on_a_correctness_point(self, network, ticket):
        """group-coordinator must catch that commits != live subscriptions."""
        from proto.sme_agents import ImpactRequest
        req = ImpactRequest.new(
            from_agent="mirrormaker", to_agent="group-coordinator",
            ticket_id=ticket.ticket_id, request_type="design_review",
            question="review my alternatives", round_number=1,
        )
        resp = network["coordinator"].consult_about(req, depth=0)
        joined = " ".join(resp.new_concerns).lower()
        assert "__consumer_offsets" in joined
        assert "subscription" in joined

    def test_broker_corrected_the_fan_out_assumption(self, network, ticket):
        """The reason kafka-broker is a separate team.

        Every other agent frames this as "replace an O(n) scan with an O(1)
        lookup". Only the broker team owns the fact that group→coordinator
        placement is by group-id hash, so a filtered ListGroups still has to
        fan out to all 50 shards. Nobody else can catch that.
        """
        from proto.sme_agents import ImpactRequest
        req = ImpactRequest.new(
            from_agent="mirrormaker", to_agent="kafka-broker",
            ticket_id=ticket.ticket_id, request_type="design_review",
            question="how does a filtered ListGroups route?", round_number=1,
        )
        resp = network["broker"].consult_about(req, depth=0)
        joined = (" ".join(resp.new_concerns) + resp.principal_review).lower()
        assert "fan out" in joined or "fan-out" in joined
        assert "hash" in joined
        assert "50" in joined

    def test_broker_rejected_kraft_metadata_with_a_measured_reason(
        self, network, ticket
    ):
        """Rejections must cite numbers, not instinct."""
        from proto.sme_agents import ImpactRequest
        req = ImpactRequest.new(
            from_agent="mirrormaker", to_agent="kafka-broker",
            ticket_id=ticket.ticket_id, request_type="design_review",
            question="should this live in KRaft metadata?", round_number=1,
        )
        resp = network["broker"].consult_about(req, depth=0)
        kraft = [a for a in resp.design_alternatives if "KRaft" in a.name]
        assert len(kraft) == 1
        assert kraft[0].is_ruled_out
        assert "32x" in kraft[0].rejected_reason

    def test_every_agent_produced_a_principal_review(self, network, ticket):
        from proto.sme_agents import ImpactRequest
        for agent_key, agent_name in (("coordinator", "group-coordinator"), ("broker", "kafka-broker"), ("clients", "kafka-clients")):
            req = ImpactRequest.new(
                from_agent="mirrormaker", to_agent=agent_name,
                ticket_id=ticket.ticket_id, request_type="design_review",
                question="deep review please", round_number=1,
            )
            resp = network[agent_key].consult_about(req, depth=1)
            assert len(resp.principal_review) > 400, (
                f"{agent_name} gave a shallow review"
            )


# ---------------------------------------------------------------------------
# requires_human — end only, and only for the real gate
# ---------------------------------------------------------------------------

class TestHumanEscalationIsEndOnlyAndMinimal:
    def test_requires_human_is_true(self, finding):
        assert finding.requires_human is True

    def test_exactly_one_human_decision_point(self, finding):
        assert len(finding.human_decision_points) == 1, (
            f"expected exactly one escalation, got: {finding.human_decision_points}"
        )

    def test_the_escalation_is_the_apache_pmc_vote(self, finding):
        point = finding.human_decision_points[0].lower()
        assert "pmc" in point or "vote" in point
        assert "kafka-clients" in point

    def test_escalation_is_not_caused_by_non_convergence(self, finding):
        assert not any("did not converge" in p for p in finding.human_decision_points)

    def test_escalation_is_not_caused_by_unreachable_peers(self, finding):
        assert not any("Could not reach" in p for p in finding.human_decision_points)

    def test_ordinary_technical_uncertainty_did_not_escalate(self, finding):
        """Memory cost, rebuild time, flag scope were all settled agent-to-agent."""
        joined = " ".join(finding.human_decision_points).lower()
        for resolved_topic in ("memory", "heap", "rebuild", "feature flag"):
            assert resolved_topic not in joined, (
                f"'{resolved_topic}' should have been settled by the agents"
            )

    def test_confidence_is_high_because_they_converged(self, finding):
        assert finding.confidence >= 0.9


# ---------------------------------------------------------------------------
# 1-pager content
# ---------------------------------------------------------------------------

class TestOnePagerContent:
    def test_has_a_tldr_with_the_recommendation(self, finding):
        assert finding.tldr
        rec = finding.recommended_alternative
        assert rec is not None
        assert rec.name in finding.tldr

    def test_background_explains_what_mirrormaker_does(self, finding):
        background = finding.background.lower()
        assert "mirrormaker" in background
        assert "replicat" in background
        assert len(finding.background) > 500

    def test_has_goals_and_non_goals(self, finding):
        assert len(finding.goals) >= 3
        assert len(finding.non_goals) >= 3

    def test_non_goals_explicitly_exclude_offset_translation(self, finding):
        joined = " ".join(finding.non_goals).lower()
        assert "translation" in joined

    def test_has_five_execution_steps(self, finding):
        assert len(finding.execution_order) == 5

    def test_testing_strategy_aggregates_all_four_teams(self, finding):
        joined = " ".join(finding.testing_strategy)
        for team in ("mirrormaker", "group-coordinator", "kafka-broker",
                     "kafka-clients"):
            assert team in joined, f"{team} contributed no test requirements"

    def test_has_risks_rollout_and_success_metrics(self, finding):
        assert len(finding.risks_and_mitigations) >= 3
        assert len(finding.rollout_and_rollback) >= 3
        assert len(finding.success_metrics) >= 3

    def test_teams_involved_covers_four_teams_with_roles(self, finding):
        teams = {t.team: t for t in finding.teams_involved}
        assert {"mirrormaker", "group-coordinator", "kafka-clients", "kafka-broker"} <= set(teams)
        assert teams["mirrormaker"].role == "owner"
        assert teams["kafka-clients"].sign_off_required is True


# ---------------------------------------------------------------------------
# Ownership discipline across the whole run
# ---------------------------------------------------------------------------

class TestOwnershipDiscipline:
    def test_mirrormaker_cited_only_what_it_owns(self, finding):
        results = validate_citations(finding.cited_codepaths, MirrorMakerAgent.OWNS)
        assert [r.codepath for r in results if not r.owned] == []

    def test_each_agents_alternatives_are_attributed_correctly(self, finding):
        for alt in finding.design_alternatives:
            assert alt.proposed_by == "mirrormaker"

    def test_no_unflagged_out_of_bounds_citations_anywhere(self, finding):
        """§25 step 6 — zero unflagged ownership violations in the whole run."""
        unflagged = [
            q for q in finding.open_questions
            if q.startswith("needs_verification:")
        ]
        assert unflagged == [], f"unflagged ownership violations: {unflagged}"


# ---------------------------------------------------------------------------
# Rendered document
# ---------------------------------------------------------------------------

class TestRenderedDocument:
    def test_renders_without_error(self, doc):
        assert doc
        assert doc.startswith("# ")

    def test_contains_every_required_section(self, doc):
        for section in (
            "## TL;DR", "## Background", "## Goals", "## Design Alternatives",
            "## Recommendation", "## Testing Strategy", "## Teams Involved",
            "## Execution Plan", "## Risks & Mitigations", "## Rollout & Rollback",
            "## Success Metrics", "## Decisions Requiring a Human",
            "## Appendix: Deliberation Record",
        ):
            assert section in doc, f"missing: {section}"

    def test_shows_all_three_alternatives_with_one_recommended(self, doc):
        assert "### Alternative A:" in doc
        assert "### Alternative B:" in doc
        assert "### Alternative C:" in doc
        assert doc.count("**RECOMMENDED**") == 1

    def test_deliberation_record_shows_the_rounds(self, doc):
        assert "| Round | From | To | Verdict |" in doc
        assert "Round 1, `mirrormaker` → `group-coordinator`" in doc
