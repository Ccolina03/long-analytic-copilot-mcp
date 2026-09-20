"""
1-pager design doc renderer tests.

Verifies every required section is present, that all three alternatives are
rendered with their tradeoffs, and that the human-escalation section says the
right thing in both the escalated and non-escalated cases.
"""

import pytest

from agents.base_agent import DirectTransport, NullTransport
from agents.group_coordinator_agent import GroupCoordinatorAgent
from agents.kafka_broker_agent import KafkaBrokerAgent
from agents.design_doc import render_one_pager
from agents.mirrormaker_agent import MirrorMakerAgent
from agents.kafka_clients_agent import KafkaClientsAgent
from proto.sme_agents import (
    DeliberationRound,
    DesignAlternative,
    Finding,
    TeamInvolvement,
    Ticket,
)

REQUIRED_SECTIONS = [
    "## TL;DR",
    "## Background",
    "## Goals",
    "**In scope**",
    "**Not in scope**",
    "## Design Alternatives",
    "## Recommendation",
    "## Testing Strategy",
    "## Teams Involved",
    "## Execution Plan",
    "## Risks & Mitigations",
    "## Rollout & Rollback",
    "## Success Metrics",
    "## Decisions Requiring a Human",
    "## Appendix: Deliberation Record",
]


@pytest.fixture(scope="module")
def real_doc():
    """Render the doc from a real full-network deliberation."""
    clients = KafkaClientsAgent()
    broker = KafkaBrokerAgent()
    coordinator = GroupCoordinatorAgent(
        transport=DirectTransport({"kafka-clients": clients})
    )
    mirrormaker = MirrorMakerAgent(transport=DirectTransport({
        "group-coordinator": coordinator,
        "kafka-broker": broker,
        "kafka-clients": clients,
    }))
    ticket = Ticket.new(
        team="mirrormaker",
        title="MirrorCheckpointConnector group discovery is slow — 8-12s p99 on clusters with >10k consumer groups",
        description="Checkpoint group discovery p99 = 11.4s on a 50k-group cluster.",
        priority="high",
    )
    finding = mirrormaker.own_ticket(ticket)
    return finding, render_one_pager(finding)


class TestRequiredSections:
    def test_all_required_sections_present(self, real_doc):
        _, doc = real_doc
        for section in REQUIRED_SECTIONS:
            assert section in doc, f"missing section: {section}"

    def test_header_has_owner_and_status(self, real_doc):
        _, doc = real_doc
        assert "**Owner**" in doc
        assert "mirrormaker" in doc
        assert "**Status**" in doc

    def test_header_reports_deliberation_rounds(self, real_doc):
        finding, doc = real_doc
        assert "**Deliberation rounds**" in doc
        assert str(finding.rounds_used) in doc

    def test_title_is_rendered_as_h1(self, real_doc):
        _, doc = real_doc
        assert doc.startswith("# ")


class TestDesignAlternativesRendering:
    def test_all_three_alternatives_rendered(self, real_doc):
        _, doc = real_doc
        assert "### Alternative A:" in doc
        assert "### Alternative B:" in doc
        assert "### Alternative C:" in doc

    def test_recommended_alternative_is_marked(self, real_doc):
        _, doc = real_doc
        assert "**RECOMMENDED**" in doc

    def test_ruled_out_alternatives_explain_why(self, real_doc):
        _, doc = real_doc
        assert "_ruled out_" in doc
        assert "**Why ruled out:**" in doc

    def test_each_alternative_shows_effort_risk_blast_radius(self, real_doc):
        _, doc = real_doc
        assert doc.count("**Effort**") == 3
        assert doc.count("**Risk**") == 3
        assert doc.count("**Blast radius**") == 3

    def test_each_alternative_shows_pros_and_cons(self, real_doc):
        _, doc = real_doc
        assert doc.count("**Pros**") == 3
        assert doc.count("**Cons**") == 3

    def test_alternatives_show_who_reviewed_them(self, real_doc):
        _, doc = real_doc
        assert "**Reviewed by**" in doc


