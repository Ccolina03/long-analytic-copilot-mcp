"""
Phase 3 — Base Agent Framework tests (Principal Engineer behaviour model).

Focus areas
-----------
  - @tool registration
  - the 3-alternatives requirement is enforced
  - multi-round deliberation loop: rounds, convergence, round cap
  - requires_human is decided ONLY at the end, and only for real org gates
  - consultation depth bounding
  - ownership self-validation on outbound responses
"""

from unittest.mock import MagicMock

import pytest

from agents.base_agent import (
    DirectTransport,
    MockTransport,
    NullTransport,
    PeerTransport,
    SMEAgentBase,
    tool,
)
from proto.sme_agents import (
    DesignAlternative,
    Finding,
    ImpactRequest,
    ImpactResponse,
    Ticket,
)


# ---------------------------------------------------------------------------
# Test fixtures / helper agents
# ---------------------------------------------------------------------------

def _alts(proposer: str, n: int = 3) -> list[DesignAlternative]:
    return [
        DesignAlternative(
            label=chr(ord("A") + i),
            name=f"{proposer} option {chr(ord('A') + i)}",
            proposed_by=proposer,
            approach=f"approach {i}",
        )
        for i in range(n)
    ]


class SimpleTestAgent(SMEAgentBase):
    AGENT_NAME = "test-agent"
    DOMAIN = "Testing"
    OWNS = ["TestFile.java", "test/"]

    @tool("do_something")
    def do_something(self, x: str) -> dict:
        return {"x": x, "done": True}

    @tool("do_something_else")
    def do_something_else(self) -> dict:
        return {"result": "else"}

    def _propose_alternatives(self, ticket, investigation):
        return _alts(self.AGENT_NAME)


class SoloAgent(SMEAgentBase):
    """Owns a ticket with no peers to consult — settles it alone."""
    AGENT_NAME = "solo-agent"
    DOMAIN = "Solo work"
    OWNS = ["Solo.java"]

    def _investigate(self, ticket):
        return {"root_cause": "self-contained", "cited_codepaths": ["Solo.java"]}

    def _propose_alternatives(self, ticket, investigation):
        alts = _alts(self.AGENT_NAME)
        alts[0].recommended = True
        return alts

    def _select_peers(self, ticket, investigation):
        return []


class TwoAlternativeAgent(SMEAgentBase):
    """Violates the principal-engineer contract by proposing only 2 options."""
    AGENT_NAME = "lazy-agent"
    OWNS = []

    def _propose_alternatives(self, ticket, investigation):
        return _alts(self.AGENT_NAME, n=2)


class DeliberatingAgent(SMEAgentBase):
    """Owns a ticket and consults exactly one peer."""
    AGENT_NAME = "driver-agent"
    DOMAIN = "Driving"
    OWNS = ["Driver.java"]

    def _investigate(self, ticket):
        return {"root_cause": "needs peer input", "cited_codepaths": ["Driver.java"]}

    def _propose_alternatives(self, ticket, investigation):
        return _alts(self.AGENT_NAME)

    def _select_peers(self, ticket, investigation):
        return [("peer-agent", "PeerFile.java")]


class StubbornPeer(SMEAgentBase):
    """Never agrees — always raises a new concern. Forces the round cap."""
    AGENT_NAME = "peer-agent"
    OWNS = ["PeerFile.java"]

    def _handle_consultation(self, request, depth):
        return ImpactResponse(
            request_id=request.request_id,
            from_agent=self.AGENT_NAME,
            to_agent=request.from_agent,
            verdict="needs_changes",
            summary=f"still unhappy at round {request.round_number}",
            new_concerns=[f"fresh concern raised in round {request.round_number}"],
        )


class AgreeableePeer(SMEAgentBase):
    """Agrees immediately with no new concerns — converges in round 1."""
    AGENT_NAME = "peer-agent"
    OWNS = ["PeerFile.java"]

    def _handle_consultation(self, request, depth):
        return ImpactResponse(
            request_id=request.request_id,
            from_agent=self.AGENT_NAME,
            to_agent=request.from_agent,
            verdict="agreed",
            summary="looks right to me",
            recommendation="driver-agent option A",
            new_concerns=[],
        )


