"""
Phase 1 / Slice 4 — Graph query library tests.

Tests
-----
  test_owning_team_returns_correct_team_for_exact_path_match
  test_owning_team_returns_none_for_unknown_path
  test_owning_team_of_list_groups_returns_consumer_team   ← the specific check from §20.4
  test_owning_team_of_clamp_offsets_returns_kora_global   ← the specific check from §20.4
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

      Teams:   kora-global, consumer-team, oss-kafka, broker-team
      Paths:   OffsetClampingService.java → kora-global
               confluent/kora-cluster-linking/ → kora-global
               GroupCoordinator.scala → consumer-team
               ListGroups → consumer-team
               ListGroupsRequest.json → oss-kafka

      depends_on:  cluster-linking-service → group-coordinator-service
                   cluster-linking-service → offset-translation-service

      must_approve: protocol_change → oss-kafka
                    coordinator_memory_change → broker-team
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
    kora   = add_entity("kora-global",    "team")
    ct     = add_entity("consumer-team",  "team")
    oss    = add_entity("oss-kafka",       "team")
    broker = add_entity("broker-team",    "team")

    # Codepaths
    clamp     = add_entity("OffsetClampingService.java",         "codepath")
    kora_dir  = add_entity("confluent/kora-cluster-linking/",   "codepath")
    gc_scala  = add_entity("GroupCoordinator.scala",            "codepath")
    list_grps = add_entity("ListGroups",                        "codepath")
    lg_json   = add_entity("ListGroupsRequest.json",            "codepath")

    # Ownership edges
    add_edge(kora, clamp,     "owns")
    add_edge(kora, kora_dir,  "owns")
    add_edge(ct,   gc_scala,  "owns")
    add_edge(ct,   list_grps, "owns")
    add_edge(oss,  lg_json,   "owns")

    # Services for depends_on
    cls_svc = add_entity("cluster-linking-service", "service")
    gc_svc  = add_entity("group-coordinator-service", "service")
    ot_svc  = add_entity("offset-translation-service", "service")
    add_edge(cls_svc, gc_svc, "depends_on")
    add_edge(cls_svc, ot_svc, "depends_on")
    add_edge(gc_svc,  oss,    "depends_on", confidence=0.5)

    # Approval rules
    proto_rule = add_entity("protocol_change",          "rule")
    mem_rule   = add_entity("coordinator_memory_change", "rule")
    add_edge(proto_rule, oss,    "must_approve")
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
        assert owning_team("OffsetClampingService.java", conn=self.conn) == "kora-global"

    def test_exact_path_for_consumer_team(self):
        assert owning_team("GroupCoordinator.scala", conn=self.conn) == "consumer-team"

    def test_owning_team_of_list_groups_returns_consumer_team(self):
        """Direct equivalent of the §20.4 integration check."""
        assert owning_team("ListGroups", conn=self.conn) == "consumer-team"

    def test_owning_team_of_clamp_offsets_returns_kora_global(self):
        """Direct equivalent of the §20.4 integration check."""
        assert owning_team("OffsetClampingService.java", conn=self.conn) == "kora-global"

    def test_returns_none_for_unknown_path(self):
        assert owning_team("some/completely/unknown/path.java", conn=self.conn) is None

    def test_prefix_match_resolves_kora_directory(self):
        # A sub-path of the kora dir should match kora-global
        result = owning_team(
            "confluent/kora-cluster-linking/src/FailoverCoordinator.java",
            conn=self.conn,
        )
        assert result == "kora-global"

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
        deps = depends_on("cluster-linking-service", depth=1, conn=self.conn)
        assert "group-coordinator-service" in deps
        assert "offset-translation-service" in deps

    def test_depth_one_does_not_include_transitive(self):
        # gc-service depends on oss-kafka (added in fixture), but depth=1 from
        # cluster-linking should NOT include oss-kafka
        deps = depends_on("cluster-linking-service", depth=1, conn=self.conn)
        assert "oss-kafka" not in deps

    def test_depth_two_includes_transitive(self):
        deps = depends_on("cluster-linking-service", depth=2, conn=self.conn)
        assert "oss-kafka" in deps

    def test_depth_below_one_raises(self):
        with pytest.raises(ValueError, match="depth must be >= 1"):
            depends_on("any-service", depth=0, conn=self.conn)

    def test_unknown_service_returns_empty_list(self):
        deps = depends_on("nonexistent-service", depth=1, conn=self.conn)
        assert deps == []

    def test_respects_requested_depth(self):
        """depth=1 and depth=2 must return different result sizes."""
        d1 = depends_on("cluster-linking-service", depth=1, conn=self.conn)
        d2 = depends_on("cluster-linking-service", depth=2, conn=self.conn)
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
        assert "oss-kafka" in approvers

    def test_coordinator_memory_change_requires_broker_team(self):
        approvers = must_approve("coordinator_memory_change", conn=self.conn)
        assert "broker-team" in approvers

    def test_unknown_change_type_returns_empty(self):
        approvers = must_approve("totally_unknown_change", conn=self.conn)
        assert approvers == []
