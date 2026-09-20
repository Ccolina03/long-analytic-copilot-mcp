"""
Phase 5 / oss-kafka agent tests.

Tests
-----
  test_search_kips_ranks_kip_518_as_closest_precedent_for_fixture_query
  test_search_kips_requires_query
  test_get_api_spec_returns_listgroups_version_history
  test_check_compat_flags_kip_848_compatibility_note
  test_check_compat_passes_basic_rules_for_listgroups_v5
  test_get_kip_template_contains_motivation_section
  test_consult_about_includes_kip_518_in_summary
  test_consult_about_response_cited_codepaths_are_oss_kafka_owned
"""

import pytest

from agents.oss_kafka_agent import OssKafkaAgent
from proto.sme_agents import ImpactRequest


@pytest.fixture
def agent():
    return OssKafkaAgent()


@pytest.fixture
def consumer_request():
    return ImpactRequest.new(
        from_agent="consumer-team",
        to_agent="oss-kafka",
        ticket_id="t-oss-1",
        request_type="protocol_review",
        context=(
            "Proposing ListGroupsRequest v5 — add topic_partitions filter field.\n"
            "Schema: topic_partitions ARRAY(STRUCT(topic STRING, partition INT32))\n"
            "Backward compat: empty field = existing behavior."
        ),
        codepaths_of_interest=["ListGroupsRequest.json", "ApiKeys.java"],
        proposed_change="ListGroupsRequest v5 with optional topic_partitions filter",
        consultation_depth=1,
    )


# ------------------------------------------------------------------
# Tool tests
# ------------------------------------------------------------------

class TestOssKafkaTools:
    def test_search_kips_ranks_kip_518_as_closest_precedent(self, agent):
        """KIP-518 is the direct predecessor and must appear first."""
        results = agent.search_kips("ListGroups topic partition filter")
        kip_ids = [r["kip"] for r in results]
        assert "KIP-518" in kip_ids
        # KIP-518 should be ranked first (highest relevance)
        assert results[0]["kip"] == "KIP-518"

    def test_search_kips_includes_kip_848(self, agent):
        results = agent.search_kips("ListGroups topic partition filter")
        kip_ids = [r["kip"] for r in results]
        assert "KIP-848" in kip_ids

    def test_search_kips_requires_nonempty_query(self, agent):
        with pytest.raises(ValueError, match="query is required"):
            agent.search_kips("")

    def test_search_kips_returns_list(self, agent):
        result = agent.search_kips("some query")
        assert isinstance(result, list)

    def test_get_api_spec_returns_listgroups_version_history(self, agent):
        spec = agent.get_api_spec("LISTGROUPS")
        assert spec["api"] == "ListGroups"
        assert spec["api_key"] == 16
        assert spec["current_max_version"] == 4
        versions = [v["version"] for v in spec["versions"]]
        assert 4 in versions  # KIP-518 version

    def test_get_api_spec_includes_proposed_v5_field(self, agent):
        spec = agent.get_api_spec("LISTGROUPS")
        assert "topic_partitions" in spec["proposed_v5_field"]

    def test_check_compat_flags_kip_848_compatibility_note(self, agent):
        result = agent.check_compat(16, 5, ["topic_partitions"])
        review_needed = [
            c for c in result["checks"] if c["status"] == "REVIEW_NEEDED"
        ]
        assert len(review_needed) >= 1
        assert any("848" in c["note"] or "KIP-848" in c["note"] for c in review_needed)

    def test_check_compat_passes_basic_rules_for_listgroups_v5(self, agent):
        result = agent.check_compat(16, 5, ["topic_partitions"])
        # Backward compat and old-client rules must pass
        pass_checks = [c for c in result["checks"] if c["status"] == "PASS"]
        assert len(pass_checks) >= 2

    def test_check_compat_overall_is_pass_with_note(self, agent):
        result = agent.check_compat(16, 5, ["topic_partitions"])
        assert result["overall"] == "PASS_WITH_NOTE"

    def test_get_kip_template_contains_motivation_section(self, agent):
        template = agent.get_kip_template("ListGroups", "new_field")
        assert "## Motivation" in template
        assert "clampOffsets" in template.lower() or "cluster linking" in template.lower()

    def test_get_kip_template_contains_compatibility_section(self, agent):
        template = agent.get_kip_template("ListGroups", "new_field")
        assert "Compatibility" in template or "compat" in template.lower()


# ------------------------------------------------------------------
# ConsultAbout tests
# ------------------------------------------------------------------

class TestOssKafkaConsultAbout:
    def test_consult_about_includes_kip_518_in_response(self, agent, consumer_request):
        response = agent.consult_about(consumer_request, depth=1)
        assert "KIP-518" in response.summary or "kip-518" in response.summary.lower()

    def test_consult_about_response_cited_codepaths_are_oss_kafka_owned(
        self, agent, consumer_request
    ):
        response = agent.consult_about(consumer_request, depth=1)
        from agents.ownership_validator import validate_citations
        results = validate_citations(response.cited_codepaths, OssKafkaAgent.OWNS)
        unowned = [r for r in results if not r.owned]
        assert len(unowned) == 0, (
            f"oss-kafka cited codepaths it doesn't own: "
            f"{[r.codepath for r in unowned]}"
        )

    def test_consult_about_verdict_is_needs_changes(self, agent, consumer_request):
        response = agent.consult_about(consumer_request, depth=1)
        assert response.verdict == "needs_changes"

    def test_consult_about_from_agent_is_oss_kafka(self, agent, consumer_request):
        response = agent.consult_about(consumer_request, depth=1)
        assert response.from_agent == "oss-kafka"

    def test_consult_about_mentions_kip_timeline(self, agent, consumer_request):
        response = agent.consult_about(consumer_request, depth=1)
        # Response should mention the ~6 week timeline or fast-path option
        assert "week" in response.summary.lower() or "kip" in response.summary.lower()
