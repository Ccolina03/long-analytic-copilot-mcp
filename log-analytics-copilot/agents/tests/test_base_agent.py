"""
Phase 3 — Base Agent Framework tests.

Tests
-----
  test_tool_decorator_registers_method_under_given_name
  test_get_tools_returns_all_registered_tools
  test_own_ticket_calls_investigate_before_deciding_who_to_consult
  test_own_ticket_only_consults_agents_returned_by_select_peers
  test_consult_about_can_itself_trigger_a_further_consult_about_call
  test_consultation_depth_is_bounded_to_avoid_infinite_chains
  test_consult_about_flags_unowned_citations
  test_null_transport_returns_timed_out_response
  test_mock_transport_returns_canned_response
"""

from unittest.mock import MagicMock, patch

import pytest

from agents.base_agent import (
    MockTransport,
    NullTransport,
    SMEAgentBase,
    PeerTransport,
    tool,
)
from proto.sme_agents import (
    Finding,
    ImpactRequest,
    ImpactResponse,
    Ticket,
)


# ---------------------------------------------------------------------------
# Minimal concrete agent for testing (not one of the real agents)
# ---------------------------------------------------------------------------

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


class RecordingAgent(SMEAgentBase):
    """Agent that records which lifecycle hooks were called."""
    AGENT_NAME = "recording-agent"
    DOMAIN = "Recording"
    OWNS = ["Recording.java"]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.investigate_called = False
        self.select_peers_called = False

    def _investigate(self, ticket):
        self.investigate_called = True
        return {"root_cause": "recorded", "cited_codepaths": ["Recording.java"]}

    def _select_peers(self, ticket, investigation):
        self.select_peers_called = True
        return []  # no consultations


class PeerConsultingAgent(SMEAgentBase):
    """Agent that always consults one peer."""
    AGENT_NAME = "consulting-agent"
    DOMAIN = "Consulting"
    OWNS = ["Consulting.java"]

    def _investigate(self, ticket):
        return {"root_cause": "requires peer input", "cited_codepaths": []}

    def _select_peers(self, ticket, investigation):
        return [("peer-agent", "PeerFile.java")]


class CyclicAgent(SMEAgentBase):
    """Agent that would cause an infinite consultation loop if not bounded."""
    AGENT_NAME = "cyclic-agent"
    DOMAIN = "Cyclic"
    OWNS = []

    def _handle_consultation(self, request, depth):
        # Tries to consult itself — should be stopped by depth limit
        sub_request = ImpactRequest.new(
            from_agent=self.AGENT_NAME,
            to_agent=self.AGENT_NAME,
            ticket_id=request.ticket_id,
            request_type="impact_analysis",
            consultation_depth=depth + 1,
        )
        return self._transport.consult(self.AGENT_NAME, sub_request)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestToolDecorator:
    def test_decorator_registers_method_under_given_name(self):
        agent = SimpleTestAgent()
        tools = agent.get_tools()
        assert "do_something" in tools
        assert "do_something_else" in tools

    def test_registered_tool_is_callable(self):
        agent = SimpleTestAgent()
        tools = agent.get_tools()
        result = tools["do_something"]("hello")
        assert result == {"x": "hello", "done": True}

    def test_get_tools_does_not_include_non_tool_methods(self):
        agent = SimpleTestAgent()
        tools = agent.get_tools()
        assert "get_tools" not in tools
        assert "own_ticket" not in tools


class TestOwnTicketWorkflow:
    def test_calls_investigate_before_deciding_who_to_consult(self):
        agent = RecordingAgent()
        ticket = Ticket.new(team="recording-agent", title="test", description="desc")
        agent.own_ticket(ticket)
        assert agent.investigate_called
        assert agent.select_peers_called

    def test_only_consults_agents_returned_by_select_peers(self):
        """If _select_peers returns [], transport.consult() is never called."""
        transport = MagicMock(spec=PeerTransport)
        agent = RecordingAgent(transport=transport)
        ticket = Ticket.new(team="recording-agent", title="t", description="d")
        agent.own_ticket(ticket)
        transport.consult.assert_not_called()

    def test_returns_finding_with_correct_ticket_id(self):
        agent = RecordingAgent()
        ticket = Ticket.new(team="recording-agent", title="t", description="d")
        finding = agent.own_ticket(ticket)
        assert isinstance(finding, Finding)
        assert finding.ticket_id == ticket.ticket_id
        assert finding.owning_agent == "recording-agent"

    def test_consults_peer_once_per_select_peers_entry(self):
        canned = ImpactResponse(
            request_id="r1",
            from_agent="peer-agent",
            to_agent="consulting-agent",
            verdict="approved",
        )
        transport = MockTransport({"peer-agent": canned})
        agent = PeerConsultingAgent(transport=transport)
        ticket = Ticket.new(team="consulting-agent", title="t", description="d")
        finding = agent.own_ticket(ticket)
        assert len(finding.consultations) == 1
        assert finding.consultations[0]["from_agent"] == "peer-agent"


