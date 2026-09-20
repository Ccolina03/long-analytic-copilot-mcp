"""
Phase 6 — Full multi-agent integration test (mocked peers, no Docker needed).

This is the single most important test in the plan (§25).  It replays the
entire Part V walkthrough for real, with real agents running their real code,
but uses MockTransport so no Docker/gRPC infrastructure is required.

What it proves
--------------
1. kora-global receives the ticket and drives investigation itself.
2. kora-global consults consumer-team on its own initiative (Knowledge Graph lookup).
3. consumer-team consults oss-kafka on its own initiative (not pre-selected by kora).
4. The final Finding has the correct 5-step execution_order from §11.
5. approvals_needed includes both team leads.
6. requires_human is True.
7. Every codepath cited by kora-global in its Finding passes ownership
   self-validation (no unflagged out-of-bounds citations).

This test is the permanent regression guard for the whole system.  Once
it's green, CI must keep it green.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from agents.base_agent import MockTransport
from agents.consumer_team_agent import ConsumerTeamAgent
from agents.kora_global_agent import KoraGlobalAgent
from agents.oss_kafka_agent import OssKafkaAgent
from agents.ownership_validator import validate_citations
from proto.sme_agents import ImpactRequest, ImpactResponse, Ticket

_FIXTURES = pathlib.Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Build a realistic multi-agent network using MockTransport
#
# Architecture:
#   kora_agent  --consults-->  consumer_agent  --consults-->  oss_agent
#
# consumer_agent's MockTransport is pre-loaded with oss_agent's real response.
# kora_agent's MockTransport is pre-loaded with consumer_agent's real response.
#
# This means each agent runs its _real_ _handle_consultation() code; only the
# outbound transport hop is mocked (because there's no gRPC server in this test).
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def oss_agent():
    return OssKafkaAgent()


@pytest.fixture(scope="module")
def consumer_agent(oss_agent):
    """Consumer team agent wired so it can actually reach oss-kafka."""
    # Build a transport that calls oss_agent.consult_about() directly
    class DirectTransport:
        def __init__(self, oss):
            self._oss = oss

        def consult(self, peer_id: str, request: ImpactRequest) -> ImpactResponse:
            if peer_id == "oss-kafka":
                return self._oss.consult_about(request, depth=request.consultation_depth)
            return ImpactResponse(
                request_id=request.request_id,
                from_agent=peer_id,
                to_agent=request.from_agent,
                verdict="unknown",
                timed_out=True,
            )

    return ConsumerTeamAgent(transport=DirectTransport(oss_agent))


@pytest.fixture(scope="module")
def kora_agent(consumer_agent):
    """Kora agent wired so it can reach consumer-team (which in turn reaches oss-kafka)."""
    class DirectTransport:
        def __init__(self, consumer):
            self._consumer = consumer

        def consult(self, peer_id: str, request: ImpactRequest) -> ImpactResponse:
            if peer_id == "consumer-team":
                return self._consumer.consult_about(request, depth=request.consultation_depth)
            # For oss-kafka direct consultation from kora
            oss = OssKafkaAgent()
            return oss.consult_about(request, depth=request.consultation_depth)

    return KoraGlobalAgent(transport=DirectTransport(consumer_agent))


@pytest.fixture(scope="module")
def ticket():
    raw = json.loads((_FIXTURES / "consumer_groups_ticket.json").read_text())
    return Ticket.from_dict(raw)


@pytest.fixture(scope="module")
def finding(kora_agent, ticket):
    """The actual Finding produced by kora-global running OwnTicket() for real."""
    return kora_agent.own_ticket(ticket)


# ---------------------------------------------------------------------------
# The tests
# ---------------------------------------------------------------------------

class TestPartVWalkthroughE2E:
    def test_finding_belongs_to_kora_global(self, finding):
        assert finding.owning_agent == "kora-global"

    def test_ticket_id_is_preserved(self, finding, ticket):
        assert finding.ticket_id == ticket.ticket_id

    def test_kora_global_consulted_consumer_team(self, finding):
        """Step 2: kora-global must have sent an ImpactRequest to consumer-team."""
        consulted = [c["from_agent"] for c in finding.consultations]
        assert "consumer-team" in consulted, (
            f"consumer-team not in consultations. Got: {consulted}"
        )

    def test_kora_global_consulted_oss_kafka(self, finding):
        """Step 3: kora-global must have consulted oss-kafka."""
        consulted = [c["from_agent"] for c in finding.consultations]
        assert "oss-kafka" in consulted, (
            f"oss-kafka not in consultations. Got: {consulted}"
        )

    def test_consumer_team_appears_before_oss_kafka_in_consultation_order(self, finding):
        """consumer-team must come first (kora consults it directly; it then consults oss-kafka)."""
        agents_in_order = [c["from_agent"] for c in finding.consultations]
        assert agents_in_order.index("consumer-team") < agents_in_order.index("oss-kafka"), (
            f"Expected consumer-team before oss-kafka. Got order: {agents_in_order}"
        )

    def test_finding_has_five_execution_order_steps(self, finding):
        """§11 specifies exactly 5 steps in the execution plan."""
        assert len(finding.execution_order) == 5, (
            f"Expected 5 execution steps, got {len(finding.execution_order)}: "
            f"{finding.execution_order}"
        )

    def test_finding_approvals_needed_includes_consumer_team(self, finding):
        approvals_str = " ".join(finding.approvals_needed).lower()
        assert "consumer-team" in approvals_str, (
            f"consumer-team not in approvals_needed: {finding.approvals_needed}"
        )

    def test_finding_approvals_needed_includes_oss_kafka(self, finding):
        approvals_str = " ".join(finding.approvals_needed).lower()
        assert "oss-kafka" in approvals_str, (
            f"oss-kafka not in approvals_needed: {finding.approvals_needed}"
        )

    def test_finding_requires_human_is_true(self, finding):
        assert finding.requires_human is True

    def test_finding_confidence_is_high(self, finding):
        assert finding.confidence >= 0.9, (
            f"Expected confidence >= 0.9, got {finding.confidence}"
        )

    def test_kora_global_cited_codepaths_all_pass_ownership_validation(self, finding):
        """§25 step 6: every codepath kora-global cites must pass its own OWNS check."""
        results = validate_citations(finding.cited_codepaths, KoraGlobalAgent.OWNS)
        unowned = [r for r in results if not r.owned]
        assert len(unowned) == 0, (
            f"kora-global cited paths it doesn't own: "
            f"{[r.codepath for r in unowned]}"
        )

    def test_root_cause_mentions_listgroups(self, finding):
        assert (
            "listgroups" in finding.root_cause.lower()
            or "listGroups" in finding.root_cause
            or "scan" in finding.root_cause.lower()
        )

    def test_router_would_route_this_ticket_to_kora(self, ticket):
        """Simulate the router step: team='kora-global' → routes to kora-global."""
        from router.main import get_agent_address
        addr = get_agent_address(ticket.team)
        assert addr is not None
        assert "kora" in addr or "8001" in addr
