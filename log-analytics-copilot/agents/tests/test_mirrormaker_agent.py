"""
Phase 5 / mirrormaker agent tests.

Covers the four tools, the three design alternatives, and the OwnTicket
deliberation flow against all three real peer agents.
"""

import pytest

from agents.base_agent import NullTransport
from agents.mirrormaker_agent import MirrorMakerAgent
from agents.network import build_network
from agents.ownership_validator import validate_citations
from proto.sme_agents import Ticket


@pytest.fixture
def ticket():
    return Ticket.new(
        team="mirrormaker",
        title="MirrorCheckpointConnector group discovery is slow on orders topic",
        description=(
            "Checkpoint group discovery p99 = 11.4s on a cluster with 50k consumer "
            "groups. Traced to an O(n_groups) listConsumerGroups + "
            "describeConsumerGroups fan-out."
        ),
        priority="high",
    )


@pytest.fixture
def full_network():
    """mirrormaker wired into the full implemented network.

    Which peers it actually consults is decided by discovery, not this fixture.
    """
    return build_network()["mirrormaker"]


# ------------------------------------------------------------------
# Tools
# ------------------------------------------------------------------

class TestMirrorMakerTools:
    def setup_method(self):
        self.agent = MirrorMakerAgent()

    def test_get_checkpoint_latency_returns_documented_shape(self):
        r = self.agent.get_checkpoint_latency("orders")
        for key in ("topic", "p50_ms", "p95_ms", "p99_ms", "bottleneck_phase", "bottleneck_pct"):
            assert key in r

    def test_get_checkpoint_latency_requires_topic(self):
        with pytest.raises(ValueError, match="topic is required"):
            self.agent.get_checkpoint_latency("")

    def test_get_checkpoint_latency_requires_positive_hours(self):
        with pytest.raises(ValueError):
            self.agent.get_checkpoint_latency("orders", last_hours=0)

    def test_clamp_trace_phases_sum_to_total_ms(self):
        r = self.agent.get_group_discovery_trace("orders")
        assert r["total_ms"] == sum(p["ms"] for p in r["phases"])

    def test_clamp_trace_has_four_phases(self):
        assert len(self.agent.get_group_discovery_trace("orders")["phases"]) == 4

    def test_clamp_trace_shows_tiny_match_rate(self):
        """The whole argument rests on matched << scanned."""
        r = self.agent.get_group_discovery_trace("orders")
        scanned = r["phases"][0]["groups_returned"]
        matched = r["phases"][2]["groups_matched"]
        assert matched < scanned / 1000

    def test_list_replication_flows_returns_flows_with_state(self):
        flows = self.agent.list_replication_flows()
        assert len(flows) >= 1
        assert all("state" in f and "flow_id" in f for f in flows)

    def test_get_group_discovery_cost_shows_terrible_efficiency(self):
        r = self.agent.get_group_discovery_cost("us-east-1->eu-west-1")
        assert r["efficiency_pct"] < 1.0

    def test_get_group_discovery_cost_requires_flow_id(self):
        with pytest.raises(ValueError):
            self.agent.get_group_discovery_cost("")


# ------------------------------------------------------------------
# Design alternatives
# ------------------------------------------------------------------

class TestMirrorMakerAlternatives:
    def setup_method(self):
        self.agent = MirrorMakerAgent()
        ticket = Ticket.new(team="mirrormaker", title="t", description="d")
        self.investigation = self.agent._investigate(ticket)
        self.alts = self.agent._propose_alternatives(ticket, self.investigation)

    def test_proposes_exactly_three(self):
        assert len(self.alts) == 3

    def test_labels_are_a_b_c(self):
        assert [a.label for a in self.alts] == ["A", "B", "C"]

    def test_all_proposed_by_mirrormaker(self):
        assert all(a.proposed_by == "mirrormaker" for a in self.alts)

    def test_every_alternative_has_substantive_pros_and_cons(self):
        for alt in self.alts:
            assert len(alt.pros) >= 3, f"{alt.name} needs real pros"
            assert len(alt.cons) >= 3, f"{alt.name} needs real cons"
            assert len(alt.approach) > 200, f"{alt.name} approach is too shallow"

    def test_alternatives_span_a_real_effort_range(self):
        efforts = {a.effort for a in self.alts}
        assert len(efforts) >= 2, "alternatives should not all be the same effort"

    def test_alternatives_span_a_real_risk_range(self):
        risks = {a.risk for a in self.alts}
        assert len(risks) >= 2, "alternatives should not all be the same risk"

    def test_each_alternative_declares_blast_radius(self):
        assert all(a.blast_radius for a in self.alts)

    def test_no_alternative_is_pre_recommended_before_deliberation(self):
        """The owning agent must not pick a winner before consulting peers."""
        assert not any(a.recommended for a in self.alts)


