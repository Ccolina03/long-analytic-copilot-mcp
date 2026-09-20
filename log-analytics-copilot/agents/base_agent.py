"""
SME Agent base class — Principal Engineer behaviour model.

Every SME agent subclasses ``SMEAgentBase``.  Agents are modelled on
**Principal Engineers**, not assistants.  Concretely that means:

  1. They never answer a design question with a bare yes/no.  Every
     consultation response carries ``ALTERNATIVES_REQUIRED`` (3) fully-argued
     design alternatives with approach, pros, cons, effort, risk, and blast
     radius.
  2. They **deliberate across multiple rounds** (``MAX_DELIBERATION_ROUNDS``,
     default 3), challenging each other's proposals and narrowing the
     solution space, rather than doing one shallow request/response hop.
  3. They **converge on a recommendation themselves**.  Ordinary technical
     uncertainty is something they resolve by talking to each other.
  4. ``requires_human`` is decided **exactly once, at the very end**, and only
     for decisions that genuinely need organizational authority — an external
     process (e.g. an Apache KIP vote), a budget commitment, or an SLA change.
     It is never set just because an open question exists mid-deliberation.

Core workflows
--------------
  ``own_ticket(ticket)``
      Full workflow for a ticket this agent's team owns.
      1. Investigate using own tools.
      2. Propose 3 design alternatives.
      3. Look up affected teams (Knowledge Graph).
      4. Deliberate with peers for up to MAX_DELIBERATION_ROUNDS rounds,
         refining the alternatives each round until convergence.
      5. Assemble the final Finding as a 1-page design doc, deciding
         requires_human once, here.

  ``consult_about(request, depth=0)``
      Workflow used when another agent consults this one.
      1. Run relevant own tools.
      2. Produce 3 alternatives for the part of the problem it owns.
      3. Optionally consult a third agent on its own initiative (depth+1).
      4. Self-validate citations against OWNS.
      5. Return ImpactResponse.

Subclass example::

    class KoraGlobalAgent(SMEAgentBase):
        AGENT_NAME = "kora-global"
        DOMAIN     = "Cluster Linking — failover, offset clamping, mirror ops"
        OWNS       = ["OffsetClampingService.java", "FailoverCoordinator.java"]

        @tool("get_failover_latency")
        def get_failover_latency(self, topic: str, last_hours: int = 24) -> dict:
            ...
"""

from __future__ import annotations

import functools
import logging
from typing import Any, Callable

from proto.sme_agents import (
    DeliberationRound,
    DesignAlternative,
    Finding,
    ImpactRequest,
    ImpactResponse,
    TeamInvolvement,
    Ticket,
)

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
    by a ``MockTransport`` or a ``DirectTransport`` that calls the peer's
    ``consult_about()`` in-process.
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
        self.calls: list[ImpactRequest] = []

    def consult(self, peer_agent_id: str, request: ImpactRequest) -> ImpactResponse:
        self.calls.append(request)
        if peer_agent_id in self._responses:
            r = self._responses[peer_agent_id]
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


