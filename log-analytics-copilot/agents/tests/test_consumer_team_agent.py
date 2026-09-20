"""
Phase 5 / consumer-team agent tests.

Covers the five tools, the three implementation alternatives for the index,
and the round-aware consultation behaviour including the substantive
correctness pushback on kora-global's alternative B.
"""

import pytest

from agents.base_agent import DirectTransport, NullTransport
from agents.consumer_team_agent import ConsumerTeamAgent
from agents.oss_kafka_agent import OssKafkaAgent
from agents.ownership_validator import validate_citations
from proto.sme_agents import ImpactRequest


@pytest.fixture
def agent():
    """Consumer team wired to a real oss-kafka agent."""
    return ConsumerTeamAgent(transport=DirectTransport({"oss-kafka": OssKafkaAgent()}))


def _request(round_number: int = 1) -> ImpactRequest:
    return ImpactRequest.new(
        from_agent="kora-global",
        to_agent="consumer-team",
        ticket_id="t-consumer-1",
        request_type="design_review",
        context="clampOffsets p99 = 11,400ms on a cluster with 50,312 groups.",
        question="Is a reverse index implementable on your side?",
        codepaths_of_interest=["GroupCoordinator.scala"],
        round_number=round_number,
    )


# ------------------------------------------------------------------
# Tools
# ------------------------------------------------------------------

class TestConsumerTeamTools:
    def setup_method(self):
        self.agent = ConsumerTeamAgent()

    def test_memory_cost_matches_documented_formula(self):
        """50k groups x 8 subs x 36 bytes ≈ 13.7 MB."""
        r = self.agent.estimate_index_memory_cost(50_000, 8)
        assert r["entries"] == 400_000
        assert r["bytes_per_entry"] == 36
        assert abs(r["total_mb"] - 13.7) < 0.5
        assert "acceptable" in r["assessment"].lower()

    def test_memory_cost_scales_linearly_with_groups(self):
        small = self.agent.estimate_index_memory_cost(10_000, 8)
        big = self.agent.estimate_index_memory_cost(100_000, 8)
        # entries is exact; total_mb is rounded for display so allow slack there
        assert big["entries"] == small["entries"] * 10
        assert abs(big["total_mb"] / small["total_mb"] - 10) < 0.5

    def test_memory_cost_warns_above_threshold(self):
        r = self.agent.estimate_index_memory_cost(5_000_000, 8)
        assert "WARNING" in r["assessment"]

    def test_memory_cost_rejects_zero_groups(self):
        with pytest.raises(ValueError, match="group_count must be > 0"):
            self.agent.estimate_index_memory_cost(0, 8)

    def test_memory_cost_rejects_zero_subscriptions(self):
        with pytest.raises(ValueError):
            self.agent.estimate_index_memory_cost(1000, 0)

    def test_groups_for_topic_partition_returns_expected_fields(self):
        r = self.agent.get_groups_for_topic_partition("orders", 3)
        for key in ("groups", "duration_ms", "method", "groups_scanned"):
            assert key in r

    def test_groups_for_topic_partition_requires_topic(self):
        with pytest.raises(ValueError, match="topic is required"):
            self.agent.get_groups_for_topic_partition("", 0)

    def test_group_state_returns_stable(self):
        r = self.agent.get_group_state("billing-consumer")
        assert r["state"] == "Stable"
        assert r["group_id"] == "billing-consumer"

    def test_offset_storage_schema_key_format(self):
        r = self.agent.get_offset_storage_schema()
        for part in ("group_id", "topic", "partition"):
            assert part in r["key_format"]
        assert r["num_partitions"] == 50

    def test_rebalance_history_returns_triggers(self):
        r = self.agent.get_rebalance_history("orders")
        assert "rebalance_count" in r
        assert "triggers" in r


# ------------------------------------------------------------------
# Index implementation alternatives
# ------------------------------------------------------------------

