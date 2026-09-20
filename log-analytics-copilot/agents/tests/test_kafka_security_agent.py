"""kafka-security agent tests.

This agent is how we show discovery is doing real work: mirrormaker never
names it, but an authorization consequence still pulls it in, and it raises
a concern no other team owns — that a topic-scoped ListGroups is a new
read path over the topic→group edge.
"""

import pytest

from agents.kafka_security_agent import KafkaSecurityAgent
from agents.ownership_validator import validate_citations
from proto.sme_agents import ImpactRequest


@pytest.fixture
def agent():
    return KafkaSecurityAgent()


def _request(round_number: int = 1) -> ImpactRequest:
    return ImpactRequest.new(
        from_agent="mirrormaker",
        to_agent="kafka-security",
        ticket_id="KAFKA-18231",
        request_type="design_review",
        context="MM2 wants a topic filter on ListGroups.",
        question="Does the topic→group relationship need its own authorization?",
        codepaths_of_interest=["StandardAuthorizer.java"],
        round_number=round_number,
    )


class TestAclRequirements:
    def test_listgroups_has_no_topic_acl_today(self, agent):
        acls = agent.get_acl_requirements("LISTGROUPS")
        assert acls["topic_acl_involvement"] == "none"
        assert acls["primary_check"]["resource_type"] == "CLUSTER"

    def test_names_the_relationship_gap(self, agent):
        acls = agent.get_acl_requirements("LISTGROUPS")
        assert "topic" in acls["gap"].lower()
        assert "group" in acls["gap"].lower()

    def test_rejects_empty_api_name(self, agent):
        with pytest.raises(ValueError):
            agent.get_acl_requirements("")


class TestInformationDisclosure:
    def test_filter_is_an_information_disclosure(self, agent):
        d = agent.analyze_information_disclosure("ListGroups", "topic_partitions")
        assert d["classification"] == "information_disclosure"
        assert d["exploitable_without_new_acls"] is True

    def test_names_the_existence_oracle(self, agent):
        d = agent.analyze_information_disclosure("ListGroups", "topic_partitions")
        vectors = {v["vector"] for v in d["leak_vectors"]}
        assert "topic_existence_oracle" in vectors
        assert "consumption_topology" in vectors

    def test_requires_both_arguments(self, agent):
        with pytest.raises(ValueError):
            agent.analyze_information_disclosure("ListGroups", "")


class TestAuthorizerCost:
    def test_batch_is_within_budget(self, agent):
        cost = agent.get_authorizer_cost(8)
        assert cost["within_budget"] is True
        assert cost["total_us"] < cost["budget_us"]

    def test_rejects_negative_checks(self, agent):
        with pytest.raises(ValueError):
            agent.get_authorizer_cost(-1)


class TestConsultation:
    def test_round_one_raises_the_authorization_gap(self, agent):
        resp = agent.consult_about(_request(1))
        assert resp.verdict == "needs_changes"
        joined = " ".join(resp.new_concerns).lower()
        assert "authorization" in joined
        assert "topic" in joined
        assert "omission" in joined or "drop" in joined or "omit" in joined

    def test_recommends_per_topic_describe(self, agent):
        resp = agent.consult_about(_request(1))
        rec = next(a for a in resp.design_alternatives if a.recommended)
        assert "DESCRIBE" in rec.name or "topic" in rec.name.lower()

    def test_rejects_cluster_wide_describe(self, agent):
        resp = agent.consult_about(_request(1))
        cluster = next(
            a for a in resp.design_alternatives if "CLUSTER" in a.name
        )
        assert cluster.is_ruled_out
        assert "mm2" in cluster.rejected_reason.lower() or "privilege" in cluster.rejected_reason.lower()

    def test_rejects_shipping_with_no_authz_change(self, agent):
        resp = agent.consult_about(_request(1))
        none = next(
            a for a in resp.design_alternatives
            if a.name.lower().startswith("no new")
        )
        assert none.is_ruled_out
        assert "edge" in none.rejected_reason.lower() or "oracle" in none.rejected_reason.lower()

    def test_agrees_by_round_three(self, agent):
        resp = agent.consult_about(_request(3))
        assert resp.verdict == "agreed"
        assert not resp.new_concerns

    def test_citations_are_owned(self, agent):
        resp = agent.consult_about(_request(1))
        results = validate_citations(resp.cited_codepaths, KafkaSecurityAgent.OWNS)
        assert [r.codepath for r in results if not r.owned] == []

    def test_principal_review_is_substantive(self, agent):
        resp = agent.consult_about(_request(1))
        assert len(resp.principal_review) > 400

    def test_always_returns_three_alternatives(self, agent):
        resp = agent.consult_about(_request(1))
        assert len(resp.design_alternatives) == 3