class OrgGatePeer(SMEAgentBase):
    """Agrees technically but flags a genuine organizational gate."""
    AGENT_NAME = "peer-agent"
    OWNS = ["PeerFile.java"]

    def _handle_consultation(self, request, depth):
        return ImpactResponse(
            request_id=request.request_id,
            from_agent=self.AGENT_NAME,
            to_agent=request.from_agent,
            verdict="agreed",
            summary="design is fine",
            recommendation="driver-agent option A",
            new_concerns=[],
            needs_org_authority=True,
            org_authority_reason="external standards-body vote required",
        )


@pytest.fixture
def ticket():
    return Ticket.new(team="driver-agent", title="t", description="d")


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

class TestToolDecorator:
    def test_registers_method_under_given_name(self):
        tools = SimpleTestAgent().get_tools()
        assert "do_something" in tools
        assert "do_something_else" in tools

    def test_registered_tool_is_callable(self):
        tools = SimpleTestAgent().get_tools()
        assert tools["do_something"]("hello") == {"x": "hello", "done": True}

    def test_does_not_include_non_tool_methods(self):
        tools = SimpleTestAgent().get_tools()
        assert "get_tools" not in tools
        assert "own_ticket" not in tools


# ---------------------------------------------------------------------------
# Principal-engineer contract: always 3 alternatives
# ---------------------------------------------------------------------------

class TestAlternativesContract:
    def test_three_alternatives_is_the_required_count(self):
        assert SMEAgentBase.ALTERNATIVES_REQUIRED == 3

    def test_agent_proposing_only_two_alternatives_raises(self, ticket):
        agent = TwoAlternativeAgent()
        with pytest.raises(ValueError, match="must return exactly 3 design alternatives"):
            agent.own_ticket(ticket)

    def test_finding_carries_all_three_alternatives(self, ticket):
        agent = DeliberatingAgent(
            transport=DirectTransport({"peer-agent": AgreeableePeer()})
        )
        finding = agent.own_ticket(ticket)
        assert len(finding.design_alternatives) == 3

    def test_ruled_out_alternatives_are_kept_not_deleted(self, ticket):
        """The doc must show the full solution space that was considered."""
        agent = DeliberatingAgent(
            transport=DirectTransport({"peer-agent": AgreeableePeer()})
        )
        finding = agent.own_ticket(ticket)
        # Peer endorsed option A, so B and C should be ruled out but still present
        assert len(finding.design_alternatives) == 3
        ruled_out = [a for a in finding.design_alternatives if a.is_ruled_out]
        assert len(ruled_out) == 2
        for alt in ruled_out:
            assert alt.rejected_reason, "ruled-out alternative must explain why"


# ---------------------------------------------------------------------------
# Multi-round deliberation
# ---------------------------------------------------------------------------

