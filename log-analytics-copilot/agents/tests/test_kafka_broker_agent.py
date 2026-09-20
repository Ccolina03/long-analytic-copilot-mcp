"""
kafka-broker agent tests.

This agent exists to catch what the other three structurally cannot: that
group→coordinator placement is by group-id hash, so a topic-scoped ListGroups
still has to fan out to every `__consumer_offsets` shard. Most of these tests
are about that correction being present, quantitative, and not silently dropped.
"""

import pytest

from agents.kafka_broker_agent import KafkaBrokerAgent
from agents.ownership_validator import validate_citations
from proto.sme_agents import ImpactRequest


@pytest.fixture
def agent():
    return KafkaBrokerAgent()


def _request(round_number: int = 1, prior_concerns=None) -> ImpactRequest:
    return ImpactRequest.new(
        from_agent="mirrormaker",
        to_agent="kafka-broker",
        ticket_id="KAFKA-18231",
        request_type="design_review",
        context="MM2 checkpoint group discovery is O(n_groups).",
        question=(
            "How does a filtered ListGroups route across coordinator shards, and is "
            "the heap cost of the index acceptable?"
        ),
        codepaths_of_interest=["KafkaApis.scala"],
        round_number=round_number,
        prior_concerns=prior_concerns or [],
    )


# ------------------------------------------------------------------
# Tools
# ------------------------------------------------------------------

class TestRequestRouting:
    def test_listgroups_routes_as_a_fan_out(self, agent):
        r = agent.get_request_routing("LISTGROUPS")
        assert r["routing"] == "fan_out_all_coordinator_shards"
        assert r["coordinator_shard_count"] == 50
        assert r["handler"] == "KafkaApis.handleListGroupsRequest"

    def test_routing_explains_why_not_just_what(self, agent):
        """A bare 'it fans out' is not reviewable; the reason has to be stated."""
        reason = agent.get_request_routing("LISTGROUPS")["reason"]
        assert "group_id.hashCode()" in reason
        assert "independent of which topics" in reason

    def test_unknown_api_degrades_instead_of_inventing_routing(self, agent):
        r = agent.get_request_routing("SomeApiWeDoNotModel")
        assert r["routing"] == "unknown"
        assert "not modelled" in r["note"]

    def test_requires_an_api_name(self, agent):
        with pytest.raises(ValueError):
            agent.get_request_routing("")


class TestCoordinatorDistribution:
    def test_all_shards_must_be_queried_even_though_few_match(self, agent):
        d = agent.get_coordinator_distribution("orders")
        assert d["shards_hosting_matched_groups"] < d["shards_that_must_be_queried"]
        assert d["shards_that_must_be_queried"] == d["total_shards"]

    def test_explains_the_gap(self, agent):
        note = agent.get_coordinator_distribution("orders")["note"]
        assert "group id" in note and "hash" in note

    def test_requires_a_topic(self, agent):
        with pytest.raises(ValueError):
            agent.get_coordinator_distribution("")


class TestHeapProfile:
    def test_reports_headroom_consistently(self, agent):
        h = agent.get_broker_heap_profile(12)
        assert h["heap_headroom_mb"] == h["heap_total_mb"] - h["heap_used_mb"]
        assert h["heap_headroom_mb"] > 0

    def test_names_the_largest_consumers(self, agent):
        h = agent.get_broker_heap_profile(12)
        assert len(h["largest_consumers"]) >= 3
        assert all("component" in c and "mb" in c for c in h["largest_consumers"])

    def test_rejects_a_negative_broker_id(self, agent):
        with pytest.raises(ValueError):
            agent.get_broker_heap_profile(-1)


class TestKraftMetadataBudget:
    def test_group_churn_dwarfs_the_metadata_record_rate(self, agent):
        m = agent.get_kraft_metadata_budget()
        assert (
            m["consumer_group_churn_per_second"]
            > m["records_per_second_steady_state"] * 10
        )

    def test_states_the_design_intent_not_just_numbers(self, agent):
        m = agent.get_kraft_metadata_budget()
        assert "topology" in m["design_intent"]
        assert "dominate" in m["verdict"]


# ------------------------------------------------------------------
# Alternatives
# ------------------------------------------------------------------

class TestRoutingAlternatives:
    def setup_method(self):
        a = KafkaBrokerAgent()
        self.alts = a._routing_alternatives(
            a.get_request_routing("LISTGROUPS"),
            a.get_coordinator_distribution("orders"),
            a.get_broker_heap_profile(12),
            a.get_kraft_metadata_budget(),
        )

    def test_proposes_exactly_three(self):
        assert len(self.alts) == 3

    def test_spans_scatter_gather_kraft_and_rehashing(self):
        names = " ".join(a.name.lower() for a in self.alts)
        assert "scatter-gather" in names
        assert "kraft" in names
        assert "co-locate" in names

    def test_every_alternative_is_argued_in_depth(self):
        for alt in self.alts:
            assert len(alt.approach) > 150, f"{alt.name}: approach too shallow"
            assert len(alt.pros) >= 2, f"{alt.name}: needs real pros"
            assert len(alt.cons) >= 3, f"{alt.name}: needs real cons"
            assert alt.blast_radius, f"{alt.name}: must declare blast radius"

    def test_recommended_option_admits_the_fan_out_floor(self):
        """The recommended option must not oversell itself."""
        cons = " ".join(self.alts[0].cons).lower()
        assert "still contacts all 50" in cons

    def test_all_alternatives_are_attributed_to_this_agent(self):
        assert all(a.proposed_by == "kafka-broker" for a in self.alts)


