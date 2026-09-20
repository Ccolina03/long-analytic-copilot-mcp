"""
GitHub ingestion: parse CODEOWNERS files and runbook OWNS declarations,
write ``Team --owns--> CodePath`` edges into the Knowledge Graph.

Usage::

    python -m knowledge_graph.ingest_github \\
        --codeowners path/to/CODEOWNERS \\
        --agent-manifests path/to/runbooks/ \\
        [--dry-run]

Supported input formats
-----------------------
1. GitHub CODEOWNERS format::

       # comment
       /src/payments/  @payments-team

2. Agent manifest format (runbook OWNS lists)::

       OWNS = [
           "connect/mirror/src/main/java/org/apache/kafka/connect/mirror/",
           "MirrorCheckpointConnector.java",
       ]

Both produce ``(team_entity) --owns--> (codepath_entity)`` edges.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from typing import Any


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def parse_codeowners_line(line: str) -> tuple[str, str] | None:
    """Parse one CODEOWNERS line into ``(pattern, owner_handle)``.

    Returns ``None`` for blank lines and comment lines.

    Examples::

        >>> parse_codeowners_line("/src/payments/  @payments-team")
        ('/src/payments/', 'payments-team')
        >>> parse_codeowners_line("# comment")
        None
        >>> parse_codeowners_line("")
        None
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None

    parts = stripped.split()
    if len(parts) < 2:
        return None  # no owner column

    pattern = parts[0]
    # first owner only; strip leading @ if present
    owner = parts[1].lstrip("@")
    return pattern, owner


def parse_codeowners_file(text: str) -> list[tuple[str, str]]:
    """Parse all valid entries from a CODEOWNERS file text.

    Returns a list of ``(pattern, owner)`` tuples, ignoring comments and blanks.
    """
    results: list[tuple[str, str]] = []
    for line in text.splitlines():
        parsed = parse_codeowners_line(line)
        if parsed:
            results.append(parsed)
    return results


_OWNS_PATTERN = re.compile(
    r'OWNS\s*=\s*\[([^\]]*)\]',
    re.DOTALL,
)
_STRING_PATTERN = re.compile(r'"([^"]+)"')


def parse_runbook_owns(text: str, agent_id: str) -> list[tuple[str, str]]:
    """Extract OWNS paths from a runbook/agent manifest.

    Looks for a block like::

        OWNS = [
            "path/to/repo/",
            "AnotherFile.java",
        ]

    Returns a list of ``(codepath, agent_id)`` tuples.
    """
    match = _OWNS_PATTERN.search(text)
    if not match:
        return []
    block = match.group(1)
    paths = _STRING_PATTERN.findall(block)
    return [(p, agent_id) for p in paths]


# ---------------------------------------------------------------------------
# Graph write helpers
# ---------------------------------------------------------------------------

def _upsert_entity(conn: Any, name: str, entity_type: str) -> int:
    """Insert entity if it doesn't exist; return its id in both cases."""
    ph = "?" if "sqlite" in type(conn).__module__ else "%s"

    conn.execute(
        f"INSERT OR IGNORE INTO entities (name, entity_type) VALUES ({ph}, {ph})",
        (name, entity_type),
    )
    cursor = conn.execute(
        f"SELECT id FROM entities WHERE name = {ph}",
        (name,),
    )
    row = cursor.fetchone()
    return row[0]


def _upsert_edge(
    conn: Any,
    from_id: int,
    to_id: int,
    edge_type: str,
    confidence: float,
    evidence: dict,
) -> None:
    ph = "?" if "sqlite" in type(conn).__module__ else "%s"
    conn.execute(
        f"""INSERT OR REPLACE INTO edges
            (from_id, to_id, edge_type, confidence, evidence)
            VALUES ({ph}, {ph}, {ph}, {ph}, {ph})""",
        (from_id, to_id, edge_type, confidence, json.dumps(evidence)),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ingest_codeowners(
    conn: Any,
    codeowners_text: str,
    source: str = "CODEOWNERS",
    dry_run: bool = False,
) -> int:
    """Ingest a CODEOWNERS file; return the number of edges written."""
    entries = parse_codeowners_file(codeowners_text)
    count = 0
    for pattern, owner in entries:
        if not dry_run:
            team_id = _upsert_entity(conn, owner, "team")
            path_id = _upsert_entity(conn, pattern, "codepath")
            _upsert_edge(
                conn, team_id, path_id, "owns", 1.0,
                {"source": source, "pattern": pattern},
            )
            count += 1
        else:
            print(f"  [dry-run] {owner} --owns--> {pattern}")
            count += 1
    if not dry_run:
        conn.commit()
    return count


def ingest_runbook(
    conn: Any,
    runbook_text: str,
    agent_id: str,
    source: str = "runbook",
    dry_run: bool = False,
) -> int:
    """Ingest OWNS declarations from a runbook; return the number of edges written."""
    entries = parse_runbook_owns(runbook_text, agent_id)
    count = 0
    for codepath, owner in entries:
        if not dry_run:
            team_id = _upsert_entity(conn, owner, "team")
            path_id = _upsert_entity(conn, codepath, "codepath")
            _upsert_edge(
                conn, team_id, path_id, "owns", 1.0,
                {"source": source, "agent_id": agent_id},
            )
            count += 1
        else:
            print(f"  [dry-run] {owner} --owns--> {codepath}")
            count += 1
    if not dry_run:
        conn.commit()
    return count


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _main(argv: list[str] | None = None) -> None:  # pragma: no cover
    parser = argparse.ArgumentParser(description="Ingest ownership edges into the Knowledge Graph")
    parser.add_argument("--codeowners", help="Path to CODEOWNERS file")
    parser.add_argument("--agent-manifests", help="Directory of runbook .md files")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    from knowledge_graph.db import get_connection, apply_schema  # noqa: PLC0415
    conn = get_connection()
    apply_schema(conn)

    if args.codeowners:
        text = pathlib.Path(args.codeowners).read_text()
        n = ingest_codeowners(conn, text, source=args.codeowners, dry_run=args.dry_run)
        print(f"Ingested {n} ownership edges from {args.codeowners}")

    if args.agent_manifests:
        root = pathlib.Path(args.agent_manifests)
        for md_file in sorted(root.glob("*.md")):
            agent_id = md_file.stem  # e.g. "mirrormaker-sme" → use as agent id
            text = md_file.read_text()
            n = ingest_runbook(conn, text, agent_id=agent_id, source=str(md_file), dry_run=args.dry_run)
            print(f"Ingested {n} ownership edges from {md_file.name}")


if __name__ == "__main__":
    _main()
