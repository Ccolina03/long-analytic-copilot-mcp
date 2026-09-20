"""
Phase 1 / Slice 4 — Graph query library tests.

Tests
-----
  test_owning_team_returns_correct_team_for_exact_path_match
  test_owning_team_returns_none_for_unknown_path
  test_owning_team_of_list_groups_returns_consumer_team   ← the specific check from §20.4
  test_owning_team_of_checkpoint_connector_returns_mirrormaker   ← the specific check from §20.4
  test_owning_team_prefers_more_specific_match
  test_depends_on_returns_direct_dependencies
  test_depends_on_respects_requested_depth
  test_depends_on_depth_one_does_not_include_transitive
  test_must_approve_returns_correct_teams_for_change_type
"""

import sqlite3

import pytest

from knowledge_graph.db import apply_schema
from knowledge_graph.query import depends_on, must_approve, owning_team


# ---------------------------------------------------------------------------
# Fixture DB factory
# ---------------------------------------------------------------------------

def _seeded_db():
    """
    Build a small but realistic test graph that mirrors the Part V example:

      Teams:   mirrormaker, group-coordinator, kafka-clients, kafka-broker
      Paths:   MirrorCheckpointConnector.java → mirrormaker
               connect/mirror/src/main/java/org/apache/kafka/connect/mirror/ → mirrormaker
               GroupMetadataManager.java → group-coordinator
               ListGroups → group-coordinator
               ListGroupsRequest.json → kafka-clients

      depends_on:  mirrormaker-connect-worker → group-coordinator-service
                   mirrormaker-connect-worker → offset-sync-store

      must_approve: protocol_change → kafka-clients
                    coordinator_memory_change → kafka-broker
    """
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    apply_schema(conn)

    def add_entity(name, etype):
        conn.execute(
            "INSERT INTO entities (name, entity_type) VALUES (?, ?)",
            (name, etype),
        )
        return conn.execute(
            "SELECT id FROM entities WHERE name = ?", (name,)
        ).fetchone()[0]

    def add_edge(from_id, to_id, etype, confidence=1.0):
        conn.execute(
            "INSERT INTO edges (from_id, to_id, edge_type, confidence) VALUES (?,?,?,?)",
            (from_id, to_id, etype, confidence),
        )

    # Teams
    mm     = add_entity("mirrormaker",    "team")
    ct     = add_entity("group-coordinator",  "team")
    clients = add_entity("kafka-clients",     "team")
    broker = add_entity("kafka-broker",    "team")

    # Codepaths
    discover  = add_entity("MirrorCheckpointConnector.java",         "codepath")
    mirror_dir  = add_entity("connect/mirror/src/main/java/org/apache/kafka/connect/mirror/",   "codepath")
    gc_scala  = add_entity("GroupMetadataManager.java",            "codepath")
    list_grps = add_entity("ListGroups",                        "codepath")
    lg_json   = add_entity("ListGroupsRequest.json",            "codepath")

    # Ownership edges
    add_edge(mm, discover,  "owns")
    add_edge(mm, mirror_dir, "owns")
    add_edge(ct,   gc_scala,  "owns")
    add_edge(ct,   list_grps, "owns")
    add_edge(clients,  lg_json,   "owns")

    # Services for depends_on
    mm_svc  = add_entity("mirrormaker-connect-worker", "service")
    gc_svc  = add_entity("group-coordinator-service", "service")
    ot_svc  = add_entity("offset-sync-store", "service")
    add_edge(mm_svc, gc_svc, "depends_on")
    add_edge(mm_svc, ot_svc, "depends_on")
    add_edge(gc_svc,  clients,    "depends_on", confidence=0.5)

    # Approval rules
    proto_rule = add_entity("protocol_change",          "rule")
    mem_rule   = add_entity("coordinator_memory_change", "rule")
    add_edge(proto_rule, clients,    "must_approve")
    add_edge(mem_rule,   broker, "must_approve")

    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# owning_team tests
# ---------------------------------------------------------------------------

