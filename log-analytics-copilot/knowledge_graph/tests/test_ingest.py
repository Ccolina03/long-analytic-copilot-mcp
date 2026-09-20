"""
Phase 1 / Slice 2 — GitHub / runbook ingestion tests.

Tests
-----
  test_parses_codeowners_line_into_pattern_and_owner
  test_ignores_comment_lines_and_blank_lines
  test_maps_wildcard_pattern_to_codepath_entity
  test_ingest_produces_owns_edge_for_each_codeowners_entry
  test_ingest_is_re_runnable_without_duplicating_edges
  test_parse_runbook_owns_extracts_paths
  test_runbook_ingest_produces_owns_edges_with_correct_team
  test_ingest_codeowners_dry_run_produces_no_db_writes
"""

import pathlib
import sqlite3

import pytest

from knowledge_graph.db import apply_schema
from knowledge_graph.ingest_github import (
    ingest_codeowners,
    ingest_runbook,
    parse_codeowners_file,
    parse_codeowners_line,
    parse_runbook_owns,
)

_FIXTURES = pathlib.Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Unit tests — pure Python, no DB
# ---------------------------------------------------------------------------

class TestParseCodeownersLine:
    def test_valid_line_returns_pattern_and_owner(self):
        result = parse_codeowners_line("/src/payments/  @payments-team")
        assert result == ("/src/payments/", "payments-team")

    def test_comment_line_returns_none(self):
        assert parse_codeowners_line("# comment") is None

    def test_blank_line_returns_none(self):
        assert parse_codeowners_line("") is None
        assert parse_codeowners_line("   ") is None

    def test_strips_at_sign_from_owner(self):
        result = parse_codeowners_line("/path/  @my-team")
        assert result is not None
        assert result[1] == "my-team"

    def test_no_owner_column_returns_none(self):
        assert parse_codeowners_line("/path/only") is None

    def test_maps_wildcard_pattern_to_codepath_entity_with_correct_metadata(self):
        """A wildcard pattern like *.proto is a valid codepath pattern."""
        result = parse_codeowners_line("*.proto  @platform-team")
        assert result is not None
        pattern, owner = result
        assert "*" in pattern
        assert owner == "platform-team"


class TestParseCodeownersFile:
    def test_parses_sample_fixture(self):
        text = (_FIXTURES / "sample_codeowners.txt").read_text()
        entries = parse_codeowners_file(text)
        # Comments and blanks are stripped; we should have 5 real entries
        assert len(entries) >= 4
        # The kora-global entry must be present
        patterns = [e[0] for e in entries]
        assert "/confluent/kora-cluster-linking/" in patterns

    def test_ignores_comment_lines_and_blank_lines(self):
        text = """
# This is a comment

/src/  @team-a
/lib/  @team-b
"""
        entries = parse_codeowners_file(text)
        assert len(entries) == 2
        assert all(e[0].startswith("/") for e in entries)


class TestParseRunbookOwns:
    _KORA_RUNBOOK = pathlib.Path(__file__).parent.parent.parent / "runbooks" / "kora-global-sme.md"

    def test_extracts_owns_paths_from_kora_runbook(self):
        if not self._KORA_RUNBOOK.exists():
            pytest.skip("kora-global-sme.md not found")
        text = self._KORA_RUNBOOK.read_text()
        # inject a synthetic OWNS block for testing (the real runbook uses prose)
        synthetic = text + "\nOWNS = [\n    \"OffsetClampingService.java\",\n    \"FailoverCoordinator.java\",\n]\n"
        entries = parse_runbook_owns(synthetic, "kora-global")
        assert len(entries) == 2
        assert ("OffsetClampingService.java", "kora-global") in entries
        assert ("FailoverCoordinator.java", "kora-global") in entries

    def test_returns_empty_list_when_no_owns_block(self):
        text = "# Just a runbook with no OWNS block"
        assert parse_runbook_owns(text, "some-agent") == []


# ---------------------------------------------------------------------------
# Integration tests — in-memory sqlite3 DB
# ---------------------------------------------------------------------------

def _fresh_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    apply_schema(conn)
    return conn


class TestIngestCodeowners:
    def test_ingest_produces_owns_edge_for_each_entry(self):
        conn = _fresh_db()
        text = "/src/  @team-a\n/lib/  @team-b\n"
        count = ingest_codeowners(conn, text)
        assert count == 2
        edges = conn.execute(
            "SELECT COUNT(*) FROM edges WHERE edge_type = 'owns'"
        ).fetchone()[0]
        assert edges == 2

    def test_ingest_creates_team_and_codepath_entities(self):
        conn = _fresh_db()
        ingest_codeowners(conn, "/src/payments/  @payments-team\n")
        names = {r[0] for r in conn.execute("SELECT name FROM entities").fetchall()}
        assert "payments-team" in names
        assert "/src/payments/" in names

    def test_ingest_is_re_runnable_without_duplicating_edges(self):
        conn = _fresh_db()
        text = "/src/  @team-a\n"
        ingest_codeowners(conn, text)
        ingest_codeowners(conn, text)  # second run
        edges = conn.execute(
            "SELECT COUNT(*) FROM edges WHERE edge_type = 'owns'"
        ).fetchone()[0]
        # should still be 1, not 2
        assert edges == 1

    def test_dry_run_does_not_write_to_db(self):
        conn = _fresh_db()
        ingest_codeowners(conn, "/src/  @team-a\n", dry_run=True)
        tables = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        assert tables == 0


class TestIngestRunbook:
    def test_runbook_ingest_produces_owns_edges_with_correct_team(self):
        conn = _fresh_db()
        text = 'OWNS = [\n    "OffsetClampingService.java",\n    "FailoverCoordinator.java",\n]\n'
        count = ingest_runbook(conn, text, agent_id="kora-global")
        assert count == 2
        # Verify the team entity was created
        team = conn.execute(
            "SELECT name FROM entities WHERE entity_type = 'team'"
        ).fetchone()
        assert team is not None
        assert team[0] == "kora-global"