class DirectTransport(PeerTransport):
    """In-process transport that calls a peer agent's ``consult_about()`` directly.

    Used for integration tests and single-process deployments — exercises the
    peer's *real* code without needing a gRPC server.
    """

    def __init__(self, peers: dict[str, "SMEAgentBase"]):
        self._peers = peers
        self.calls: list[ImpactRequest] = []

    def consult(self, peer_agent_id: str, request: ImpactRequest) -> ImpactResponse:
        self.calls.append(request)
        peer = self._peers.get(peer_agent_id)
        if peer is None:
            return ImpactResponse(
                request_id=request.request_id,
                from_agent=peer_agent_id,
                to_agent=request.from_agent,
                verdict="unknown",
                timed_out=True,
                summary=f"DirectTransport: no peer registered as {peer_agent_id}",
            )
        return peer.consult_about(request, depth=request.consultation_depth)


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

    Subclasses should override:
        _investigate(ticket)
        _propose_alternatives(ticket, investigation)   → must return 3
        _select_peers(ticket, investigation)
        _handle_consultation(request, depth)
        _build_background/_build_goals/_build_non_goals/...
    """

    AGENT_NAME: str = ""
    DOMAIN: str = ""
    OWNS: list[str] = []

    # Principal-engineer behaviour knobs
    ALTERNATIVES_REQUIRED: int = 3
    MAX_DELIBERATION_ROUNDS: int = 3
    MAX_CONSULTATION_DEPTH: int = 3

    def __init__(self, transport: PeerTransport | None = None, kg_conn: Any = None):
        """
        Args:
            transport:  peer transport used for outbound consultations.
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
    # OwnTicket workflow — multi-round deliberation
    # ------------------------------------------------------------------

    def own_ticket(self, ticket: Ticket) -> Finding:
        """Drive a ticket this agent's team owns, end to end.

        Runs up to ``MAX_DELIBERATION_ROUNDS`` rounds of peer deliberation,
        refining the design alternatives each round, and only decides
        ``requires_human`` once at the very end.
        """
        logger.info("[%s] OwnTicket: %s (%s)", self.AGENT_NAME, ticket.ticket_id, ticket.title)

        # Step 1 — investigate with own tools
        investigation = self._investigate(ticket)

        # Step 2 — propose the initial solution space (always 3 alternatives)
        alternatives = self._propose_alternatives(ticket, investigation)
        self._assert_alternative_count(alternatives, context="_propose_alternatives")
        logger.info(
            "[%s] proposed %d alternatives: %s",
            self.AGENT_NAME, len(alternatives), [a.name for a in alternatives],
        )

        # Step 3 — who is affected
        peers = self._select_peers(ticket, investigation)
        logger.info("[%s] peers to deliberate with: %s", self.AGENT_NAME, [p for p, _ in peers])

        # Step 4 — deliberate for up to MAX_DELIBERATION_ROUNDS
        deliberation: list[DeliberationRound] = []
        all_responses: list[ImpactResponse] = []
        seen_concerns: set[str] = set()
        converged = False
        rounds_used = 0

        for round_num in range(1, self.MAX_DELIBERATION_ROUNDS + 1):
            rounds_used = round_num
            round_responses: list[ImpactResponse] = []

            for peer_id, codepath in peers:
                request = self._build_impact_request(
                    peer_id, ticket, investigation, codepath,
                    alternatives=alternatives,
                    round_number=round_num,
                    prior_concerns=sorted(seen_concerns),
                )
                response = self._transport.consult(peer_id, request)
                round_responses.append(response)
                all_responses.append(response)

                new_here = [c for c in response.new_concerns if c not in seen_concerns]
                seen_concerns.update(response.new_concerns)

                deliberation.append(DeliberationRound(
                    round_number=round_num,
                    from_agent=self.AGENT_NAME,
                    to_agent=peer_id,
                    question=request.question,
                    response_summary=response.summary,
                    alternatives_discussed=[a.name for a in response.design_alternatives],
                    new_concerns_raised=new_here,
                    verdict=response.verdict,
                ))

                logger.info(
                    "[%s] round %d ← %s: verdict=%s new_concerns=%d alternatives=%d",
                    self.AGENT_NAME, round_num, peer_id, response.verdict,
                    len(new_here), len(response.design_alternatives),
                )

            # Fold peer feedback into the alternative set
            alternatives = self._refine_alternatives(alternatives, round_responses, round_num)

            # Have we converged?
            if self._has_converged(round_responses):
                converged = True
                for rec in deliberation[-len(round_responses):]:
                    rec.converged = True
                logger.info(
                    "[%s] deliberation converged after round %d", self.AGENT_NAME, round_num
                )
                break

        # Step 5 — assemble the design doc; requires_human decided ONLY here
        finding = self._assemble_finding(
            ticket, investigation, all_responses, alternatives,
            deliberation=deliberation, rounds_used=rounds_used, converged=converged,
        )
        logger.info(
            "[%s] OwnTicket complete: rounds=%d converged=%s requires_human=%s",
            self.AGENT_NAME, finding.rounds_used, finding.converged, finding.requires_human,
        )
        return finding

    # ------------------------------------------------------------------
    # ConsultAbout workflow
    # ------------------------------------------------------------------

    def consult_about(self, request: ImpactRequest, depth: int = 0) -> ImpactResponse:
        """Respond to a consultation request from a peer agent.

        Runs own tools, produces alternatives for the part of the problem this
        agent owns, optionally consults a further peer, self-validates
        citations, and returns an ImpactResponse.
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
            "[%s] ConsultAbout from=%s round=%d depth=%d",
            self.AGENT_NAME, request.from_agent, request.round_number, depth,
        )

        response = self._handle_consultation(request, depth)

        # Self-validate citations before sending
        from agents.ownership_validator import validate_citations  # noqa: PLC0415
        validation = validate_citations(response.cited_codepaths, self.OWNS)
        unowned = [r.codepath for r in validation if not r.owned]
        if unowned:
            logger.warning("[%s] unowned citations flagged: %s", self.AGENT_NAME, unowned)
            response.open_questions.extend(
                [f"needs_verification: citation outside OWNS — {p}" for p in unowned]
            )

        return response

    # ------------------------------------------------------------------
    # Deliberation mechanics
    # ------------------------------------------------------------------

    def _assert_alternative_count(
        self, alternatives: list[DesignAlternative], context: str
    ) -> None:
        """Principal engineers present the real solution space, not one option."""
        if len(alternatives) != self.ALTERNATIVES_REQUIRED:
            raise ValueError(
                f"{self.AGENT_NAME}.{context} must return exactly "
                f"{self.ALTERNATIVES_REQUIRED} design alternatives, got {len(alternatives)}"
            )

    def _refine_alternatives(
        self,
        alternatives: list[DesignAlternative],
        responses: list[ImpactResponse],
        round_number: int,
    ) -> list[DesignAlternative]:
        """Fold peer feedback into the alternative set.

        Default behaviour: record who reviewed each alternative, and if a peer
        explicitly backs one by name, mark it recommended and rule out the
        others with the peer's stated reason.  The alternative *count* never
        changes — ruling out is recorded, not deleted, so the final doc shows
        the full solution space that was considered.
        """
        reviewers = [r.from_agent for r in responses if not r.timed_out]
        for alt in alternatives:
            for reviewer in reviewers:
                if reviewer not in alt.reviewed_by:
                    alt.reviewed_by.append(reviewer)

        # Look for an explicit endorsement by name
        for resp in responses:
            if resp.timed_out or not resp.recommendation:
                continue
            rec_text = resp.recommendation.lower()
            for alt in alternatives:
                if alt.name.lower() in rec_text or f"alternative {alt.label.lower()}" in rec_text:
                    alt.recommended = True
                    for other in alternatives:
                        if other is not alt and not other.is_ruled_out:
                            other.rejected_reason = (
                                f"Ruled out in round {round_number} — {resp.from_agent} "
                                f"backed '{alt.name}' instead."
                            )
                    break
        return alternatives

    def _has_converged(self, responses: list[ImpactResponse]) -> bool:
        """Deliberation has converged when every peer says 'agreed' and no
        peer raised a new concern this round.

        A timed-out response never counts as convergence.
        """
        if not responses:
            return True  # nothing to deliberate about
        if any(r.timed_out for r in responses):
            return False
        if not all(r.verdict == "agreed" for r in responses):
            return False
        if any(r.new_concerns for r in responses):
            return False
        return True

    # ------------------------------------------------------------------
    # Hooks for subclasses to override
    # ------------------------------------------------------------------

    def _investigate(self, ticket: Ticket) -> dict[str, Any]:
        """Run this agent's own tools against the ticket."""
        return {
            "agent": self.AGENT_NAME,
            "ticket_id": ticket.ticket_id,
            "tools_available": list(self.get_tools().keys()),
        }

    def _propose_alternatives(
        self, ticket: Ticket, investigation: dict[str, Any]
    ) -> list[DesignAlternative]:
        """Return exactly ALTERNATIVES_REQUIRED design alternatives.

        Subclasses must override this with real, domain-specific options.
        """
        return [
            DesignAlternative(
                label=chr(ord("A") + i),
                name=f"Placeholder alternative {chr(ord('A') + i)}",
                proposed_by=self.AGENT_NAME,
                approach="override _propose_alternatives() in the subclass",
            )
            for i in range(self.ALTERNATIVES_REQUIRED)
        ]

    def _select_peers(
        self, ticket: Ticket, investigation: dict[str, Any]
    ) -> list[tuple[str, str]]:
        """Return [(peer_agent_id, codepath_of_interest)] to deliberate with."""
        return []

    def _build_impact_request(
        self,
        peer_id: str,
        ticket: Ticket,
        investigation: dict[str, Any],
        codepath: str = "",
        alternatives: list[DesignAlternative] | None = None,
        round_number: int = 1,
        prior_concerns: list[str] | None = None,
    ) -> ImpactRequest:
        """Build the ImpactRequest to send to ``peer_id`` for this round."""
        return ImpactRequest.new(
            from_agent=self.AGENT_NAME,
            to_agent=peer_id,
            ticket_id=ticket.ticket_id,
            request_type="design_review",
            context=ticket.description,
            question=(
                f"Round {round_number}: review these {len(alternatives or [])} alternatives "
                f"for the part of this change that touches {codepath or 'your domain'}. "
                "Give me your own alternatives if you see a better path."
            ),
            codepaths_of_interest=[codepath] if codepath else [],
            alternatives_on_table=alternatives or [],
            round_number=round_number,
            prior_concerns=prior_concerns or [],
        )

    def _handle_consultation(
        self, request: ImpactRequest, depth: int
    ) -> ImpactResponse:
        """Produce a response to a ConsultAbout request."""
        return ImpactResponse(
            request_id=request.request_id,
            from_agent=self.AGENT_NAME,
            to_agent=request.from_agent,
            verdict="unknown",
            summary=f"{self.AGENT_NAME} base implementation — override _handle_consultation()",
        )

    # --- 1-pager section builders (override for domain-specific content) ---

    def _build_background(self, ticket: Ticket, investigation: dict[str, Any]) -> str:
        return ticket.description

    def _build_tldr(
        self,
        ticket: Ticket,
        investigation: dict[str, Any],
        recommended: DesignAlternative | None,
    ) -> str:
        if recommended:
            return (
                f"{investigation.get('root_cause', ticket.title)} "
                f"Recommendation: {recommended.name} "
                f"(effort {recommended.effort}, risk {recommended.risk})."
            )
        return investigation.get("root_cause", ticket.title)

    def _build_goals(self, ticket: Ticket, investigation: dict[str, Any]) -> list[str]:
        return investigation.get("goals", [])

    def _build_non_goals(self, ticket: Ticket, investigation: dict[str, Any]) -> list[str]:
        return investigation.get("non_goals", [])

    def _build_teams_involved(
        self, responses: list[ImpactResponse]
    ) -> list[TeamInvolvement]:
        """Default: this agent is owner, every responding peer is a contributor."""
        teams = [TeamInvolvement(
            team=self.AGENT_NAME,
            role="owner",
            owns=self.DOMAIN,
            sign_off_required=False,
            contribution="Diagnosed the problem, drove deliberation, owns the plan.",
        )]
        for resp in responses:
            if resp.timed_out:
                continue
            if any(t.team == resp.from_agent for t in teams):
                continue
            teams.append(TeamInvolvement(
                team=resp.from_agent,
                role="approver" if resp.needs_org_authority else "implementer",
                owns="",
                sign_off_required=resp.needs_org_authority,
                contribution=resp.recommendation or resp.summary,
            ))
        return teams

    # ------------------------------------------------------------------
    # Final assembly — the ONLY place requires_human is decided
    # ------------------------------------------------------------------

    def _assemble_finding(
        self,
        ticket: Ticket,
        investigation: dict[str, Any],
        responses: list[ImpactResponse],
        alternatives: list[DesignAlternative],
        deliberation: list[DeliberationRound] | None = None,
        rounds_used: int = 0,
        converged: bool = False,
    ) -> Finding:
        """Assemble the 1-page design doc from own investigation + deliberation.

        ``requires_human`` is decided here and nowhere else.  It is True only
        when the agents could not settle the matter themselves:
          - a peer flagged that the decision needs organizational authority
            (external process, budget, SLA), or
          - deliberation failed to converge within MAX_DELIBERATION_ROUNDS, or
          - a peer was unreachable so the analysis is genuinely incomplete.
        """
        recommended = next((a for a in alternatives if a.recommended), None)

        # --- human escalation decision, made once, here ---
        human_decision_points: list[str] = []

        for resp in responses:
            if resp.needs_org_authority and resp.org_authority_reason:
                point = f"{resp.from_agent}: {resp.org_authority_reason}"
                if point not in human_decision_points:
                    human_decision_points.append(point)

        if not converged and responses:
            human_decision_points.append(
                f"Agents did not converge within {self.MAX_DELIBERATION_ROUNDS} rounds — "
                "a human needs to break the tie between the remaining alternatives."
            )

        unreachable = [r.from_agent for r in responses if r.timed_out]
        if unreachable:
            human_decision_points.append(
                f"Could not reach {', '.join(sorted(set(unreachable)))} — "
                "analysis is incomplete without their input."
            )

        if recommended is None and responses:
            human_decision_points.append(
                "No single alternative emerged as the clear recommendation."
            )

        requires_human = bool(human_decision_points)

        # --- aggregate testing strategy from every participant ---
        testing_strategy = list(investigation.get("testing_strategy", []))
        for resp in responses:
            for req in resp.test_requirements:
                line = f"[{resp.from_agent}] {req}"
                if line not in testing_strategy:
                    testing_strategy.append(line)

        # --- open questions that genuinely remain ---
        open_questions: list[str] = []
        for resp in responses:
            for q in resp.open_questions:
                if q not in open_questions:
                    open_questions.append(q)

        return Finding(
            ticket_id=ticket.ticket_id,
            owning_agent=self.AGENT_NAME,
            title=ticket.title,
            tldr=self._build_tldr(ticket, investigation, recommended),
            background=self._build_background(ticket, investigation),
            goals=self._build_goals(ticket, investigation),
            non_goals=self._build_non_goals(ticket, investigation),
            design_alternatives=alternatives,
            recommendation=(
                f"{recommended.name} — {recommended.approach.splitlines()[0]}"
                if recommended else "No clear recommendation emerged."
            ),
            testing_strategy=testing_strategy,
            teams_involved=self._build_teams_involved(responses),
            execution_order=investigation.get("execution_order", []),
            risks_and_mitigations=investigation.get("risks_and_mitigations", []),
            rollout_and_rollback=investigation.get("rollout_and_rollback", []),
            success_metrics=investigation.get("success_metrics", []),
            summary=investigation.get("summary", f"{self.AGENT_NAME}: {ticket.title}"),
            root_cause=investigation.get("root_cause", "under investigation"),
            confidence=0.95 if converged else 0.7,
            cited_codepaths=investigation.get("cited_codepaths", []),
            open_questions=open_questions,
            deliberation=deliberation or [],
            rounds_used=rounds_used,
            converged=converged,
            requires_human=requires_human,
            human_decision_points=human_decision_points,
        )
