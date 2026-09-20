"""
Phase 5 / consumer-team agent tests.

Tests
-----
  test_estimate_index_memory_cost_matches_documented_formula
  test_estimate_index_memory_cost_raises_on_nonpositive_inputs
  test_get_groups_for_topic_partition_returns_expected_fields
  test_get_group_state_returns_stable_state
  test_get_offset_storage_schema_has_correct_key_format
  test_consult_about_recognises_it_needs_oss_kafka
  test_consult_about_responds_correctly_to_kora_global_impact_request
"""

import pytest

from agents.base_agent import MockTransport, NullTransport
from agents.consumer_team_agent import ConsumerTeamAgent
from proto.sme_agents import ImpactRequest, ImpactResponse


@pytest.fixture
def oss_kafka_canned():
    return ImpactResponse(
        request_id="oss-r1",
        from_agent="oss-kafka",
        to_agent="consumer-team",
        verdict="needs_changes",
        confidence=0.95,
        summary="New KIP required. ~6 weeks to trunk.",
        cited_codepaths=["ListGroupsRequest.json"],
        open_questions=["KIP-848 compatibility note"],
    )


@pytest.fixture
def agent_with_oss(oss_kafka_canned):
    transport = MockTransport({"oss-kafka": oss_kafka_canned})
    return ConsumerTeamAgent(transport=transport)


@pytest.fixture
def kora_request():
    return ImpactRequest.new(
        from_agent="kora-global",
        to_agent="consumer-team",
        ticket_id="t-consumer-1",
        request_type="impact_analysis",
        context=(
            "Requesting: listGroupsForTopicPartition(topic, partition) → [group_id]\n"
            "Evidence: clampOffsets p99 = 11,400ms for cluster with 50k groups\n"
            "91% of time in listGroups + describeGroups fan-out\n"
            "Only 4 of 50,312 groups matched (0.008% hit rate)\n"
            "Proposed contract: Input: topic str, partition int → Output: [group_id]\n"
            "Latency target: <50ms"
        ),
        codepaths_of_interest=["GroupCoordinator.scala"],
        proposed_change="Add inverted index to GroupCoordinator + ListGroups v5 filter",
    )


# ------------------------------------------------------------------
# Tool tests
# ------------------------------------------------------------------

class TestConsumerTeamTools:
    def setup_method(self):
        self.agent = ConsumerTeamAgent()

    def test_estimate_index_memory_cost_matches_documented_formula(self):
        """Test: 50k groups × 8 subs × 36 bytes = ~13.7 MB."""
        result = self.agent.estimate_index_memory_cost(50_000, 8)
        assert result["entries"] == 400_000
        assert result["bytes_per_entry"] == 36
        # 400_000 * 36 / (1024**2) ≈ 13.7
        assert abs(result["total_mb"] - 13.7) < 0.5
        assert result["per_coordinator_mb"] > 0
        assert "acceptable" in result["assessment"].lower()

    def test_estimate_index_memory_cost_raises_on_zero_groups(self):
        with pytest.raises(ValueError, match="group_count must be > 0"):
            self.agent.estimate_index_memory_cost(0, 8)

    def test_estimate_index_memory_cost_raises_on_zero_subscriptions(self):
        with pytest.raises(ValueError):
            self.agent.estimate_index_memory_cost(1000, 0)

    def test_get_groups_for_topic_partition_returns_expected_fields(self):
        result = self.agent.get_groups_for_topic_partition("orders", 3)
        assert "groups" in result
        assert "duration_ms" in result
        assert "method" in result
        assert isinstance(result["groups"], list)

    def test_get_groups_for_topic_partition_requires_topic(self):
        with pytest.raises(ValueError, match="topic is required"):
            self.agent.get_groups_for_topic_partition("", 0)

    def test_get_group_state_returns_stable_state(self):
        result = self.agent.get_group_state("billing-consumer")
        assert result["state"] == "Stable"
        assert result["group_id"] == "billing-consumer"

    def test_get_offset_storage_schema_has_correct_key_format(self):
        result = self.agent.get_offset_storage_schema()
        assert "group_id" in result["key_format"]
        assert "topic" in result["key_format"]
        assert "partition" in result["key_format"]
        assert result["num_partitions"] == 50

    def test_get_rebalance_history_returns_triggers(self):
        result = self.agent.get_rebalance_history("orders")
        assert "rebalance_count" in result
        assert "triggers" in result


# ------------------------------------------------------------------
# ConsultAbout tests
# ------------------------------------------------------------------

class TestConsumerTeamConsultAbout:
    def test_consult_about_recognises_it_needs_oss_kafka(
        self, agent_with_oss, kora_request
    ):
        """The consumer-team agent must identify it needs oss-kafka input."""
        response = agent_with_oss.consult_about(kora_request, depth=0)
        # Must either put oss-kafka in follow_up_consultations or open_questions
        needs_oss = (
            "oss-kafka" in response.follow_up_consultations
            or any("oss-kafka" in q or "kip" in q.lower() for q in response.open_questions)
            or "kip" in response.summary.lower()
        )
        assert needs_oss, (
            f"consumer-team didn't flag oss-kafka consultation. "
            f"follow_ups={response.follow_up_consultations}, "
            f"open_qs={response.open_questions}, "
            f"summary={response.summary}"
        )

    def test_consult_about_responds_correctly_to_kora_global_impact_request(
        self, agent_with_oss, kora_request
    ):
        """§24.2 integration check: exact ImpactRequest from §8."""
        response = agent_with_oss.consult_about(kora_request, depth=0)

        assert response.from_agent == "consumer-team"
        assert response.to_agent == "kora-global"
        assert response.verdict == "needs_changes"
        assert response.confidence >= 0.8

        # Must cite GroupCoordinator.scala (which consumer-team owns)
        assert "GroupCoordinator.scala" in response.cited_codepaths

        # The response summary must mention the inverted index
        assert "index" in response.summary.lower() or "inverted" in response.summary.lower()

    def test_consult_about_with_null_transport_still_returns_response(
        self, kora_request
    ):
        """Even with no oss-kafka reachable, consumer-team must still respond."""
        agent = ConsumerTeamAgent(transport=NullTransport())
        response = agent.consult_about(kora_request, depth=0)
        assert response.from_agent == "consumer-team"
        assert response.verdict == "needs_changes"
        # The open_questions should mention the oss-kafka need
        assert any(
            "kip" in q.lower() or "kafka" in q.lower() or "version" in q.lower()
            for q in response.open_questions
        ), f"Expected KIP-related open question, got: {response.open_questions}"