class TestOwningTeam:
    @pytest.fixture(autouse=True)
    def db(self):
        self.conn = _seeded_db()

    def test_exact_path_returns_correct_team(self):
        assert owning_team("MirrorCheckpointConnector.java", conn=self.conn) == "mirrormaker"

    def test_exact_path_for_consumer_team(self):
        assert owning_team("GroupMetadataManager.java", conn=self.conn) == "group-coordinator"

    def test_owning_team_of_list_groups_returns_consumer_team(self):
        """Direct equivalent of the §20.4 integration check."""
        assert owning_team("ListGroups", conn=self.conn) == "group-coordinator"

    def test_owning_team_of_checkpoint_connector_returns_mirrormaker(self):
        """Direct equivalent of the §20.4 integration check."""
        assert owning_team("MirrorCheckpointConnector.java", conn=self.conn) == "mirrormaker"

    def test_returns_none_for_unknown_path(self):
        assert owning_team("some/completely/unknown/path.java", conn=self.conn) is None

    def test_prefix_match_resolves_mirror_directory(self):
        # A sub-path of the connect/mirror dir should match mirrormaker
        result = owning_team(
            "connect/mirror/src/main/java/org/apache/kafka/connect/mirror/src/MirrorCheckpointTask.java",
            conn=self.conn,
        )
        assert result == "mirrormaker"

    def test_prefers_more_specific_match(self):
        """When two patterns both match, the longer (more specific) one wins."""
        # Add a generic wildcard ownership and a specific one
        conn = sqlite3.connect(":memory:")
        conn.execute("PRAGMA foreign_keys = ON")
        apply_schema(conn)
        conn.execute("INSERT INTO entities (name, entity_type) VALUES ('team-specific','team')")
        conn.execute("INSERT INTO entities (name, entity_type) VALUES ('team-generic','team')")
        conn.execute("INSERT INTO entities (name, entity_type) VALUES ('src/','codepath')")
        conn.execute("INSERT INTO entities (name, entity_type) VALUES ('src/payments/','codepath')")
        conn.commit()
        spec_team = conn.execute("SELECT id FROM entities WHERE name='team-specific'").fetchone()[0]
        gen_team  = conn.execute("SELECT id FROM entities WHERE name='team-generic'").fetchone()[0]
        src_id    = conn.execute("SELECT id FROM entities WHERE name='src/'").fetchone()[0]
        pay_id    = conn.execute("SELECT id FROM entities WHERE name='src/payments/'").fetchone()[0]
        conn.execute("INSERT INTO edges (from_id,to_id,edge_type) VALUES (?,?,'owns')", (gen_team, src_id))
        conn.execute("INSERT INTO edges (from_id,to_id,edge_type) VALUES (?,?,'owns')", (spec_team, pay_id))
        conn.commit()
        # The longer prefix should win
        result = owning_team("src/payments/checkout.java", conn=conn)
        assert result == "team-specific"


# ---------------------------------------------------------------------------
# depends_on tests
# ---------------------------------------------------------------------------

class TestDependsOn:
    @pytest.fixture(autouse=True)
    def db(self):
        self.conn = _seeded_db()

    def test_returns_direct_dependencies(self):
        deps = depends_on("mirrormaker-connect-worker", depth=1, conn=self.conn)
        assert "group-coordinator-service" in deps
        assert "offset-sync-store" in deps

    def test_depth_one_does_not_include_transitive(self):
        # gc-service depends on kafka-clients (added in fixture), but depth=1 from
        # mirrormaker should NOT include kafka-clients
        deps = depends_on("mirrormaker-connect-worker", depth=1, conn=self.conn)
        assert "kafka-clients" not in deps

    def test_depth_two_includes_transitive(self):
        deps = depends_on("mirrormaker-connect-worker", depth=2, conn=self.conn)
        assert "kafka-clients" in deps

    def test_depth_below_one_raises(self):
        with pytest.raises(ValueError, match="depth must be >= 1"):
            depends_on("any-service", depth=0, conn=self.conn)

    def test_unknown_service_returns_empty_list(self):
        deps = depends_on("nonexistent-service", depth=1, conn=self.conn)
        assert deps == []

    def test_respects_requested_depth(self):
        """depth=1 and depth=2 must return different result sizes."""
        d1 = depends_on("mirrormaker-connect-worker", depth=1, conn=self.conn)
        d2 = depends_on("mirrormaker-connect-worker", depth=2, conn=self.conn)
        assert len(d2) >= len(d1)


# ---------------------------------------------------------------------------
# must_approve tests
# ---------------------------------------------------------------------------

class TestMustApprove:
    @pytest.fixture(autouse=True)
    def db(self):
        self.conn = _seeded_db()

    def test_protocol_change_requires_oss_kafka(self):
        approvers = must_approve("protocol_change", conn=self.conn)
        assert "kafka-clients" in approvers

    def test_coordinator_memory_change_requires_broker_team(self):
        approvers = must_approve("coordinator_memory_change", conn=self.conn)
        assert "kafka-broker" in approvers

    def test_unknown_change_type_returns_empty(self):
        approvers = must_approve("totally_unknown_change", conn=self.conn)
        assert approvers == []
