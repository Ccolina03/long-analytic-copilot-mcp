"""
Knowledge Graph query library.

Every SME agent imports and calls these functions directly — there is no
separate network service.  This is intentional: every agent needs to look
up ownership for *its own* reasoning, not ask a central coordinator.

Core functions
--------------
    owning_team(codepath)         → str | None
    depends_on(service, depth=1)  → list[str]
    must_approve(change_type)     → list[str]

All functions take an optional ``conn`` argument.  If omitted, a fresh
connection is opened using ``db.get_connection()``.  In production, pass a
long-lived connection or connection pool object.  In tests, pass the test
fixture connection so no real DB is needed.
"""

from __future__ import annotations

import fnmatch
from typing import Any


# ---------------------------------------------------------------------------
# owning_team
# ---------------------------------------------------------------------------

def owning_team(codepath: str, conn: Any = None) -> str | None:
    """Return the team that owns ``codepath``, or ``None`` if unknown.

    Matching rules (applied in priority order):
    1. Exact match on ``entities.name``
    2. Prefix match   — the stored codepath is a directory prefix of the query
    3. Fnmatch glob   — the stored codepath contains ``*`` or ``?``

    The *most specific* match wins (longest prefix / most chars before the
    first wildcard).

    Example::

        >>> owning_team("connect/mirror/src/main/java/org/apache/kafka/connect/mirror/")
        'mirrormaker'
        >>> owning_team("GroupMetadataManager.java")
        'group-coordinator'
        >>> owning_team("unknown/path.java")
        None
    """
    if conn is None:
        from knowledge_graph.db import get_connection  # noqa: PLC0415
        conn = get_connection()

    # Fetch all owns edges with their entity names
    cursor = conn.execute(
        """
        SELECT t.name AS team, cp.name AS codepath
        FROM edges e
        JOIN entities t  ON e.from_id = t.id
        JOIN entities cp ON e.to_id   = cp.id
        WHERE e.edge_type = 'owns'
          AND t.entity_type = 'team'
        """
    )
    rows = cursor.fetchall()

    best_team: str | None = None
    best_specificity = -1

    for team, pattern in rows:
        if _matches(codepath, pattern):
            specificity = _specificity(pattern)
            if specificity > best_specificity:
                best_specificity = specificity
                best_team = team

    return best_team


def _matches(codepath: str, pattern: str) -> bool:
    """Return True if ``codepath`` matches ``pattern`` under any rule."""
    # Exact match
    if codepath == pattern:
        return True
    # Prefix match
    if codepath.startswith(pattern):
        return True
    # Suffix / basename match (e.g. "GroupCoordinator.scala" stored, queried by full path)
    if pattern in codepath:
        return True
    # Glob match
    if fnmatch.fnmatch(codepath, pattern):
        return True
    return False


def _specificity(pattern: str) -> int:
    """Longer, more specific patterns score higher."""
    return len(pattern)


# ---------------------------------------------------------------------------
# depends_on
# ---------------------------------------------------------------------------

def depends_on(service: str, depth: int = 1, conn: Any = None) -> list[str]:
    """Return services that ``service`` depends on, up to ``depth`` hops.

    Traverses ``depends_on`` edges in the Knowledge Graph.  ``depth=1``
    returns direct dependencies; ``depth=2`` includes transitive ones.

    Example::

        >>> depends_on("mirrormaker-connect-worker", depth=1)
        ['group-coordinator-service', 'offset-sync-store']
    """
    if depth < 1:
        raise ValueError("depth must be >= 1")

    if conn is None:
        from knowledge_graph.db import get_connection  # noqa: PLC0415
        conn = get_connection()

    all_deps: set[str] = set()
    queried: set[str] = {service}   # services we've already expanded
    frontier = {service}

    for _ in range(depth):
        if not frontier:
            break
        placeholders = ",".join("?" * len(frontier))
        cursor = conn.execute(
            f"""
            SELECT dep.name
            FROM edges e
            JOIN entities svc ON e.from_id = svc.id
            JOIN entities dep ON e.to_id   = dep.id
            WHERE e.edge_type = 'depends_on'
              AND svc.name IN ({placeholders})
            """,
            list(frontier),
        )
        new_deps = {row[0] for row in cursor.fetchall()} - queried
        all_deps |= new_deps
        queried |= new_deps
        frontier = new_deps

    return sorted(all_deps)


# ---------------------------------------------------------------------------
# must_approve
# ---------------------------------------------------------------------------

def must_approve(change_type: str, conn: Any = None) -> list[str]:
    """Return the list of teams that must approve a change of type ``change_type``.

    Looks for ``must_approve`` edges whose ``from`` entity matches
    ``change_type`` (stored as an entity of type ``rule``).

    Example::

        >>> must_approve("protocol_change")
        ['kafka-clients']
        >>> must_approve("coordinator_memory_change")
        ['kafka-broker']
    """
    if conn is None:
        from knowledge_graph.db import get_connection  # noqa: PLC0415
        conn = get_connection()

    cursor = conn.execute(
        """
        SELECT t.name
        FROM edges e
        JOIN entities rule  ON e.from_id = rule.id
        JOIN entities t     ON e.to_id   = t.id
        WHERE e.edge_type = 'must_approve'
          AND rule.name = ?
        """,
        (change_type,),
    )
    return sorted(row[0] for row in cursor.fetchall())
