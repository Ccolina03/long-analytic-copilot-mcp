"""
SME Agent base class — Phase 3 / Slice 22.1.

Every SME agent subclasses ``SMEAgentBase``.  The base class provides:

  ``OwnTicket(ticket)``
      Full workflow for a ticket that *this* agent's team owns.
      1. Investigate using own tools.
      2. Look up which other teams are affected (Knowledge Graph).
      3. Send ImpactRequests to peer agents.
      4. Assemble the final Finding.

  ``ConsultAbout(request, depth=0)``
      Workflow used when another agent consults *this* one.
      1. Run relevant own tools.
      2. Optionally consult a third agent on its own initiative (depth+1).
      3. Self-validate citations.
      4. Return ImpactResponse.

  ``@tool(name)``
      Class-level decorator that registers a method as an agent tool.

Subclass example::

    class KoraGlobalAgent(SMEAgentBase):
        AGENT_NAME = "kora-global"
        DOMAIN     = "Cluster Linking — failover, offset clamping, mirror ops"
        OWNS       = ["OffsetClampingService.java", "FailoverCoordinator.java"]

        @tool("get_failover_latency")
        def get_failover_latency(self, topic: str, last_hours: int = 24) -> dict:
            ...

Consultation depth
------------------
To prevent infinite consultation chains, ``ConsultAbout()`` refuses to
forward beyond ``MAX_CONSULTATION_DEPTH`` hops.  The default is 3.
"""

from __future__ import annotations

import functools
import logging
from typing import Any, Callable

from proto.sme_agents import Finding, ImpactRequest, ImpactResponse, Ticket

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# @tool decorator
# ---------------------------------------------------------------------------

def tool(name: str) -> Callable:
    """Decorator that tags a method as a named agent tool.

    Usage::

        @tool("get_failover_latency")
        def get_failover_latency(self, topic: str) -> dict:
            ...

    The method is then discoverable via ``agent.get_tools()``.
    """
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            return fn(*args, **kwargs)
        wrapper._tool_name = name  # type: ignore[attr-defined]
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Peer transport abstraction
# ---------------------------------------------------------------------------

class PeerTransport:
    """Abstraction over agent-to-agent communication.

    In production this would send gRPC messages.  In tests it is replaced
    by a ``MockTransport`` that holds canned responses.
    """

    def consult(self, peer_agent_id: str, request: ImpactRequest) -> ImpactResponse:
        """Send ``request`` to ``peer_agent_id`` and return its response."""
        raise NotImplementedError("PeerTransport.consult() must be implemented")


class NullTransport(PeerTransport):
    """Transport that always returns a timed-out response.  Used as the
    safe default when no transport is injected."""

    def consult(self, peer_agent_id: str, request: ImpactRequest) -> ImpactResponse:
        logger.warning(
            "NullTransport: no peer transport configured; returning timed-out response "
            "for request to %s", peer_agent_id
        )
        return ImpactResponse(
            request_id=request.request_id,
            from_agent=peer_agent_id,
            to_agent=request.from_agent,
            verdict="unknown",
            timed_out=True,
            summary="NullTransport: no peer reachable",
        )


class MockTransport(PeerTransport):
    """Test double: holds pre-canned ImpactResponses keyed by peer agent id."""

    def __init__(self, responses: dict[str, ImpactResponse]):
        self._responses = responses

    def consult(self, peer_agent_id: str, request: ImpactRequest) -> ImpactResponse:
        if peer_agent_id in self._responses:
            r = self._responses[peer_agent_id]
            # Patch request_id so callers can correlate
            r.request_id = request.request_id
            return r
        return ImpactResponse(
            request_id=request.request_id,
            from_agent=peer_agent_id,
            to_agent=request.from_agent,
            verdict="unknown",
            timed_out=True,
            summary=f"MockTransport: no canned response for {peer_agent_id}",
        )


# ---------------------------------------------------------------------------
# SMEAgentBase
# ---------------------------------------------------------------------------

