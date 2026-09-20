"""
Phase 5 / oss-kafka agent tests.

Covers the four tools, the three protocol-surface alternatives, and the one
legitimate organizational-authority escalation on this ticket (the Apache
PMC vote) — including that it correctly identifies the unblocking path.
"""

import pytest

from agents.oss_kafka_agent import OssKafkaAgent
from agents.ownership_validator import validate_citations
from proto.sme_agents import ImpactRequest


@pytest.fixture
def agent():
    return OssKafkaAgent()


def _request(round_number: int = 1) -> ImpactRequest:
    return ImpactRequest.new(
        from_agent="consumer-team",
        to_agent="oss-kafka",
        ticket_id="t-oss-1",
        request_type="protocol_review",
        context="Proposing ListGroupsRequest v5 with a topic_partitions filter field.",
        question="Does this need a KIP, and what is the right protocol shape?",
        codepaths_of_interest=["ListGroupsRequest.json", "ApiKeys.java"],
        round_number=round_number,
        consultation_depth=1,
    )


# ------------------------------------------------------------------
# Tools
# ------------------------------------------------------------------

class TestOssKafkaTools:
    def test_search_kips_ranks_kip_518_first(self, agent):
        results = agent.search_kips("ListGroups topic partition filter")
        assert results[0]["kip"] == "KIP-518"

    def test_search_kips_includes_kip_848(self, agent):
        results = agent.search_kips("ListGroups topic partition filter")
        assert "KIP-848" in [r["kip"] for r in results]

    def test_search_kips_returns_new_required_for_unrelated_query(self, agent):
        results = agent.search_kips("something entirely unrelated to groups")
        assert results[0]["status"] == "NEW_REQUIRED"

    def test_search_kips_requires_query(self, agent):
        with pytest.raises(ValueError, match="query is required"):
            agent.search_kips("")

    def test_api_spec_returns_listgroups_history(self, agent):
        spec = agent.get_api_spec("LISTGROUPS")
        assert spec["api_key"] == 16
        assert spec["current_max_version"] == 4
        assert 4 in [v["version"] for v in spec["versions"]]

    def test_api_spec_reports_flexible_since_v3(self, agent):
        """This is why tagged fields are safe — it's load-bearing for the design."""
        assert agent.get_api_spec("LISTGROUPS")["flexible_since_version"] == 3

    def test_api_spec_includes_proposed_v5_field(self, agent):
        assert "topic_partitions" in agent.get_api_spec("LISTGROUPS")["proposed_v5_field"]

    def test_api_spec_handles_unknown_api(self, agent):
        spec = agent.get_api_spec("NotARealApi")
        assert spec["api_key"] == -1

    def test_api_spec_requires_name(self, agent):
        with pytest.raises(ValueError):
            agent.get_api_spec("")

    def test_check_compat_flags_kip_848(self, agent):
        result = agent.check_compat(16, 5, ["topic_partitions"])
        review = [c for c in result["checks"] if c["status"] == "REVIEW_NEEDED"]
        assert len(review) >= 1
        assert any("848" in c["note"] for c in review)

    def test_check_compat_passes_the_backward_compat_rules(self, agent):
        result = agent.check_compat(16, 5, ["topic_partitions"])
        assert len([c for c in result["checks"] if c["status"] == "PASS"]) >= 3

    def test_check_compat_overall_is_pass_with_note(self, agent):
        assert agent.check_compat(16, 5, ["topic_partitions"])["overall"] == "PASS_WITH_NOTE"

    def test_kip_template_has_required_sections(self, agent):
        t = agent.get_kip_template("ListGroups", "new_field")
        for section in ("## Motivation", "## Proposed Changes", "## Compatibility",
                        "## Rejected Alternatives"):
            assert section in t


# ------------------------------------------------------------------
# Protocol alternatives
# ------------------------------------------------------------------