class TestTeamsTable:
    def test_teams_table_has_headers(self, real_doc):
        _, doc = real_doc
        assert "| Team | Role | Owns | Sign-off | Contribution |" in doc

    def test_all_participating_teams_appear(self, real_doc):
        _, doc = real_doc
        for team in ("mirrormaker", "group-coordinator", "kafka-clients", "kafka-broker"):
            assert f"`{team}`" in doc

    def test_oss_kafka_is_marked_as_needing_sign_off(self, real_doc):
        finding, doc = real_doc
        clients = next(t for t in finding.teams_involved if t.team == "kafka-clients")
        assert clients.sign_off_required is True
        assert "**Yes**" in doc


class TestDeliberationRecord:
    def test_deliberation_table_rendered(self, real_doc):
        _, doc = real_doc
        assert "| Round | From | To | Verdict |" in doc

    def test_questions_are_included_round_by_round(self, real_doc):
        _, doc = real_doc
        assert "**Questions asked, round by round**" in doc
        assert "Round 1, `mirrormaker` → `group-coordinator`" in doc

    def test_cited_codepaths_appendix(self, real_doc):
        _, doc = real_doc
        assert "### Codepaths cited by the owning agent" in doc
        assert "MirrorCheckpointConnector.java" in doc


class TestHumanEscalationSection:
    def test_escalation_section_names_the_pmc_vote(self, real_doc):
        _, doc = real_doc
        idx = doc.index("## Decisions Requiring a Human")
        section = doc[idx:idx + 800].lower()
        assert "pmc" in section or "vote" in section

    def test_no_escalation_renders_the_settled_message(self):
        finding = Finding(
            ticket_id="t",
            owning_agent="solo",
            title="Settled ticket",
            converged=True,
            requires_human=False,
            human_decision_points=[],
            design_alternatives=[
                DesignAlternative(
                    label="A", name="The way", proposed_by="solo",
                    approach="do it", recommended=True,
                )
            ],
        )
        doc = render_one_pager(finding)
        assert "the agents reached a decision without escalation" in doc.lower()

    def test_needs_a_human_flag_in_header_reflects_state(self):
        settled = Finding(
            ticket_id="t", owning_agent="solo", title="x",
            converged=True, requires_human=False,
        )
        assert "No — agents settled it" in render_one_pager(settled)

        escalated = Finding(
            ticket_id="t", owning_agent="solo", title="x",
            converged=True, requires_human=True,
            human_decision_points=["needs a VP"],
        )
        assert "| **Needs a human decision** | Yes |" in render_one_pager(escalated)


class TestEmptySectionHandling:
    def test_empty_sections_render_a_placeholder_not_nothing(self):
        """A reviewer must be able to tell 'nothing to say' from 'we forgot'."""
        finding = Finding(ticket_id="t", owning_agent="a", title="Bare finding")
        doc = render_one_pager(finding)
        for section in REQUIRED_SECTIONS:
            assert section in doc
        assert "_None._" in doc

    def test_no_deliberation_renders_explicit_message(self):
        finding = Finding(ticket_id="t", owning_agent="a", title="Solo")
        doc = render_one_pager(finding)
        assert "No peer deliberation was required" in doc


class TestDocIsReadableLength:
    def test_doc_is_substantive_but_not_unbounded(self, real_doc):
        """It's a '1-pager' in the engineering sense: dense, not endless.

        The upper bound scales with the number of consulting teams, since each
        one contributes alternatives, a review, and test requirements. Four
        teams lands around 32k; the cap leaves headroom for a fifth without
        tolerating unbounded growth.
        """
        _, doc = real_doc
        assert len(doc) > 4000, "doc is too thin to be useful"
        assert len(doc) < 40000, "doc has ballooned past a reviewable size"

    def test_doc_ends_with_newline(self, real_doc):
        _, doc = real_doc
        assert doc.endswith("\n")