class SMEAgentBase:
    """Base class for every SME agent.

    Subclasses must define:
        AGENT_NAME  str   — unique identifier (e.g. "kora-global")
        DOMAIN      str   — plain English description of what this team owns
        OWNS        list  — list of file path prefixes / exact names this
                            agent is authoritative over

    Subclasses may override:
        _investigate(ticket)    — run tools and return raw findings dict
        _select_peers(ticket, investigation)
                                — return [(peer_id, codepath)] to consult
        _build_impact_request(peer_id, ticket, investigation)
                                — build the ImpactRequest to send
        _assemble_finding(ticket, investigation, responses)
                                — produce the final Finding
    """

    AGENT_NAME: str = ""
    DOMAIN: str = ""
    OWNS: list[str] = []
    MAX_CONSULTATION_DEPTH: int = 3

    def __init__(self, transport: PeerTransport | None = None, kg_conn: Any = None):
        """
        Args:
            transport:  peer transport to use for ConsultAbout calls.
                        Defaults to NullTransport.
            kg_conn:    Knowledge Graph database connection.  If None, a
                        fresh connection is opened via ``db.get_connection()``.
        """
        self._transport = transport or NullTransport()
        self._kg_conn = kg_conn

    # ------------------------------------------------------------------
    # Tool registry
    # ------------------------------------------------------------------

    def get_tools(self) -> dict[str, Callable]:
        """Return a dict of {tool_name: bound_method} for all registered tools."""
        tools: dict[str, Callable] = {}
        for attr_name in dir(self.__class__):
            attr = getattr(self.__class__, attr_name, None)
            if callable(attr) and hasattr(attr, "_tool_name"):
                tools[attr._tool_name] = getattr(self, attr_name)
        return tools

    # ------------------------------------------------------------------
    # OwnTicket workflow
    # ------------------------------------------------------------------

    def own_ticket(self, ticket: Ticket) -> Finding:
        """Drive a ticket that this agent's team owns, end to end.

        Step 1 — investigate with own tools.
        Step 2 — determine which peer agents to consult via the KG.
        Step 3 — send ImpactRequests to each peer.
        Step 4 — assemble and return the final Finding.
        """
        logger.info("[%s] OwnTicket: %s (%s)", self.AGENT_NAME, ticket.ticket_id, ticket.title)

        # Step 1
        investigation = self._investigate(ticket)
        logger.debug("[%s] investigation keys: %s", self.AGENT_NAME, list(investigation.keys()))

        # Step 2
        peers_to_consult = self._select_peers(ticket, investigation)
        logger.info("[%s] will consult %d peer(s): %s",
                    self.AGENT_NAME, len(peers_to_consult),
                    [p for p, _ in peers_to_consult])

        # Step 3
        responses: list[ImpactResponse] = []
        for peer_id, codepath in peers_to_consult:
            req = self._build_impact_request(peer_id, ticket, investigation, codepath)
            resp = self._transport.consult(peer_id, req)
            logger.info(
                "[%s] consultation with %s → verdict=%s timed_out=%s",
                self.AGENT_NAME, peer_id, resp.verdict, resp.timed_out,
            )
            responses.append(resp)

        # Step 4
        finding = self._assemble_finding(ticket, investigation, responses)
        logger.info("[%s] OwnTicket complete: confidence=%.2f", self.AGENT_NAME, finding.confidence)
        return finding

    # ------------------------------------------------------------------
    # ConsultAbout workflow
    # ------------------------------------------------------------------

    def consult_about(self, request: ImpactRequest, depth: int = 0) -> ImpactResponse:
        """Respond to a consultation request from a peer agent.

        Runs own tools, optionally consults a further peer (at depth+1),
        self-validates citations, and returns an ImpactResponse.
        """
        if depth >= self.MAX_CONSULTATION_DEPTH:
            logger.warning(
                "[%s] consultation depth %d reached limit %d — returning timed-out response",
                self.AGENT_NAME, depth, self.MAX_CONSULTATION_DEPTH,
            )
            return ImpactResponse(
                request_id=request.request_id,
                from_agent=self.AGENT_NAME,
                to_agent=request.from_agent,
                verdict="unknown",
                timed_out=True,
                summary=f"consultation depth limit {self.MAX_CONSULTATION_DEPTH} reached",
            )

        logger.info(
            "[%s] ConsultAbout from=%s depth=%d", self.AGENT_NAME, request.from_agent, depth
        )

        response = self._handle_consultation(request, depth)

        # Self-validate citations before sending
        from agents.ownership_validator import validate_citations  # noqa: PLC0415
        validation = validate_citations(response.cited_codepaths, self.OWNS)
        unowned = [r.codepath for r in validation if not r.owned]
        if unowned:
            logger.warning(
                "[%s] unowned citations flagged: %s", self.AGENT_NAME, unowned
            )
            response.open_questions.extend(
                [f"needs_verification: citation outside OWNS — {p}" for p in unowned]
            )

        return response

    # ------------------------------------------------------------------
    # Hooks for subclasses to override
    # ------------------------------------------------------------------

    def _investigate(self, ticket: Ticket) -> dict[str, Any]:
        """Run this agent's own tools against the ticket.

        Override in subclass to call real tools and return a findings dict.
        The base implementation returns minimal metadata.
        """
        return {
            "agent": self.AGENT_NAME,
            "ticket_id": ticket.ticket_id,
            "tools_available": list(self.get_tools().keys()),
        }

    def _select_peers(
        self, ticket: Ticket, investigation: dict[str, Any]
    ) -> list[tuple[str, str]]:
        """Return [(peer_agent_id, codepath_of_interest)] to consult.

        Override in subclass to use the Knowledge Graph for dynamic lookup.
        The base implementation returns an empty list (no consultations).
        """
        return []

    def _build_impact_request(
        self,
        peer_id: str,
        ticket: Ticket,
        investigation: dict[str, Any],
        codepath: str = "",
    ) -> ImpactRequest:
        """Build the ImpactRequest to send to ``peer_id``."""
        return ImpactRequest.new(
            from_agent=self.AGENT_NAME,
            to_agent=peer_id,
            ticket_id=ticket.ticket_id,
            request_type="impact_analysis",
            context=ticket.description,
            codepaths_of_interest=[codepath] if codepath else [],
        )

    def _handle_consultation(
        self, request: ImpactRequest, depth: int
    ) -> ImpactResponse:
        """Produce a response to a ConsultAbout request.

        Override in subclass to run real tools and optionally consult further.
        """
        return ImpactResponse(
            request_id=request.request_id,
            from_agent=self.AGENT_NAME,
            to_agent=request.from_agent,
            verdict="unknown",
            summary=f"{self.AGENT_NAME} base implementation — override _handle_consultation()",
        )

    def _assemble_finding(
        self,
        ticket: Ticket,
        investigation: dict[str, Any],
        responses: list[ImpactResponse],
    ) -> Finding:
        """Assemble the final Finding from own investigation + peer responses."""
        all_open = []
        all_approvals = []
        for resp in responses:
            all_open.extend(resp.open_questions)
            if resp.verdict in ("needs_changes", "blocked"):
                all_approvals.append(resp.from_agent)

        return Finding(
            ticket_id=ticket.ticket_id,
            owning_agent=self.AGENT_NAME,
            summary=f"{self.AGENT_NAME} investigated: {ticket.title}",
            root_cause=investigation.get("root_cause", "under investigation"),
            confidence=0.8 if responses else 0.5,
            execution_order=investigation.get("execution_order", []),
            approvals_needed=all_approvals,
            requires_human=bool(all_approvals) or bool(all_open),
            cited_codepaths=investigation.get("cited_codepaths", []),
            open_questions=all_open,
            consultations=[r.to_dict() for r in responses],
        )