class TestOssKafkaAlternatives:
    def setup_method(self):
        a = OssKafkaAgent()
        self.alts = a._protocol_alternatives(
            a.get_api_spec("LISTGROUPS"),
            a.check_compat(16, 5, ["topic_partitions"]),
        )

    def test_proposes_exactly_three(self):
        assert len(self.alts) == 3

    def test_spans_versioned_field_new_api_and_internal_only(self):
        names = " ".join(a.name.lower() for a in self.alts)
        assert "v5" in names
        assert "api key" in names
        assert "internal" in names

    def test_every_alternative_has_depth(self):
        for alt in self.alts:
            assert len(alt.pros) >= 2
            assert len(alt.cons) >= 3
            assert len(alt.approach) > 150

    def test_v5_field_cites_kip_518_precedent(self):
        joined = " ".join(self.alts[0].pros)
        assert "KIP-518" in joined

    def test_internal_only_option_names_the_fork_risk(self):
        joined = " ".join(self.alts[2].cons).lower()
        assert "fork" in joined


# ------------------------------------------------------------------
# ConsultAbout
# ------------------------------------------------------------------

class TestOssKafkaConsultAbout:
    def test_recommends_the_v5_tagged_field(self, agent):
        resp = agent.consult_about(_request(1), depth=1)
        assert "v5" in resp.recommendation
        recommended = [a for a in resp.design_alternatives if a.recommended]
        assert len(recommended) == 1
        assert "v5" in recommended[0].name

    def test_rules_out_the_other_two_with_reasons(self, agent):
        resp = agent.consult_about(_request(1), depth=1)
        ruled_out = [a for a in resp.design_alternatives if a.is_ruled_out]
        assert len(ruled_out) == 2
        assert all(len(a.rejected_reason) > 20 for a in ruled_out)

    def test_summary_cites_kip_518_as_prior_art(self, agent):
        resp = agent.consult_about(_request(1), depth=1)
        assert "KIP-518" in resp.summary

    def test_round_one_raises_the_kip_848_concern(self, agent):
        resp = agent.consult_about(_request(1), depth=1)
        assert any("848" in c for c in resp.new_concerns)

    def test_round_two_agrees(self, agent):
        resp = agent.consult_about(_request(2), depth=1)
        assert resp.verdict == "agreed"

    def test_raises_org_authority_for_the_pmc_vote(self, agent):
        """This is the one legitimate human gate on the whole ticket."""
        resp = agent.consult_about(_request(1), depth=1)
        assert resp.needs_org_authority is True
        reason = resp.org_authority_reason.lower()
        assert "pmc" in reason
        assert "vote" in reason

    def test_org_authority_reason_identifies_the_unblocking_path(self, agent):
        """A principal engineer says what is NOT blocked, not just what is."""
        resp = agent.consult_about(_request(1), depth=1)
        reason = resp.org_authority_reason.lower()
        assert "public api" in reason
        assert "without it" in reason or "can ship" in reason

    def test_summary_explains_the_fix_is_not_gated_on_the_vote(self, agent):
        resp = agent.consult_about(_request(1), depth=1)
        assert "not gated" in resp.summary.lower()

    def test_principal_review_separates_perf_fix_from_api_surface(self, agent):
        resp = agent.consult_about(_request(1), depth=1)
        review = resp.principal_review.lower()
        assert len(resp.principal_review) > 400
        assert "no kip" in review or "internal implementation detail" in review

    def test_cites_only_codepaths_it_owns(self, agent):
        resp = agent.consult_about(_request(1), depth=1)
        results = validate_citations(resp.cited_codepaths, OssKafkaAgent.OWNS)
        assert [r.codepath for r in results if not r.owned] == []

    def test_supplies_protocol_test_requirements(self, agent):
        resp = agent.consult_about(_request(1), depth=1)
        assert len(resp.test_requirements) >= 4
        joined = " ".join(resp.test_requirements).lower()
        assert "unsupported_version" in joined
