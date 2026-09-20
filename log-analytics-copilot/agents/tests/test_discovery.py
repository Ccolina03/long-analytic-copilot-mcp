"""Peer self-discovery tests.

The property that matters: an agent names *consequences*, not teams, and
the directory decides who is consulted. Teams whose concerns do not
intersect the signals are skipped with a recorded reason — including
teams that have no agent deployed.
"""

from agents.discovery import (
    CONCERNS,
    DIRECTORY,
    discover_peers,
)
from agents.mirrormaker_agent import MirrorMakerAgent
from agents.network import build_network
from proto.sme_agents import ImpactSignal, Ticket


class TestDirectoryIntegrity:
    def test_every_answered_concern_is_in_the_taxonomy(self):
        unknown = {
            c for card in DIRECTORY for c in card.answers if c not in CONCERNS
        }
        assert unknown == set()

    def test_every_concern_is_claimed_by_someone(self):
        claimed = {c for card in DIRECTORY for c in card.answers}
        orphaned = set(CONCERNS) - claimed
        assert orphaned == set(), f"concerns nobody owns: {orphaned}"

    def test_agent_ids_are_unique(self):
        ids = [c.agent_id for c in DIRECTORY]
        assert len(ids) == len(set(ids))

    def test_unimplemented_teams_exist_so_skips_are_observable(self):
        unimplemented = [c.agent_id for c in DIRECTORY if not c.implemented]
        assert "kafka-storage" in unimplemented
        assert "kafka-streams" in unimplemented
        assert "kafka-connect" in unimplemented
        assert "kafka-tools" in unimplemented


class TestDiscoverPeers:
    def test_owner_is_never_consulted_as_its_own_peer(self):
        result = discover_peers(
            [ImpactSignal(concern="cross_cluster_replication", evidence="x")],
            self_id="mirrormaker",
        )
        own = next(d for d in result.decisions if d.agent_id == "mirrormaker")
        assert own.consult is False

    def test_matching_concern_pulls_in_the_owning_team(self):
        result = discover_peers(
            [ImpactSignal(
                concern="authorization",
                evidence="filter exposes topic→group",
                codepath="StandardAuthorizer.java",
            )],
            self_id="mirrormaker",
        )
        security = next(d for d in result.consulted if d.agent_id == "kafka-security")
        assert "authorization" in security.matched_concerns
        assert security.codepath == "StandardAuthorizer.java"

    def test_unrelated_teams_are_skipped_with_a_reason_that_names_their_domain(self):
        result = discover_peers(
            [ImpactSignal(concern="authorization", evidence="acl gap")],
            self_id="mirrormaker",
        )
        storage = next(d for d in result.skipped if d.agent_id == "kafka-storage")
        assert "log" in storage.reason.lower()
        assert "kafka-storage" in storage.reason

    def test_unimplemented_team_is_still_consulted_when_its_concern_matches(self):
        """A gap in deployment must surface, not be routed around."""
        result = discover_peers(
            [ImpactSignal(
                concern="log_storage",
                evidence="would change compaction keys",
            )],
            self_id="mirrormaker",
        )
        storage = next(d for d in result.decisions if d.agent_id == "kafka-storage")
        assert storage.consult is True
        assert storage.reachable is False

    def test_one_decision_per_directory_entry(self):
        result = discover_peers([], self_id="mirrormaker")
        assert {d.agent_id for d in result.decisions} == {
            c.agent_id for c in DIRECTORY
        }

    def test_summarize_counts_consults_and_skips(self):
        result = discover_peers(
            [ImpactSignal(concern="wire_protocol", evidence="new field")],
            self_id="mirrormaker",
        )
        text = result.summarize()
        assert "consulted 1" in text
        assert f"considered {len(DIRECTORY)}" in text

    def test_unroutable_concern_is_reported(self):
        result = discover_peers(
            [ImpactSignal(concern="not_a_real_concern", evidence="typo")],
            self_id="mirrormaker",
        )
        assert "not_a_real_concern" in result.unroutable_concerns
        assert result.consulted == []


class TestMirrorMakerDiscovery:
    """The worked example: MM2 names consequences, not teams."""

    def setup_method(self):
        self.agent = MirrorMakerAgent()
        self.ticket = Ticket.new(
            team="mirrormaker", title="t", description="d",
        )
        self.inv = self.agent._investigate(self.ticket)
        self.signals = self.agent._impact_signals(self.ticket, self.inv)

    def test_signals_do_not_name_teams(self):
        for sig in self.signals:
            assert "group-coordinator" not in sig.evidence
            assert "kafka-broker" not in sig.evidence
            assert "kafka-clients" not in sig.evidence
            assert "kafka-security" not in sig.evidence

    def test_signals_use_the_shared_taxonomy(self):
        for sig in self.signals:
            assert sig.concern in CONCERNS, sig.concern

    def test_resolves_to_the_four_teams_the_change_actually_touches(self):
        result = discover_peers(self.signals, self_id="mirrormaker")
        consulted = {d.agent_id for d in result.consulted}
        assert consulted == {
            "group-coordinator",
            "kafka-broker",
            "kafka-clients",
            "kafka-security",
        }

    def test_skips_teams_with_nothing_to_do(self):
        result = discover_peers(self.signals, self_id="mirrormaker")
        skipped = {d.agent_id for d in result.skipped}
        assert "kafka-storage" in skipped
        assert "kafka-streams" in skipped
        assert "kafka-connect" in skipped
        assert "kafka-tools" in skipped
        assert "mirrormaker" in skipped

    def test_own_ticket_records_the_skips_on_the_finding(self):
        finding = build_network()["mirrormaker"].own_ticket(
            Ticket.new(
                team="mirrormaker",
                title="checkpoint discovery slow",
                description="p99=11s",
            )
        )
        skipped = {d.agent_id: d for d in finding.peer_discovery if not d.consult}
        assert "kafka-storage" in skipped
        assert "log" in skipped["kafka-storage"].reason.lower()
        consulted = {d.agent_id for d in finding.peer_discovery if d.consult}
        assert "kafka-security" in consulted
        assert finding.impact_signals