# ------------------------------------------------------------------
# Investigation content for the 1-pager
# ------------------------------------------------------------------

class TestMirrorMakerInvestigation:
    def setup_method(self):
        self.agent = MirrorMakerAgent()
        self.inv = self.agent._investigate(
            Ticket.new(team="mirrormaker", title="t", description="d")
        )

    def test_root_cause_explains_the_missing_reverse_index(self):
        assert "reverse index" in self.inv["root_cause"].lower()

    def test_has_goals_and_non_goals(self):
        assert len(self.inv["goals"]) >= 3
        assert len(self.inv["non_goals"]) >= 3

    def test_has_five_execution_steps(self):
        assert len(self.inv["execution_order"]) == 5

    def test_has_risks_rollout_and_metrics(self):
        assert len(self.inv["risks_and_mitigations"]) >= 3
        assert len(self.inv["rollout_and_rollback"]) >= 3
        assert len(self.inv["success_metrics"]) >= 3

    def test_has_its_own_test_requirements(self):
        assert len(self.inv["testing_strategy"]) >= 3


# ------------------------------------------------------------------
# OwnTicket deliberation
# ------------------------------------------------------------------

class TestMirrorMakerOwnTicket:
    def test_discovers_and_consults_the_relevant_peers(self, full_network, ticket):
        finding = full_network.own_ticket(ticket)
        consulted = {r.to_agent for r in finding.deliberation}
        assert "group-coordinator" in consulted
        assert "kafka-broker" in consulted
        assert "kafka-clients" in consulted
        assert "kafka-security" in consulted
        skipped = {d.agent_id for d in finding.peer_discovery if not d.consult}
        assert "kafka-storage" in skipped
        assert "kafka-streams" in skipped

    def test_deliberates_over_multiple_rounds(self, full_network, ticket):
        finding = full_network.own_ticket(ticket)
        assert finding.rounds_used >= 2, (
            "group-coordinator raises concerns in round 1, so round 1 cannot converge"
        )

    def test_converges_within_the_round_cap(self, full_network, ticket):
        finding = full_network.own_ticket(ticket)
        assert finding.rounds_used <= MirrorMakerAgent.MAX_DELIBERATION_ROUNDS

    def test_produces_a_recommendation(self, full_network, ticket):
        finding = full_network.own_ticket(ticket)
        assert finding.recommended_alternative is not None

    def test_cited_codepaths_all_pass_ownership_validation(self, full_network, ticket):
        finding = full_network.own_ticket(ticket)
        results = validate_citations(finding.cited_codepaths, MirrorMakerAgent.OWNS)
        unowned = [r.codepath for r in results if not r.owned]
        assert unowned == []

    def test_teams_involved_includes_discovered_peers(self, full_network, ticket):
        finding = full_network.own_ticket(ticket)
        teams = {t.team for t in finding.teams_involved}
        assert {"mirrormaker", "group-coordinator", "kafka-broker",
                "kafka-clients", "kafka-security"} <= teams

    def test_testing_strategy_aggregates_from_all_participants(self, full_network, ticket):
        finding = full_network.own_ticket(ticket)
        joined = " ".join(finding.testing_strategy)
        assert "mirrormaker" in joined
        assert "group-coordinator" in joined
        assert "kafka-broker" in joined
        assert "kafka-clients" in joined
        assert "kafka-security" in joined

    def test_requires_human_only_for_the_apache_vote(self, full_network, ticket):
        """The single legitimate escalation is the PMC vote, nothing else."""
        finding = full_network.own_ticket(ticket)
        assert finding.requires_human is True
        assert len(finding.human_decision_points) == 1
        point = finding.human_decision_points[0].lower()
        assert "pmc" in point or "vote" in point

    def test_unreachable_peers_escalate(self, ticket):
        agent = MirrorMakerAgent(transport=NullTransport())
        finding = agent.own_ticket(ticket)
        assert finding.requires_human is True
        assert any("Could not reach" in p for p in finding.human_decision_points)
