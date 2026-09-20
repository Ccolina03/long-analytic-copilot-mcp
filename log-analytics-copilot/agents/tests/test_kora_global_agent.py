"""
Phase 5 / kora-global agent tests.

Tests
-----
  test_get_failover_latency_returns_documented_json_shape
  test_get_failover_latency_requires_topic
  test_get_offset_clamp_trace_phases_sum_to_total_ms
  test_list_active_links_returns_list_with_state_field
  test_get_consumer_groups_for_link_returns_efficiency_pct
  test_own_ticket_produces_impact_request_addressed_to_consumer_team
  test_own_ticket_produces_impact_request_addressed_to_oss_kafka
  test_finding_has_five_execution_order_steps
  test_finding_approvals_needed_includes_both_teams
  test_finding_requires_human_is_true
"""

import pytest

from agents.base_agent import MockTransport
from agents.kora_global_agent import KoraGlobalAgent
from proto.sme_agents import ImpactResponse, Ticket


@pytest.fixture
def ticket():
    return Ticket.new(
        team="kora-global",
        title="clampOffsets is slow on orders topic",
        description=(
            "clampOffsets p99 = 11.4s on cluster with 50k consumer groups. "
            "Traced to O(n_groups) listGroups + describeGroups fan-out."
        ),
        priority="high",
    )


@pytest.fixture
def canned_consumer_response():
    return ImpactResponse(
        request_id="r1",
        from_agent="consumer-team",
        to_agent="kora-global",
        verdict="needs_changes",
        confidence=0.9,
        summary="Inverted index feasible. ListGroups v5 needs KIP.",
        cited_codepaths=["GroupCoordinator.scala"],
        open_questions=["needs a new Kafka API version — ask oss-kafka"],
        follow_up_consultations=["oss-kafka"],
    )


@pytest.fixture
def canned_oss_response():
    return ImpactResponse(
        request_id="r2",
        from_agent="oss-kafka",
        to_agent="kora-global",
        verdict="needs_changes",
        confidence=0.95,
        summary="New KIP required. ~6 weeks to trunk.",
        cited_codepaths=["ListGroupsRequest.json"],
        open_questions=["KIP-848 compatibility: mixed group behavior"],
    )


@pytest.fixture
def agent_with_mocked_peers(canned_consumer_response, canned_oss_response):
    transport = MockTransport({
        "consumer-team": canned_consumer_response,
        "oss-kafka": canned_oss_response,
    })
    return KoraGlobalAgent(transport=transport)


# ------------------------------------------------------------------
# Tool tests
# ------------------------------------------------------------------

class TestKoraTools:
    def setup_method(self):
        self.agent = KoraGlobalAgent()

    def test_get_failover_latency_returns_documented_json_shape(self):
        result = self.agent.get_failover_latency("orders")
        assert result["topic"] == "orders"
        assert "p50_ms" in result
        assert "p95_ms" in result
        assert "p99_ms" in result
        assert "bottleneck_phase" in result
        assert "bottleneck_pct" in result

    def test_get_failover_latency_requires_topic(self):
        with pytest.raises(ValueError, match="topic is required"):
            self.agent.get_failover_latency("")

    def test_get_failover_latency_requires_positive_hours(self):
        with pytest.raises(ValueError):
            self.agent.get_failover_latency("orders", last_hours=0)

    def test_get_offset_clamp_trace_phases_sum_to_total_ms(self):
        result = self.agent.get_offset_clamp_trace("orders", 3)
        phases_sum = sum(p["ms"] for p in result["phases"])
        assert result["total_ms"] == phases_sum

    def test_get_offset_clamp_trace_has_four_phases(self):
        result = self.agent.get_offset_clamp_trace("orders", 3)
        assert len(result["phases"]) == 4

    def test_list_active_links_returns_list_with_state_field(self):
        links = self.agent.list_active_links()
        assert isinstance(links, list)
        assert len(links) >= 1
        assert "state" in links[0]
        assert "link_id" in links[0]

    def test_get_consumer_groups_for_link_returns_efficiency_pct(self):
        result = self.agent.get_consumer_groups_for_link("us-east-1-to-eu-west-1")
        assert "efficiency_pct" in result
        assert result["efficiency_pct"] < 1.0  # should be a tiny fraction

    def test_get_consumer_groups_for_link_requires_link_id(self):
        with pytest.raises(ValueError):
            self.agent.get_consumer_groups_for_link("")


# ------------------------------------------------------------------
# OwnTicket workflow tests
# ------------------------------------------------------------------

class TestKoraOwnTicket:
    def test_own_ticket_produces_impact_request_addressed_to_consumer_team(
        self, agent_with_mocked_peers, ticket
    ):
        finding = agent_with_mocked_peers.own_ticket(ticket)
        # consumer-team must have been consulted (its canned response appears in consultations)
        consulted_agents = [c["from_agent"] for c in finding.consultations]
        assert "consumer-team" in consulted_agents

    def test_own_ticket_produces_impact_request_addressed_to_oss_kafka(
        self, agent_with_mocked_peers, ticket
    ):
        finding = agent_with_mocked_peers.own_ticket(ticket)
        consulted_agents = [c["from_agent"] for c in finding.consultations]
        assert "oss-kafka" in consulted_agents

    def test_finding_has_five_execution_order_steps(
        self, agent_with_mocked_peers, ticket
    ):
        finding = agent_with_mocked_peers.own_ticket(ticket)
        assert len(finding.execution_order) == 5

    def test_finding_approvals_needed_includes_both_teams(
        self, agent_with_mocked_peers, ticket
    ):
        finding = agent_with_mocked_peers.own_ticket(ticket)
        approvals_str = " ".join(finding.approvals_needed)
        assert "consumer-team" in approvals_str
        assert "oss-kafka" in approvals_str

    def test_finding_requires_human_is_true(
        self, agent_with_mocked_peers, ticket
    ):
        finding = agent_with_mocked_peers.own_ticket(ticket)
        assert finding.requires_human is True

    def test_finding_confidence_is_high(self, agent_with_mocked_peers, ticket):
        finding = agent_with_mocked_peers.own_ticket(ticket)
        assert finding.confidence >= 0.9

    def test_finding_cited_codepaths_are_kora_owned(
        self, agent_with_mocked_peers, ticket
    ):
        finding = agent_with_mocked_peers.own_ticket(ticket)
        for cp in finding.cited_codepaths:
            # All cited paths must be owned by kora-global
            assert any(
                cp.startswith(owned) or owned in cp
                for owned in KoraGlobalAgent.OWNS
            ), f"Cited path not owned by kora-global: {cp}"
