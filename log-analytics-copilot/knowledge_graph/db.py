"""
Knowledge Graph database abstraction.

Supports two backends selected by the ``KG_BACKEND`` env var:
  - ``sqlite`` (default) — in-process sqlite3, great for tests and local dev
  - ``postgres``          — psycopg2 connection pool for production

Usage::

    from knowledge_graph.db import get_connection, apply_schema

    conn = get_connection()
    apply_schema(conn)
    conn.execute("INSERT INTO entities (name, entity_type) VALUES (?, ?)",
                 ("kora-global", "team"))
    conn.commit()

Both backends expose the same ``sqlite3.Connection``-compatible interface for
the SQL we actually use (INSERT, SELECT, parameterised queries).  Postgres
uses ``%s`` placeholders; sqlite3 uses ``?``.  Call ``placeholder()`` to get
the right one for the current backend.
"""

from __future__ import annotations

import os
import pathlib
import sqlite3
from typing import Any

_SCHEMA_PATH = pathlib.Path(__file__).parent / "schema.sql"

# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------

_BACKEND = os.environ.get("KG_BACKEND", "sqlite").lower()
_SQLITE_PATH = os.environ.get("KG_SQLITE_PATH", ":memory:")
_DATABASE_URL = os.environ.get("DATABASE_URL", "")


def placeholder() -> str:
    """Return the SQL placeholder token for the active backend."""
    return "%s" if _BACKEND == "postgres" else "?"


# ---------------------------------------------------------------------------
# Connection factory
# ---------------------------------------------------------------------------

def get_connection(path: str | None = None) -> Any:
    """Return a new database connection.

    ``path`` is only used for the sqlite backend; pass ``None`` to use the
    value from the ``KG_SQLITE_PATH`` environment variable (default: in-memory).
    """
    if _BACKEND == "postgres":
        try:
            import psycopg2  # type: ignore
        except ImportError as exc:
            raise ImportError(
                "psycopg2 is required for the postgres backend: pip install psycopg2-binary"
            ) from exc
        return psycopg2.connect(_DATABASE_URL)

    # sqlite3 backend (default)
    sqlite_path = path or _SQLITE_PATH
    conn = sqlite3.connect(sqlite_path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def apply_schema(conn: Any) -> None:
    """Apply schema.sql to ``conn``, idempotently (uses IF NOT EXISTS)."""
    sql = _SCHEMA_PATH.read_text()

    if _BACKEND == "postgres":
        # Postgres uses SERIAL / NOW() / JSONB; translate the sqlite dialect
        sql = (
            sql
            .replace("INTEGER PRIMARY KEY,", "SERIAL PRIMARY KEY,")
            .replace("datetime('now')", "NOW()")
        )
        conn.cursor().execute(sql)
    else:
        conn.executescript(sql)

    conn.commit()
