"""
1-page engineering design doc renderer.

Takes the ``Finding`` produced by an agent's ``own_ticket()`` and renders it
as the engineering document a Principal Engineer would circulate for review.

Section order (fixed — this is the house style):

    Title + metadata header
    TL;DR
    Background
    Goals / Non-Goals
    Design Alternatives  (all three, with the recommended one marked)
    Recommendation
    Testing Strategy
    Teams Involved
    Execution Plan
    Risks & Mitigations
    Rollout & Rollback
    Success Metrics
    Decisions Requiring a Human      ← empty if the agents settled everything
    Appendix: Deliberation Record    ← the audit trail of the agent discussion

Usage::

    from agents.design_doc import render_one_pager
    print(render_one_pager(finding))
"""

from __future__ import annotations

from proto.sme_agents import DesignAlternative, Finding

# Rendered when a section has no content, so reviewers can tell "nothing to
# say here" apart from "we forgot this section".
_EMPTY = "_None._"


def _bullets(items: list[str], empty: str = _EMPTY) -> str:
    if not items:
        return empty
    return "\n".join(f"- {item}" for item in items)


def _numbered(items: list[str], empty: str = _EMPTY) -> str:
    if not items:
        return empty
    return "\n".join(f"{i}. {item}" for i, item in enumerate(items, 1))


def _render_alternative(alt: DesignAlternative) -> str:
    """Render one design alternative in full detail."""
    if alt.recommended:
        marker = " — **RECOMMENDED**"
    elif alt.is_ruled_out:
        marker = " — _ruled out_"
    else:
        marker = ""

    lines = [
        f"### Alternative {alt.label}: {alt.name}{marker}",
        "",
        f"| | |",
        f"|---|---|",
        f"| **Proposed by** | `{alt.proposed_by}` |",
        f"| **Effort** | {alt.effort} |",
        f"| **Risk** | {alt.risk} |",
        f"| **Blast radius** | {', '.join(alt.blast_radius) if alt.blast_radius else '—'} |",
        f"| **Reviewed by** | {', '.join(f'`{r}`' for r in alt.reviewed_by) if alt.reviewed_by else '—'} |",
        "",
        "**Approach**",
        "",
        alt.approach.strip(),
        "",
        "**Pros**",
        "",
        _bullets(alt.pros),
        "",
        "**Cons**",
        "",
        _bullets(alt.cons),
    ]

    if alt.is_ruled_out:
        lines += ["", f"**Why ruled out:** {alt.rejected_reason}"]

    return "\n".join(lines)


def _render_teams_table(finding: Finding) -> str:
    if not finding.teams_involved:
        return _EMPTY
    rows = [
        "| Team | Role | Owns | Sign-off | Contribution |",
        "|---|---|---|---|---|",
    ]
    for t in finding.teams_involved:
        sign_off = "**Yes**" if t.sign_off_required else "No"
        owns = t.owns or "—"
        contribution = t.contribution.replace("\n", " ").strip() or "—"
        rows.append(
            f"| `{t.team}` | {t.role} | {owns} | {sign_off} | {contribution} |"
        )
    return "\n".join(rows)


def _render_deliberation(finding: Finding) -> str:
    if not finding.deliberation:
        return "_No peer deliberation was required._"

    rows = [
        "| Round | From | To | Verdict | New concerns raised | Alternatives discussed |",
        "|---|---|---|---|---|---|",
    ]
    for r in finding.deliberation:
        concerns = "; ".join(r.new_concerns_raised) if r.new_concerns_raised else "—"
        alts = "; ".join(r.alternatives_discussed) if r.alternatives_discussed else "—"
        rows.append(
            f"| {r.round_number} | `{r.from_agent}` | `{r.to_agent}` | "
            f"{r.verdict} | {concerns} | {alts} |"
        )

    detail = ["", "**Questions asked, round by round**", ""]
    for r in finding.deliberation:
        detail.append(f"- **Round {r.round_number}, `{r.from_agent}` → `{r.to_agent}`:** {r.question}")
        detail.append(f"  - Response: {r.response_summary}")

    return "\n".join(rows) + "\n" + "\n".join(detail)


def render_one_pager(finding: Finding) -> str:
    """Render ``finding`` as a 1-page engineering design doc in Markdown."""

    status = "Converged — ready for review" if finding.converged else "Deliberation incomplete"
    human_gate = "Yes" if finding.requires_human else "No — agents settled it"

    header = [
        f"# {finding.title or finding.summary}",
        "",
        "| | |",
        "|---|---|",
        f"| **Owner** | `{finding.owning_agent}` |",
        f"| **Ticket** | `{finding.ticket_id}` |",
        f"| **Status** | {status} |",
        f"| **Deliberation rounds** | {finding.rounds_used} |",
        f"| **Confidence** | {finding.confidence:.0%} |",
        f"| **Needs a human decision** | {human_gate} |",
    ]

    body = [
        "",
        "---",
        "",
        "## TL;DR",
        "",
        finding.tldr.strip() or _EMPTY,
        "",
        "## Background",
        "",
        finding.background.strip() or _EMPTY,
        "",
        "## Goals",
        "",
        "**In scope**",
        "",
        _bullets(finding.goals),
        "",
        "**Not in scope**",
        "",
        _bullets(finding.non_goals),
        "",
        "## Design Alternatives",
        "",
    ]

    for alt in finding.design_alternatives:
        body.append(_render_alternative(alt))
        body.append("")

    body += [
        "## Recommendation",
        "",
        finding.recommendation.strip() or _EMPTY,
        "",
        "## Testing Strategy",
        "",
        _bullets(finding.testing_strategy),
        "",
        "## Teams Involved",
        "",
        _render_teams_table(finding),
        "",
        "## Execution Plan",
        "",
        _numbered(finding.execution_order),
        "",
        "## Risks & Mitigations",
        "",
        _bullets(finding.risks_and_mitigations),
        "",
        "## Rollout & Rollback",
        "",
        _bullets(finding.rollout_and_rollback),
        "",
        "## Success Metrics",
        "",
        _bullets(finding.success_metrics),
        "",
        "## Decisions Requiring a Human",
        "",
        _bullets(
            finding.human_decision_points,
            empty="_None — the agents reached a decision without escalation._",
        ),
    ]

    if finding.open_questions:
        body += ["", "## Open Questions", "", _bullets(finding.open_questions)]

    body += [
        "",
        "---",
        "",
        "## Appendix: Deliberation Record",
        "",
        _render_deliberation(finding),
        "",
        "### Codepaths cited by the owning agent",
        "",
        _bullets([f"`{cp}`" for cp in finding.cited_codepaths]),
    ]

    return "\n".join(header + body).rstrip() + "\n"