class TestDeliberation:
    def test_default_round_cap_is_three(self):
        assert SMEAgentBase.MAX_DELIBERATION_ROUNDS == 3

    def test_converges_in_round_one_when_peer_agrees_immediately(self, ticket):
        agent = DeliberatingAgent(
            transport=DirectTransport({"peer-agent": AgreeableePeer()})
        )
        finding = agent.own_ticket(ticket)
        assert finding.converged is True
        assert finding.rounds_used == 1

    def test_stubborn_peer_forces_the_full_round_cap(self, ticket):
        agent = DeliberatingAgent(
            transport=DirectTransport({"peer-agent": StubbornPeer()})
        )
        finding = agent.own_ticket(ticket)
        assert finding.converged is False
        assert finding.rounds_used == 3

    def test_deliberation_record_has_one_entry_per_round_per_peer(self, ticket):
        agent = DeliberatingAgent(
            transport=DirectTransport({"peer-agent": StubbornPeer()})
        )
        finding = agent.own_ticket(ticket)
        # 3 rounds x 1 peer
        assert len(finding.deliberation) == 3
        assert [r.round_number for r in finding.deliberation] == [1, 2, 3]

    def test_round_number_is_propagated_into_the_request(self, ticket):
        transport = DirectTransport({"peer-agent": StubbornPeer()})
        agent = DeliberatingAgent(transport=transport)
        agent.own_ticket(ticket)
        assert [c.round_number for c in transport.calls] == [1, 2, 3]

    def test_prior_concerns_accumulate_across_rounds(self, ticket):
        transport = DirectTransport({"peer-agent": StubbornPeer()})
        agent = DeliberatingAgent(transport=transport)
        agent.own_ticket(ticket)
        # Round 1 starts empty; later rounds carry forward what was raised
        assert transport.calls[0].prior_concerns == []
        assert len(transport.calls[1].prior_concerns) >= 1
        assert len(transport.calls[2].prior_concerns) >= len(transport.calls[1].prior_concerns)

    def test_new_concerns_are_recorded_in_the_deliberation_log(self, ticket):
        agent = DeliberatingAgent(
            transport=DirectTransport({"peer-agent": StubbornPeer()})
        )
        finding = agent.own_ticket(ticket)
        assert any(r.new_concerns_raised for r in finding.deliberation)

    def test_no_peers_means_immediate_convergence(self, ticket):
        agent = SoloAgent()
        finding = agent.own_ticket(Ticket.new(team="solo-agent", title="t", description="d"))
        assert finding.converged is True
        assert finding.rounds_used == 1

    def test_alternatives_record_who_reviewed_them(self, ticket):
        agent = DeliberatingAgent(
            transport=DirectTransport({"peer-agent": AgreeableePeer()})
        )
        finding = agent.own_ticket(ticket)
        for alt in finding.design_alternatives:
            assert "peer-agent" in alt.reviewed_by


# ---------------------------------------------------------------------------
# requires_human — decided once, at the end, only for real org gates
# ---------------------------------------------------------------------------

class TestRequiresHumanIsEndOnly:
    def test_false_when_agents_converge_with_no_org_gate(self, ticket):
        agent = DeliberatingAgent(
            transport=DirectTransport({"peer-agent": AgreeableePeer()})
        )
        finding = agent.own_ticket(ticket)
        assert finding.converged is True
        assert finding.requires_human is False
        assert finding.human_decision_points == []

    def test_true_when_a_peer_flags_genuine_org_authority(self, ticket):
        agent = DeliberatingAgent(
            transport=DirectTransport({"peer-agent": OrgGatePeer()})
        )
        finding = agent.own_ticket(ticket)
        assert finding.requires_human is True
        assert any("standards-body" in p for p in finding.human_decision_points)

    def test_true_when_deliberation_fails_to_converge(self, ticket):
        agent = DeliberatingAgent(
            transport=DirectTransport({"peer-agent": StubbornPeer()})
        )
        finding = agent.own_ticket(ticket)
        assert finding.requires_human is True
        assert any("did not converge" in p for p in finding.human_decision_points)

    def test_true_when_a_peer_is_unreachable(self, ticket):
        agent = DeliberatingAgent(transport=NullTransport())
        finding = agent.own_ticket(ticket)
        assert finding.requires_human is True
        assert any("Could not reach" in p for p in finding.human_decision_points)

    def test_mid_deliberation_concerns_alone_do_not_escalate(self, ticket):
        """A peer raising concerns then agreeing must NOT trigger escalation."""
        class ConcernThenAgreePeer(SMEAgentBase):
            AGENT_NAME = "peer-agent"
            OWNS = ["PeerFile.java"]

            def _handle_consultation(self, request, depth):
                if request.round_number == 1:
                    return ImpactResponse(
                        request_id=request.request_id,
                        from_agent=self.AGENT_NAME, to_agent=request.from_agent,
                        verdict="needs_changes",
                        summary="concerns in round 1",
                        new_concerns=["memory cost needs checking"],
                    )
                return ImpactResponse(
                    request_id=request.request_id,
                    from_agent=self.AGENT_NAME, to_agent=request.from_agent,
                    verdict="agreed",
                    summary="satisfied now",
                    recommendation="driver-agent option A",
                    new_concerns=[],
                )

        agent = DeliberatingAgent(
            transport=DirectTransport({"peer-agent": ConcernThenAgreePeer()})
        )
        finding = agent.own_ticket(ticket)
        assert finding.rounds_used == 2
        assert finding.converged is True
        assert finding.requires_human is False

    def test_solo_agent_with_recommendation_does_not_escalate(self):
        agent = SoloAgent()
        finding = agent.own_ticket(
            Ticket.new(team="solo-agent", title="t", description="d")
        )
        assert finding.requires_human is False


