"""
Phase 2 / Slice 21.2 — Ownership self-validation tests.

Tests
-----
  test_citation_matching_owned_exact_path_passes
  test_citation_matching_owned_prefix_path_passes
  test_citation_outside_owned_paths_is_flagged_needs_verification
  test_all_owned_returns_true_when_all_citations_match
  test_all_owned_returns_false_when_any_citation_unowned
  test_unowned_citations_returns_only_unmatched_items
  test_validator_against_consumer_team_runbook_fixture
"""

import pytest

from agents.ownership_validator import (
    CitationResult,
    all_owned,
    unowned_citations,
    validate_citations,
)


class TestValidateCitations:
    _KORA_OWNS = [
        "confluent/kora-cluster-linking/",
        "OffsetClampingService.java",
        "FailoverCoordinator.java",
    ]

    def test_exact_path_passes(self):
        results = validate_citations(["OffsetClampingService.java"], self._KORA_OWNS)
        assert len(results) == 1
        assert results[0].owned is True
        assert results[0].status == "owned"

    def test_prefix_path_passes(self):
        # Sub-path of an owned directory
        results = validate_citations(
            ["confluent/kora-cluster-linking/src/FailoverCoordinator.java"],
            self._KORA_OWNS,
        )
        assert results[0].owned is True

    def test_citation_outside_owned_paths_is_flagged(self):
        results = validate_citations(
            ["GroupCoordinator.scala"],  # belongs to consumer-team, not kora
            self._KORA_OWNS,
        )
        assert results[0].owned is False
        assert results[0].status == "needs_verification"

    def test_empty_codepaths_returns_empty_list(self):
        assert validate_citations([], self._KORA_OWNS) == []

    def test_empty_owns_list_flags_everything(self):
        results = validate_citations(["SomeFile.java"], [])
        assert results[0].owned is False

    def test_matched_pattern_is_populated(self):
        results = validate_citations(["OffsetClampingService.java"], self._KORA_OWNS)
        assert results[0].matched_pattern == "OffsetClampingService.java"

    def test_unmatched_pattern_is_empty_string(self):
        results = validate_citations(["SomeUnknownFile.java"], self._KORA_OWNS)
        assert results[0].matched_pattern == ""


class TestHelpers:
    def test_all_owned_returns_true_when_all_citations_match(self):
        results = [
            CitationResult("a.java", True, "owned", "a.java"),
            CitationResult("b.java", True, "owned", "b.java"),
        ]
        assert all_owned(results) is True

    def test_all_owned_returns_false_when_any_unowned(self):
        results = [
            CitationResult("a.java", True, "owned", "a.java"),
            CitationResult("b.java", False, "needs_verification", ""),
        ]
        assert all_owned(results) is False

    def test_unowned_citations_returns_only_unmatched(self):
        results = [
            CitationResult("a.java", True, "owned", "a.java"),
            CitationResult("b.java", False, "needs_verification", ""),
            CitationResult("c.java", False, "needs_verification", ""),
        ]
        unowned = unowned_citations(results)
        assert len(unowned) == 2
        assert all(not r.owned for r in unowned)


class TestConsumerTeamRunbookFixture:
    """§21.2 integration check: consumer-team's OWNS list."""

    _CONSUMER_OWNS = [
        "apache/kafka/core/src/main/scala/kafka/coordinator/group/",
        "GroupCoordinator.scala",
        "GroupMetadata.scala",
        "TopicPartitionGroupIndex.java",
    ]

    def test_gc_scala_is_owned_by_consumer_team(self):
        results = validate_citations(["GroupCoordinator.scala"], self._CONSUMER_OWNS)
        assert results[0].owned is True

    def test_offset_clamping_service_is_not_owned_by_consumer_team(self):
        """OffsetClampingService.java belongs to kora-global, not consumer-team."""
        results = validate_citations(
            ["OffsetClampingService.java"], self._CONSUMER_OWNS
        )
        assert results[0].owned is False
        assert results[0].status == "needs_verification"

    def test_group_coordinator_full_path_matches_prefix_pattern(self):
        full_path = (
            "apache/kafka/core/src/main/scala/kafka/coordinator/group/"
            "GroupCoordinator.scala"
        )
        results = validate_citations([full_path], self._CONSUMER_OWNS)
        assert results[0].owned is True