class TestConsumerTeamAlternatives:
    def setup_method(self):
        a = ConsumerTeamAgent()
        self.alts = a._index_alternatives(
            a.estimate_index_memory_cost(50_000, 8),
            a.get_rebalance_history("orders"),
        )

    def test_proposes_exactly_three(self):
        assert len(self.alts) == 3

    def test_all_proposed_by_consumer_team(self):
        assert all(a.proposed_by == "consumer-team" for a in self.alts)

    def test_every_alternative_has_depth(self):
        for alt in self.alts:
            assert len(alt.pros) >= 2
            assert len(alt.cons) >= 3
            assert len(alt.approach) > 150

    def test_eager_hashmap_is_the_low_risk_option(self):
        eager = self.alts[0]
        assert "eager" in eager.name.lower()
        assert eager.risk == "low"

    def test_persisted_index_has_largest_blast_radius(self):
        persisted = self.alts[2]
        assert persisted.effort == "XL"
        assert len(persisted.blast_radius) >= 3

    def test_memory_numbers_appear_in_the_pros(self):
        """The recommendation must be backed by measured numbers, not vibes."""
        joined = " ".join(self.alts[0].pros)
        assert "13.7" in joined or "MB" in joined


# ------------------------------------------------------------------
# ConsultAbout — round-aware behaviour
# ------------------------------------------------------------------

class TestConsumerTeamConsultAbout:
    def test_round_one_raises_concerns_and_does_not_agree(self, agent):
        resp = agent.consult_about(_request(1), depth=0)
        assert resp.verdict == "needs_changes"
        assert len(resp.new_concerns) >= 3

    def test_round_one_corrects_kora_alternative_b_on_correctness(self, agent):
        """The substantive principal-engineer pushback: commits != subscriptions."""
        resp = agent.consult_about(_request(1), depth=0)
        joined = " ".join(resp.new_concerns).lower()
        assert "__consumer_offsets" in joined
        assert "subscription" in joined
        assert "correctness" in joined or "correct" in joined

    def test_round_one_consults_oss_kafka_on_own_initiative(self, agent):
        resp = agent.consult_about(_request(1), depth=0)
        assert "oss-kafka" in resp.follow_up_consultations

    def test_round_three_agrees(self, agent):
        resp = agent.consult_about(_request(3), depth=0)
        assert resp.verdict == "agreed"
        assert resp.new_concerns == []

    def test_recommends_the_eager_index(self, agent):
        resp = agent.consult_about(_request(1), depth=0)
        assert "eager" in resp.recommendation.lower()
        recommended = [a for a in resp.design_alternatives if a.recommended]
        assert len(recommended) == 1
        assert "eager" in recommended[0].name.lower()

    def test_rules_out_the_other_two_with_reasons(self, agent):
        resp = agent.consult_about(_request(1), depth=0)
        ruled_out = [a for a in resp.design_alternatives if a.is_ruled_out]
        assert len(ruled_out) == 2
        assert all(len(a.rejected_reason) > 20 for a in ruled_out)

    def test_principal_review_is_substantive(self, agent):
        resp = agent.consult_about(_request(1), depth=0)
        assert len(resp.principal_review) > 400

    def test_cites_only_codepaths_it_owns(self, agent):
        resp = agent.consult_about(_request(1), depth=0)
        results = validate_citations(resp.cited_codepaths, ConsumerTeamAgent.OWNS)
        assert [r.codepath for r in results if not r.owned] == []

    def test_supplies_its_own_test_requirements(self, agent):
        resp = agent.consult_about(_request(1), depth=0)
        assert len(resp.test_requirements) >= 5

    def test_does_not_claim_org_authority_for_itself(self, agent):
        """The KIP vote is oss-kafka's gate, not consumer-team's."""
        resp = agent.consult_about(_request(1), depth=0)
        assert resp.needs_org_authority is False

    def test_still_responds_when_oss_kafka_unreachable(self):
        agent = ConsumerTeamAgent(transport=NullTransport())
        resp = agent.consult_about(_request(1), depth=0)
        assert resp.from_agent == "consumer-team"
        assert resp.verdict == "needs_changes"
        assert any("unreachable" in q.lower() or "unconfirmed" in q.lower()
                   for q in resp.open_questions)