# ---------------------------------------------------------------------------
# ConsultAbout
# ---------------------------------------------------------------------------

class TestConsultAbout:
    def test_consultation_depth_is_bounded(self):
        depth_calls = []

        class CountingCyclicAgent(SMEAgentBase):
            AGENT_NAME = "cyclic"
            OWNS = []

            def _handle_consultation(inner, request, depth):
                depth_calls.append(depth)
                if depth < inner.MAX_CONSULTATION_DEPTH:
                    sub = ImpactRequest.new(
                        "cyclic", "cyclic", "t", "design_review",
                        consultation_depth=depth + 1,
                    )
                    return inner.consult_about(sub, depth=depth + 1)
                return ImpactResponse(
                    request_id=request.request_id,
                    from_agent="cyclic", to_agent="cyclic", verdict="unknown",
                )

        agent = CountingCyclicAgent()
        agent.consult_about(ImpactRequest.new("a", "cyclic", "t", "design_review"), depth=0)
        assert max(depth_calls) <= CountingCyclicAgent.MAX_CONSULTATION_DEPTH

    def test_depth_limit_returns_timed_out_response(self):
        agent = SimpleTestAgent()
        resp = agent.consult_about(
            ImpactRequest.new("a", "test-agent", "t", "design_review"),
            depth=SimpleTestAgent.MAX_CONSULTATION_DEPTH,
        )
        assert resp.timed_out is True

    def test_flags_unowned_citations_as_open_questions(self):
        class CitingAgent(SMEAgentBase):
            AGENT_NAME = "citing-agent"
            OWNS = ["OwnedFile.java"]

            def _handle_consultation(self, request, depth):
                return ImpactResponse(
                    request_id=request.request_id,
                    from_agent=self.AGENT_NAME, to_agent=request.from_agent,
                    verdict="agreed",
                    cited_codepaths=["OwnedFile.java", "UnownedFile.java"],
                )

        resp = CitingAgent().consult_about(
            ImpactRequest.new("peer", "citing-agent", "t", "design_review")
        )
        assert any("UnownedFile.java" in q for q in resp.open_questions)


# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------

class TestTransports:
    def test_null_transport_returns_timed_out(self):
        resp = NullTransport().consult("b", ImpactRequest.new("a", "b", "t", "r"))
        assert resp.timed_out is True

    def test_mock_transport_returns_canned_response(self):
        canned = ImpactResponse(
            request_id="x", from_agent="b", to_agent="a", verdict="agreed"
        )
        resp = MockTransport({"b": canned}).consult(
            "b", ImpactRequest.new("a", "b", "t", "r")
        )
        assert resp.verdict == "agreed"

    def test_mock_transport_records_calls(self):
        transport = MockTransport({})
        transport.consult("b", ImpactRequest.new("a", "b", "t", "r"))
        assert len(transport.calls) == 1

    def test_mock_transport_times_out_for_unknown_peer(self):
        resp = MockTransport({}).consult(
            "unknown", ImpactRequest.new("a", "unknown", "t", "r")
        )
        assert resp.timed_out is True

    def test_direct_transport_calls_real_peer_code(self):
        transport = DirectTransport({"peer-agent": AgreeableePeer()})
        resp = transport.consult(
            "peer-agent", ImpactRequest.new("a", "peer-agent", "t", "design_review")
        )
        assert resp.from_agent == "peer-agent"
        assert resp.verdict == "agreed"

    def test_direct_transport_times_out_for_unregistered_peer(self):
        resp = DirectTransport({}).consult(
            "nobody", ImpactRequest.new("a", "nobody", "t", "r")
        )
        assert resp.timed_out is True
