"""
SME Agent typed protocol — Python dataclasses.

This module defines the message types used for agent-to-agent communication.
It mirrors what a compiled ``proto/sme_agents.proto`` would generate, but
uses pure Python dataclasses so no ``protoc`` installation is required.

Message shapes
--------------
  Ticket          — incoming work item (from router to owning agent)
  Finding         — result of OwnTicket() (assembled by the owning agent)
  ImpactRequest   — sent by any agent to a peer it wants to consult
  ImpactResponse  — reply from the consulted agent

Serialization
-------------
  Every type has ``.to_dict()`` and ``.from_dict(cls, d)`` for JSON I/O.
  Confidence is always a float in [0.0, 1.0].
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Ticket
# ---------------------------------------------------------------------------

@dataclass
class Ticket:
    """A work item assigned to a specific team's SME agent."""

    ticket_id: str
    team: str                        # owning team — the router reads this field
    title: str
    description: str
    priority: str = "medium"         # "low" | "medium" | "high" | "critical"
    labels: list[str] = field(default_factory=list)
    source: str = "manual"           # "jira" | "github" | "manual"
    source_url: str = ""

    @classmethod
    def new(cls, team: str, title: str, description: str, **kwargs) -> "Ticket":
        """Factory that auto-generates a ticket_id."""
        return cls(
            ticket_id=str(uuid.uuid4()),
            team=team,
            title=title,
            description=description,
            **kwargs,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticket_id": self.ticket_id,
            "team": self.team,
            "title": self.title,
            "description": self.description,
            "priority": self.priority,
            "labels": self.labels,
            "source": self.source,
            "source_url": self.source_url,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Ticket":
        return cls(
            ticket_id=d.get("ticket_id", str(uuid.uuid4())),
            team=d["team"],
            title=d.get("title", ""),
            description=d.get("description", ""),
            priority=d.get("priority", "medium"),
            labels=d.get("labels", []),
            source=d.get("source", "manual"),
            source_url=d.get("source_url", ""),
        )


# ---------------------------------------------------------------------------
# Finding  (result of OwnTicket)
# ---------------------------------------------------------------------------

@dataclass
class Finding:
    """The assembled result produced by the owning agent after OwnTicket()."""

    ticket_id: str
    owning_agent: str
    summary: str
    root_cause: str
    confidence: float = 1.0          # 0.0–1.0

    # Structured execution plan
    execution_order: list[str] = field(default_factory=list)
    approvals_needed: list[str] = field(default_factory=list)
    requires_human: bool = False

    # Citations — each entry is a file/path this finding depends on
    cited_codepaths: list[str] = field(default_factory=list)

    # Open questions that could not be answered
    open_questions: list[str] = field(default_factory=list)

    # Peer consultations this agent triggered
    consultations: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticket_id": self.ticket_id,
            "owning_agent": self.owning_agent,
            "summary": self.summary,
            "root_cause": self.root_cause,
            "confidence": self.confidence,
            "execution_order": self.execution_order,
            "approvals_needed": self.approvals_needed,
            "requires_human": self.requires_human,
            "cited_codepaths": self.cited_codepaths,
            "open_questions": self.open_questions,
            "consultations": self.consultations,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Finding":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# ImpactRequest
# ---------------------------------------------------------------------------

@dataclass
class ImpactRequest:
    """Sent by an agent to a peer when it needs domain-specific input."""

    request_id: str
    from_agent: str
    to_agent: str
    ticket_id: str
    request_type: str                # 'impact_analysis' | 'approval_check' | 'protocol_review'

    # What specifically is being asked
    context: str = ""
    codepaths_of_interest: list[str] = field(default_factory=list)
    proposed_change: str = ""

    # Metadata
    consultation_depth: int = 0      # incremented with each hop

    @classmethod
    def new(
        cls,
        from_agent: str,
        to_agent: str,
        ticket_id: str,
        request_type: str,
        **kwargs,
    ) -> "ImpactRequest":
        return cls(
            request_id=str(uuid.uuid4()),
            from_agent=from_agent,
            to_agent=to_agent,
            ticket_id=ticket_id,
            request_type=request_type,
            **kwargs,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "ticket_id": self.ticket_id,
            "request_type": self.request_type,
            "context": self.context,
            "codepaths_of_interest": self.codepaths_of_interest,
            "proposed_change": self.proposed_change,
            "consultation_depth": self.consultation_depth,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ImpactRequest":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# ImpactResponse
# ---------------------------------------------------------------------------

@dataclass
class ImpactResponse:
    """Returned by a consulted agent in response to an ImpactRequest."""

    request_id: str                  # echoes ImpactRequest.request_id
    from_agent: str                  # the agent answering
    to_agent: str                    # the agent that asked

    verdict: str = "unknown"         # 'approved' | 'blocked' | 'needs_changes' | 'unknown'
    confidence: float = 1.0

    # Substance of the response
    summary: str = ""
    cited_codepaths: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    follow_up_consultations: list[str] = field(default_factory=list)

    # If True, the chain timed out at or above this hop
    timed_out: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "verdict": self.verdict,
            "confidence": self.confidence,
            "summary": self.summary,
            "cited_codepaths": self.cited_codepaths,
            "open_questions": self.open_questions,
            "follow_up_consultations": self.follow_up_consultations,
            "timed_out": self.timed_out,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ImpactResponse":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
