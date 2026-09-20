"""
Deliberation tracing — the observable record of how a ticket was worked.

Why this exists
---------------
The ``Finding`` is the *product*: a 1-page design doc. It deliberately hides
the process. But the process is the interesting part of this system — which
teams were discovered, which were skipped and why, who pushed back on whom,
and where the alternatives changed shape between rounds.

A ``Tracer`` is a shared, append-only timeline that every agent in a network
writes to. One is created per ticket and attached to all agents, so a peer's
events land in the same ordered stream as the owner's. Serialized to JSON it
is exactly what the web UI replays.

Tracing is opt-in and free when off: agents hold a :class:`NullTracer` by
default, whose ``emit()`` does nothing, so the agent network behaves
identically whether or not anybody is watching.

Phases
------
The timeline is divided into the phases a ticket moves through. The UI uses
these to drive its stage transitions, so the ordering in :data:`PHASES` is
meaningful.

    intake         the ticket arrives and is routed to an owning team
    investigation  the owning agent runs its own tools
    discovery      it declares consequences and resolves them to teams
    alternatives   it proposes its initial design alternatives
    deliberation   rounds of typed request/response with peers
    synthesis      peer feedback folded back into the alternatives
    decision       convergence and the single requires_human call
    artifact       the finished 1-pager

Usage::

    tracer = Tracer(ticket_id="KAFKA-18231")
    attach_tracer(tracer, [owner, *peers])
    finding = owner.own_ticket(ticket)
    Path("trace.json").write_text(json.dumps(tracer.to_dict(), indent=2))
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Iterable

PHASES: tuple[str, ...] = (
    "intake",
    "investigation",
    "discovery",
    "alternatives",
    "deliberation",
    "synthesis",
    "decision",
    "artifact",
)

# Event kinds, for the UI to pick an icon and a shape per row.
KINDS: tuple[str, ...] = (
    "ticket",          # a ticket entered the system
    "tool_call",       # an agent ran one of its own tools
    "finding",         # an agent concluded something from its tools
    "signal",          # a declared consequence of a proposed change
    "peer_decision",   # consulted / skipped, with the reason
    "alternative",     # a design alternative proposed or revised
    "request",         # an outbound ImpactRequest
    "response",        # an inbound ImpactResponse
    "concern",         # a new concern raised by a peer
    "convergence",     # the deliberation loop settled, or did not
    "escalation",      # a human decision point recorded
    "artifact",        # the finished design doc
)


@dataclass
class TraceEvent:
    """One entry in the timeline."""

    seq: int
    t_ms: int                  # milliseconds since the tracer was created
    phase: str
    agent: str
    kind: str
    title: str
    detail: str = ""
    round_number: int = 0      # 0 outside the deliberation loop
    to_agent: str = ""         # set on request/response events
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "t_ms": self.t_ms,
            "phase": self.phase,
            "agent": self.agent,
            "kind": self.kind,
            "title": self.title,
            "detail": self.detail,
            "round_number": self.round_number,
            "to_agent": self.to_agent,
            "data": self.data,
        }


class Tracer:
    """A shared, append-only timeline for one ticket."""

    def __init__(self, ticket_id: str = "", title: str = ""):
        self.ticket_id = ticket_id
        self.title = title
        self.events: list[TraceEvent] = []
        self._t0 = time.monotonic()
        self._listeners: list[Callable[[TraceEvent], None]] = []

    def on_event(self, fn: Callable[[TraceEvent], None]) -> None:
        """Register a callback invoked after every emit (used by the UI stream)."""
        self._listeners.append(fn)

    # -- writing --------------------------------------------------------

    def emit(
        self,
        *,
        phase: str,
        agent: str,
        kind: str,
        title: str,
        detail: str = "",
        round_number: int = 0,
        to_agent: str = "",
        **data: Any,
    ) -> TraceEvent:
        """Append an event and return it."""
        event = TraceEvent(
            seq=len(self.events),
            t_ms=int((time.monotonic() - self._t0) * 1000),
            phase=phase,
            agent=agent,
            kind=kind,
            title=title,
            detail=detail,
            round_number=round_number,
            to_agent=to_agent,
            data=data,
        )
        self.events.append(event)
        for fn in self._listeners:
            fn(event)
        return event

    # -- reading --------------------------------------------------------

    def by_phase(self, phase: str) -> list[TraceEvent]:
        return [e for e in self.events if e.phase == phase]

    def phases_reached(self) -> list[str]:
        """Phases that actually produced events, in canonical order."""
        seen = {e.phase for e in self.events}
        return [p for p in PHASES if p in seen]

    def agents_seen(self) -> list[str]:
        """Every agent that appears in the timeline, in first-appearance order."""
        out: list[str] = []
        for e in self.events:
            for name in (e.agent, e.to_agent):
                if name and name not in out:
                    out.append(name)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticket_id": self.ticket_id,
            "title": self.title,
            "phases": list(PHASES),
            "phases_reached": self.phases_reached(),
            "agents": self.agents_seen(),
            "event_count": len(self.events),
            "duration_ms": self.events[-1].t_ms if self.events else 0,
            "events": [e.to_dict() for e in self.events],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)


class NullTracer(Tracer):
    """The default: accepts events and discards them.

    Subclassing ``Tracer`` rather than duck-typing keeps the attribute access
    in agent code (``self._tracer.emit(...)``) unconditional and total.
    """

    def emit(self, **kwargs: Any) -> TraceEvent:  # type: ignore[override]
        return TraceEvent(seq=-1, t_ms=0, phase="", agent="", kind="", title="")


def attach_tracer(tracer: Tracer, agents: Iterable[Any]) -> Tracer:
    """Attach one tracer to every agent in a network, and return it.

    Every agent must share the *same* tracer instance, otherwise a peer's
    reasoning lands in a timeline nobody reads.
    """
    for agent in agents:
        agent.attach_tracer(tracer)
    return tracer