# ------------------------------------------------------------------
# ConsultAbout
# ------------------------------------------------------------------

class TestConsultAbout:
    def test_recommends_scatter_gather(self, agent):
        resp = agent.consult_about(_request(1), depth=1)
        recommended = [a for a in resp.design_alternatives if a.recommended]
        assert len(recommended) == 1
        assert "scatter-gather" in recommended[0].name.lower()

    def test_rules_out_the_other_two_with_measured_reasons(self, agent):
        resp = agent.consult_about(_request(1), depth=1)
        ruled_out = [a for a in resp.design_alternatives if a.is_ruled_out]
        assert len(ruled_out) == 2
        for alt in ruled_out:
            assert len(alt.rejected_reason) > 40

    def test_kraft_rejection_cites_the_churn_ratio(self, agent):
        resp = agent.consult_about(_request(1), depth=1)
        kraft = next(a for a in resp.design_alternatives if "KRaft" in a.name)
        assert "32x" in kraft.rejected_reason

    def test_rehash_rejection_cites_the_client_contract(self, agent):
        resp = agent.consult_about(_request(1), depth=1)
        rehash = next(a for a in resp.design_alternatives if "Co-locate" in a.name)
        assert "public contract" in rehash.rejected_reason

    def test_round_one_corrects_the_fan_out_assumption(self, agent):
        """The whole reason this team is in the room."""
        resp = agent.consult_about(_request(1), depth=1)
        joined = " ".join(resp.new_concerns).lower()
        assert "fan out" in joined
        assert "hashcode" in joined or "hash" in joined
        assert "50" in joined

    def test_round_one_quantifies_the_heap_cost_as_a_percentage(self, agent):
        resp = agent.consult_about(_request(1), depth=1)
        joined = " ".join(resp.new_concerns)
        assert "%" in joined
        assert "14MB" in joined

    def test_round_one_raises_partial_results_as_a_protocol_question(self, agent):
        resp = agent.consult_about(_request(1), depth=1)
        assert resp.open_questions
        assert any("partial" in q.lower() for q in resp.open_questions)

    def test_converges_to_agreed_by_round_three(self, agent):
        assert agent.consult_about(_request(1), depth=1).verdict == "needs_changes"
        assert agent.consult_about(_request(2), depth=1).verdict == "needs_changes"
        assert agent.consult_about(_request(3), depth=1).verdict == "agreed"

    def test_stops_raising_concerns_by_the_final_round(self, agent):
        assert agent.consult_about(_request(3), depth=1).new_concerns == []

    def test_never_escalates_to_a_human(self, agent):
        """Routing and heap are engineering decisions, not org-authority ones."""
        for rnd in (1, 2, 3):
            resp = agent.consult_about(_request(rnd), depth=1)
            assert resp.needs_org_authority is False

    def test_produces_a_principal_level_review(self, agent):
        resp = agent.consult_about(_request(1), depth=1)
        assert len(resp.principal_review) > 400

    def test_review_corrects_the_mental_model_explicitly(self, agent):
        review = agent.consult_about(_request(1), depth=1).principal_review.lower()
        assert "o(1) index lookup" in review or "o(1) lookup" in review
        assert "structural" in review

    def test_contributes_test_requirements_for_the_fan_out_path(self, agent):
        reqs = " ".join(agent.consult_about(_request(1), depth=1).test_requirements)
        assert "fan" in reqs.lower()
        assert "partial" in reqs.lower()


# ------------------------------------------------------------------
# Ownership discipline
# ------------------------------------------------------------------

class TestOwnership:
    def test_cites_only_paths_it_owns(self, agent):
        resp = agent.consult_about(_request(1), depth=1)
        results = validate_citations(resp.cited_codepaths, KafkaBrokerAgent.OWNS)
        assert [r.codepath for r in results if not r.owned] == []

    def test_does_not_claim_the_coordinator_or_the_schemas(self):
        owns = " ".join(KafkaBrokerAgent.OWNS)
        assert "group-coordinator/" not in owns
        assert "ListGroupsRequest.json" not in owns

    def test_owns_both_request_handling_and_kraft_metadata(self):
        owns = " ".join(KafkaBrokerAgent.OWNS)
        assert "KafkaApis.scala" in owns
        assert "QuorumController.java" in owns

    def test_declares_no_confluent_specific_paths(self):
        owns = " ".join(KafkaBrokerAgent.OWNS).lower()
        assert "confluent" not in owns
        assert "mirrormaker" not in owns
