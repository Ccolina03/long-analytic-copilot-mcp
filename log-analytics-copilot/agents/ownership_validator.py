"""
Ownership self-validation — Phase 2 / Slice 21.2.

Every SME agent calls ``validate_citations()`` before sending an
``ImpactResponse`` or assembling a ``Finding``.  It checks that every
codepath cited in the response actually falls within the agent's declared
``OWNS`` list.

Citations outside ``OWNS`` are not blocked — the agent may legitimately
*reference* a path it doesn't own — but they are flagged with
``needs_verification`` so the receiving agent (or human reviewer) knows
that claim was not first-hand knowledge.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass


@dataclass
class CitationResult:
    codepath: str
    owned: bool              # True if this agent's OWNS list covers this path
    status: str              # 'owned' | 'needs_verification'
    matched_pattern: str     # the OWNS entry that matched, or "" if none


def validate_citations(
    codepaths: list[str],
    owns: list[str],
) -> list[CitationResult]:
    """Return a CitationResult for each codepath in ``codepaths``.

    ``owns`` is the agent's declared ``OWNS`` list — a list of path prefixes
    or exact file names (same format used by the agent class attribute).

    A codepath is considered *owned* if any OWNS entry:
    - exactly matches it,
    - is a prefix of it (directory ownership),
    - is a substring of it (basename match), or
    - matches it via fnmatch glob.

    Args:
        codepaths:  List of file/path strings the agent wants to cite.
        owns:       The agent's OWNS list (strings, may include globs).

    Returns:
        One CitationResult per input codepath.
    """
    results: list[CitationResult] = []
    for cp in codepaths:
        matched = _find_matching_pattern(cp, owns)
        results.append(CitationResult(
            codepath=cp,
            owned=matched is not None,
            status="owned" if matched is not None else "needs_verification",
            matched_pattern=matched or "",
        ))
    return results


def _find_matching_pattern(codepath: str, owns: list[str]) -> str | None:
    """Return the first OWNS pattern that matches ``codepath``, or None."""
    for pattern in owns:
        if codepath == pattern:
            return pattern
        if codepath.startswith(pattern):
            return pattern
        if pattern in codepath:
            return pattern
        if fnmatch.fnmatch(codepath, pattern):
            return pattern
    return None


def all_owned(results: list[CitationResult]) -> bool:
    """Return True if every citation in ``results`` is owned."""
    return all(r.owned for r in results)


def unowned_citations(results: list[CitationResult]) -> list[CitationResult]:
    """Return only the citations that are *not* owned."""
    return [r for r in results if not r.owned]
