-- SME Agent Network — Knowledge Graph schema
--
-- Production target: PostgreSQL 15+
-- Test compatibility: sqlite3 (used by unit tests via db.py sqlite mode)
--
-- Run idempotently:
--   psql $DATABASE_URL -f schema.sql
--
-- Tables
-- ------
--   entities  — every named thing the system knows about:
--               services, repositories, codepaths, teams, decisions, rules
--   edges     — typed relationships between entities with supporting evidence
--               and a confidence score

CREATE TABLE IF NOT EXISTS entities (
    id          INTEGER PRIMARY KEY,       -- SERIAL in Postgres; autoincrement in sqlite3
    name        TEXT    NOT NULL UNIQUE,
    entity_type TEXT    NOT NULL,          -- 'team' | 'repository' | 'codepath' |
                                           --  'service' | 'decision' | 'rule'
    metadata    TEXT    NOT NULL DEFAULT '{}',  -- JSON blob (JSONB in Postgres)
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))  -- TIMESTAMPTZ in Postgres
);

CREATE TABLE IF NOT EXISTS edges (
    id          INTEGER PRIMARY KEY,
    from_id     INTEGER NOT NULL REFERENCES entities(id),
    to_id       INTEGER NOT NULL REFERENCES entities(id),
    edge_type   TEXT    NOT NULL,          -- 'owns' | 'depends_on' | 'must_approve' |
                                           --  'consults' | 'implements'
    confidence  REAL    NOT NULL DEFAULT 1.0,  -- 0.0–1.0; lower = inferred, higher = declared
    evidence    TEXT    NOT NULL DEFAULT '{}', -- JSON: source file, line, timestamp
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Indexes used by query.py lookups
CREATE INDEX IF NOT EXISTS idx_edges_from       ON edges(from_id);
CREATE INDEX IF NOT EXISTS idx_edges_to         ON edges(to_id);
CREATE INDEX IF NOT EXISTS idx_edges_type       ON edges(edge_type);
CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_name ON entities(name);
-- Prevents duplicate edges on re-ingestion
CREATE UNIQUE INDEX IF NOT EXISTS idx_edges_unique ON edges(from_id, to_id, edge_type);