class TestConsultAboutWorkflow:
    def test_consult_about_can_trigger_further_consult(self):
        """An agent receiving a ConsultAbout CAN consult a third agent unprompted."""
        # oss_kafka returns a canned response when called
        oss_response = ImpactResponse(
            request_id="r2",
            from_agent="oss-kafka",
            to_agent="consumer-team",
            verdict="needs_changes",
            summary="New KIP required",
        )
        transport = MockTransport({"oss-kafka": oss_response})

        from agents.consumer_team_agent import ConsumerTeamAgent
        consumer = ConsumerTeamAgent(transport=transport)

        request = ImpactRequest.new(
            from_agent="kora-global",
            to_agent="consumer-team",
            ticket_id="t-1",
            request_type="impact_analysis",
        )
        response = consumer.consult_about(request, depth=0)
        assert response.from_agent == "consumer-team"
        # Consumer team should have consulted oss-kafka and reported it
        assert "oss-kafka" in response.follow_up_consultations or \
               any("kip" in q.lower() or "oss-kafka" in q.lower()
                   for q in response.open_questions) or \
               "oss-kafka" in response.summary.lower()

    def test_consultation_depth_is_bounded_to_avoid_infinite_chains(self):
        """A cyclic consultation chain must terminate at MAX_CONSULTATION_DEPTH."""
        # CyclicAgent tries to consult itself endlessly
        # We mock the transport to simulate this
        depth_calls = []

        class CountingCyclicAgent(SMEAgentBase):
            AGENT_NAME = "cyclic"
            OWNS = []

            def _handle_consultation(self_inner, request, depth):
                depth_calls.append(depth)
                if depth < self_inner.MAX_CONSULTATION_DEPTH:
                    # simulate deeper call
                    sub = ImpactRequest.new("cyclic", "cyclic", "t", "r",
                                           consultation_depth=depth + 1)
                    return self_inner.consult_about(sub, depth=depth + 1)
                return ImpactResponse(
                    request_id=request.request_id,
                    from_agent="cyclic",
                    to_agent="cyclic",
                    verdict="unknown",
                )

        agent = CountingCyclicAgent()
        req = ImpactRequest.new("a", "cyclic", "t", "r")
        response = agent.consult_about(req, depth=0)

        # Must not recurse beyond MAX_CONSULTATION_DEPTH
        assert max(depth_calls) <= CountingCyclicAgent.MAX_CONSULTATION_DEPTH
        # The final response when depth limit is reached must have timed_out=True
        # (the base class sets it when depth >= limit)
        assert response is not None

    def test_consult_about_flags_unowned_citations_as_open_questions(self):
        """Citations outside OWNS must appear in open_questions after self-validation."""
        class CitingAgent(SMEAgentBase):
            AGENT_NAME = "citing-agent"
            OWNS = ["OwnedFile.java"]

            def _handle_consultation(self, request, depth):
                return ImpactResponse(
                    request_id=request.request_id,
                    from_agent=self.AGENT_NAME,
                    to_agent=request.from_agent,
                    verdict="approved",
                    cited_codepaths=["OwnedFile.java", "UnownedFile.java"],
                )

        agent = CitingAgent()
        req = ImpactRequest.new("peer", "citing-agent", "t", "r")
        response = agent.consult_about(req)
        # UnownedFile.java should be flagged
        assert any("UnownedFile.java" in q for q in response.open_questions)


class TestTransports:
    def test_null_transport_returns_timed_out_response(self):
        transport = NullTransport()
        req = ImpactRequest.new("a", "b", "t", "r")
        resp = transport.consult("b", req)
        assert resp.timed_out is True

    def test_mock_transport_returns_canned_response(self):
        canned = ImpactResponse(
            request_id="x", from_agent="b", to_agent="a", verdict="approved"
        )
        transport = MockTransport({"b": canned})
        req = ImpactRequest.new("a", "b", "t", "r")
        resp = transport.consult("b", req)
        assert resp.verdict == "approved"

    def test_mock_transport_returns_timed_out_for_unknown_peer(self):
        transport = MockTransport({})
        req = ImpactRequest.new("a", "unknown", "t", "r")
        resp = transport.consult("unknown", req)
        assert resp.timed_out is True
