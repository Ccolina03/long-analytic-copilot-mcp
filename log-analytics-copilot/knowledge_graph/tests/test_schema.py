"""
Phase 1 / Slice 1 — Postgres Schema unit + integration tests.

All tests use sqlite3 (via db.get_connection()) so they run with no external
services.  The schema.sql uses sqlite3-compatible DDL.

Tests
-----
  test_schema_sql_is_valid_syntax
  test_schema_sql_contains_entities_table
  test_schema_sql_contains_edges_table
  test_migration_is_idempotent_on_repeat_apply
  test_entities_and_edges_tables_exist_after_migration
  test_edge_from_id_and_to_id_enforce_foreign_key
  test_edge_confidence_defaults_to_one_point_zero
  test_indexes_exist_on_edges_from_and_to
"""

import pathlib
import sqlite3

import pytest

from knowledge_graph.db import get_connection, apply_schema

_SCHEMA_PATH = pathlib.Path(__file__).parent.parent / "schema.sql"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_conn() -> sqlite3.Connection:
    """Return a brand-new in-memory sqlite3 connection."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _table_names(conn) -> set[str]:
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    return {row[0] for row in cur.fetchall()}


def _index_names(conn) -> set[str]:
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
    return {row[0] for row in cur.fetchall()}


def _column_names(conn, table: str) -> list[str]:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return [row[1] for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# Unit tests — no live DB state needed beyond reading the SQL file
# ---------------------------------------------------------------------------

class TestSchemaFile:
    def test_schema_sql_exists(self):
        assert _SCHEMA_PATH.exists(), "schema.sql file is missing"

    def test_schema_sql_is_valid_syntax(self):
        """Apply the schema to a throwaway in-memory DB; no syntax error = pass."""
        conn = _fresh_conn()
        try:
            conn.executescript(_SCHEMA_PATH.read_text())
        except sqlite3.OperationalError as exc:
            pytest.fail(f"schema.sql has a syntax error: {exc}")
        finally:
            conn.close()

    def test_schema_sql_contains_entities_table(self):
        sql = _SCHEMA_PATH.read_text()
        assert "entities" in sql

    def test_schema_sql_contains_edges_table(self):
        sql = _SCHEMA_PATH.read_text()
        assert "edges" in sql

    def test_schema_sql_confidence_default_is_1(self):
        """The schema must declare a DEFAULT 1.0 for the confidence column."""
        sql = _SCHEMA_PATH.read_text()
        assert "DEFAULT 1.0" in sql or "DEFAULT 1" in sql


# ---------------------------------------------------------------------------
# Integration tests — run against a real (in-memory) sqlite3 DB
# ---------------------------------------------------------------------------

class TestMigration:
    def test_tables_exist_after_migration(self):
        conn = _fresh_conn()
        apply_schema(conn)
        tables = _table_names(conn)
        assert "entities" in tables
        assert "edges" in tables

    def test_migration_is_idempotent(self):
        """Applying the schema twice must not raise an error."""
        conn = _fresh_conn()
        apply_schema(conn)
        apply_schema(conn)  # second call — must be safe
        assert "entities" in _table_names(conn)

    def test_entities_has_expected_columns(self):
        conn = _fresh_conn()
        apply_schema(conn)
        cols = _column_names(conn, "entities")
        for expected in ("id", "name", "entity_type", "metadata", "created_at"):
            assert expected in cols, f"expected column '{expected}' in entities"

    def test_edges_has_expected_columns(self):
        conn = _fresh_conn()
        apply_schema(conn)
        cols = _column_names(conn, "edges")
        for expected in ("id", "from_id", "to_id", "edge_type", "confidence", "evidence"):
            assert expected in cols, f"expected column '{expected}' in edges"

    def test_edge_confidence_defaults_to_one_point_zero(self):
        conn = _fresh_conn()
        apply_schema(conn)
        conn.execute("INSERT INTO entities (name, entity_type) VALUES ('t1', 'team')")
        conn.execute("INSERT INTO entities (name, entity_type) VALUES ('p1', 'codepath')")
        conn.execute(
            "INSERT INTO edges (from_id, to_id, edge_type) VALUES (1, 2, 'owns')"
        )
        conn.commit()
        row = conn.execute("SELECT confidence FROM edges WHERE id = 1").fetchone()
        assert row[0] == 1.0

    def test_edge_from_id_enforces_foreign_key(self):
        conn = _fresh_conn()
        apply_schema(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO edges (from_id, to_id, edge_type) VALUES (9999, 9999, 'owns')"
            )
            conn.commit()

    def test_indexes_exist_on_edges_from_and_to(self):
        conn = _fresh_conn()
        apply_schema(conn)
        indexes = _index_names(conn)
        assert "idx_edges_from" in indexes
        assert "idx_edges_to" in indexes

    def test_entities_name_is_unique(self):
        conn = _fresh_conn()
        apply_schema(conn)
        conn.execute("INSERT INTO entities (name, entity_type) VALUES ('team-a', 'team')")
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO entities (name, entity_type) VALUES ('team-a', 'team')")
            conn.commit()
