"""
SME Agent typed protocol — Python dataclasses.

This module defines the message types used for agent-to-agent communication.
It mirrors what a compiled ``proto/sme_agents.proto`` would generate, but
uses pure Python dataclasses so no ``protoc`` installation is required.

Message shapes
--------------
  Ticket              — incoming work item (from router to owning agent)
  DesignAlternative   — one fully-argued design option (agents always produce 3)
  DeliberationRound   — a record of one round of peer-to-peer discussion
  ImpactRequest       — sent by any agent to a peer it wants to consult
  ImpactResponse      — reply from the consulted agent
  Finding             — final result of OwnTicket(), rendered as a 1-page design doc

Design philosophy
-----------------
Agents behave like Principal Engineers: they do not return "sounds fine".
Every consultation response must carry **three** fully-reasoned design
alternatives with explicit tradeoffs, effort, risk, and blast radius.
Agents deliberate across multiple rounds and converge on a recommendation
themselves.  ``requires_human`` is set exactly once, at the very end, and
only for decisions that genuinely need organizational authority.
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
# DesignAlternative
# ---------------------------------------------------------------------------

EFFORT_VALUES = ("S", "M", "L", "XL")
RISK_VALUES = ("low", "medium", "high")


@dataclass
class DesignAlternative:
    """One fully-argued design option.

    Every agent must produce exactly ``ALTERNATIVES_REQUIRED`` (3) of these
    for any non-trivial question.  A principal engineer does not present one
    option — they present the real solution space and argue for one.
    """

    label: str                       # "A" | "B" | "C"
    name: str                        # short descriptive title
    proposed_by: str                 # agent id that authored this alternative

    # The actual engineering content
    approach: str                    # how it works, technically, in detail
    pros: list[str] = field(default_factory=list)
    cons: list[str] = field(default_factory=list)

    # Principal-level estimation
    effort: str = "M"                # S | M | L | XL
    risk: str = "medium"             # low | medium | high
    blast_radius: list[str] = field(default_factory=list)  # teams/systems touched

    # Outcome of deliberation
    recommended: bool = False
    rejected_reason: str = ""        # non-empty once ruled out, with the *why*
    # Which agent(s) validated or challenged this alternative
    reviewed_by: list[str] = field(default_factory=list)

    def __post_init__(self):
        if self.effort not in EFFORT_VALUES:
            raise ValueError(f"effort must be one of {EFFORT_VALUES}, got {self.effort!r}")
        if self.risk not in RISK_VALUES:
            raise ValueError(f"risk must be one of {RISK_VALUES}, got {self.risk!r}")

    @property
    def is_ruled_out(self) -> bool:
        return bool(self.rejected_reason)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "name": self.name,
            "proposed_by": self.proposed_by,
            "approach": self.approach,
            "pros": self.pros,
            "cons": self.cons,
            "effort": self.effort,
            "risk": self.risk,
            "blast_radius": self.blast_radius,
            "recommended": self.recommended,
            "rejected_reason": self.rejected_reason,
            "reviewed_by": self.reviewed_by,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DesignAlternative":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# DeliberationRound
# ---------------------------------------------------------------------------

@dataclass
class DeliberationRound:
    """Record of one round of peer-to-peer discussion.

    The full list of these becomes the "Deliberation Record" appendix of the
    final design doc — it is the audit trail showing that the agents actually
    argued the problem rather than rubber-stamping it.
    """

    round_number: int
    from_agent: str
    to_agent: str
    question: str
    response_summary: str
    alternatives_discussed: list[str] = field(default_factory=list)
    new_concerns_raised: list[str] = field(default_factory=list)
    verdict: str = "unknown"
    converged: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "round_number": self.round_number,
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "question": self.question,
            "response_summary": self.response_summary,
            "alternatives_discussed": self.alternatives_discussed,
            "new_concerns_raised": self.new_concerns_raised,
            "verdict": self.verdict,
            "converged": self.converged,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DeliberationRound":
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
    request_type: str                # 'impact_analysis' | 'design_review' | 'protocol_review'

    # What specifically is being asked
    context: str = ""
    question: str = ""               # the precise question this round is asking
    codepaths_of_interest: list[str] = field(default_factory=list)
    proposed_change: str = ""

    # Alternatives the asking agent has on the table (peer critiques these
    # and adds its own)
    alternatives_on_table: list[DesignAlternative] = field(default_factory=list)

    # Deliberation metadata
    round_number: int = 1
    consultation_depth: int = 0       # incremented with each hop
    # Concerns already raised in earlier rounds — lets the peer avoid
    # repeating itself and lets convergence detection work
    prior_concerns: list[str] = field(default_factory=list)

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
            "question": self.question,
            "codepaths_of_interest": self.codepaths_of_interest,
            "proposed_change": self.proposed_change,
            "alternatives_on_table": [a.to_dict() for a in self.alternatives_on_table],
            "round_number": self.round_number,
            "consultation_depth": self.consultation_depth,
            "prior_concerns": self.prior_concerns,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ImpactRequest":
        d = dict(d)
        if "alternatives_on_table" in d:
            d["alternatives_on_table"] = [
                DesignAlternative.from_dict(a) if isinstance(a, dict) else a
                for a in d["alternatives_on_table"]
            ]
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# ImpactResponse
# ---------------------------------------------------------------------------

@dataclass
class ImpactResponse:
    """Returned by a consulted agent in response to an ImpactRequest.

    A principal-engineer-grade response always carries design alternatives —
    never a bare yes/no.
    """

    request_id: str                  # echoes ImpactRequest.request_id
    from_agent: str                  # the agent answering
    to_agent: str                    # the agent that asked

    # 'agreed' means this agent considers the question settled from its side.
    # Convergence requires every peer to reach 'agreed'.
    verdict: str = "unknown"         # 'agreed' | 'needs_changes' | 'blocked' | 'unknown'
    confidence: float = 1.0

    # Substance of the response
    summary: str = ""
    principal_review: str = ""       # the deep engineering rationale
    design_alternatives: list[DesignAlternative] = field(default_factory=list)
    recommendation: str = ""         # which alternative this agent backs, and why

    cited_codepaths: list[str] = field(default_factory=list)
    # Concerns this agent is raising for the first time in this round
    new_concerns: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    follow_up_consultations: list[str] = field(default_factory=list)

    # Testing strategy this agent owns for its part of the change
    test_requirements: list[str] = field(default_factory=list)

    # True only if this decision genuinely needs a human with org authority
    # (external process, budget, SLA change) — NOT for ordinary uncertainty.
    needs_org_authority: bool = False
    org_authority_reason: str = ""

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
            "principal_review": self.principal_review,
            "design_alternatives": [a.to_dict() for a in self.design_alternatives],
            "recommendation": self.recommendation,
            "cited_codepaths": self.cited_codepaths,
            "new_concerns": self.new_concerns,
            "open_questions": self.open_questions,
            "follow_up_consultations": self.follow_up_consultations,
            "test_requirements": self.test_requirements,
            "needs_org_authority": self.needs_org_authority,
            "org_authority_reason": self.org_authority_reason,
            "timed_out": self.timed_out,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ImpactResponse":
        d = dict(d)
        if "design_alternatives" in d:
            d["design_alternatives"] = [
                DesignAlternative.from_dict(a) if isinstance(a, dict) else a
                for a in d["design_alternatives"]
            ]
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# TeamInvolvement
# ---------------------------------------------------------------------------

@dataclass
class TeamInvolvement:
    """One row of the "Teams Involved" table in the final design doc."""

    team: str
    role: str                        # 'owner' | 'implementer' | 'approver' | 'notified'
    owns: str                        # what this team is authoritative over
    sign_off_required: bool = False
    contribution: str = ""           # what they actually contributed in deliberation

    def to_dict(self) -> dict[str, Any]:
        return {
            "team": self.team,
            "role": self.role,
            "owns": self.owns,
            "sign_off_required": self.sign_off_required,
            "contribution": self.contribution,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TeamInvolvement":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# Finding  (result of OwnTicket — renders as the 1-page design doc)
# ---------------------------------------------------------------------------

@dataclass
class Finding:
    """The assembled result produced by the owning agent after OwnTicket().

    This carries everything needed to render the 1-page engineering design
    doc: background, TL;DR, goals/non-goals, the three design alternatives
    with the recommendation, testing strategy, and teams involved.
    """

    ticket_id: str
    owning_agent: str

    # --- 1-pager content ---
    title: str = ""
    tldr: str = ""
    background: str = ""
    goals: list[str] = field(default_factory=list)          # in scope
    non_goals: list[str] = field(default_factory=list)      # explicitly not in scope

    design_alternatives: list[DesignAlternative] = field(default_factory=list)
    recommendation: str = ""                                 # which and why

    testing_strategy: list[str] = field(default_factory=list)
    teams_involved: list[TeamInvolvement] = field(default_factory=list)
    execution_order: list[str] = field(default_factory=list)
    risks_and_mitigations: list[str] = field(default_factory=list)
    rollout_and_rollback: list[str] = field(default_factory=list)
    success_metrics: list[str] = field(default_factory=list)

    # --- diagnosis ---
    summary: str = ""
    root_cause: str = ""
    confidence: float = 1.0

    cited_codepaths: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)

    # --- deliberation audit trail ---
    deliberation: list[DeliberationRound] = field(default_factory=list)
    rounds_used: int = 0
    converged: bool = False

    # --- human escalation: decided exactly once, at the very end ---
    requires_human: bool = False
    human_decision_points: list[str] = field(default_factory=list)

    @property
    def recommended_alternative(self) -> DesignAlternative | None:
        """Return the alternative the agents converged on, if any."""
        for alt in self.design_alternatives:
            if alt.recommended:
                return alt
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticket_id": self.ticket_id,
            "owning_agent": self.owning_agent,
            "title": self.title,
            "tldr": self.tldr,
            "background": self.background,
            "goals": self.goals,
            "non_goals": self.non_goals,
            "design_alternatives": [a.to_dict() for a in self.design_alternatives],
            "recommendation": self.recommendation,
            "testing_strategy": self.testing_strategy,
            "teams_involved": [t.to_dict() for t in self.teams_involved],
            "execution_order": self.execution_order,
            "risks_and_mitigations": self.risks_and_mitigations,
            "rollout_and_rollback": self.rollout_and_rollback,
            "success_metrics": self.success_metrics,
            "summary": self.summary,
            "root_cause": self.root_cause,
            "confidence": self.confidence,
            "cited_codepaths": self.cited_codepaths,
            "open_questions": self.open_questions,
            "deliberation": [d.to_dict() for d in self.deliberation],
            "rounds_used": self.rounds_used,
            "converged": self.converged,
            "requires_human": self.requires_human,
            "human_decision_points": self.human_decision_points,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Finding":
        d = dict(d)
        if "design_alternatives" in d:
            d["design_alternatives"] = [
                DesignAlternative.from_dict(a) if isinstance(a, dict) else a
                for a in d["design_alternatives"]
            ]
        if "teams_involved" in d:
            d["teams_involved"] = [
                TeamInvolvement.from_dict(t) if isinstance(t, dict) else t
                for t in d["teams_involved"]
            ]
        if "deliberation" in d:
            d["deliberation"] = [
                DeliberationRound.from_dict(r) if isinstance(r, dict) else r
                for r in d["deliberation"]
            ]
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
