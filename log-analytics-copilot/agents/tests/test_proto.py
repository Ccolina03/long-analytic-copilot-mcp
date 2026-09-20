"""
Phase 2 — Typed Protocol tests.

Tests
-----
  test_impact_request_round_trips_through_serialize_and_parse
  test_impact_response_open_questions_defaults_to_empty_list
  test_finding_confidence_field_accepts_float_between_0_and_1
  test_ticket_from_dict_uses_provided_team
  test_ticket_new_auto_generates_ticket_id
  test_impact_request_new_factory_sets_all_required_fields
"""

import pytest

from proto.sme_agents import Finding, ImpactRequest, ImpactResponse, Ticket


class TestTicket:
    def test_from_dict_uses_provided_team(self):
        t = Ticket.from_dict({"team": "kora-global", "title": "slow clamp"})
        assert t.team == "kora-global"
        assert t.title == "slow clamp"

    def test_from_dict_missing_team_raises(self):
        with pytest.raises(KeyError):
            Ticket.from_dict({"title": "no team"})

    def test_new_auto_generates_ticket_id(self):
        t1 = Ticket.new(team="kora-global", title="a", description="b")
        t2 = Ticket.new(team="kora-global", title="a", description="b")
        assert t1.ticket_id != t2.ticket_id

    def test_to_dict_round_trips(self):
        t = Ticket.new(team="kora-global", title="test", description="desc")
        d = t.to_dict()
        t2 = Ticket.from_dict(d)
        assert t2.ticket_id == t.ticket_id
        assert t2.team == t.team


class TestImpactRequest:
    def test_round_trips_through_serialize_and_parse(self):
        req = ImpactRequest.new(
            from_agent="kora-global",
            to_agent="consumer-team",
            ticket_id="ticket-123",
            request_type="impact_analysis",
            context="clampOffsets is slow",
            codepaths_of_interest=["GroupCoordinator.scala"],
        )
        d = req.to_dict()
        req2 = ImpactRequest.from_dict(d)
        assert req2.from_agent == "kora-global"
        assert req2.to_agent == "consumer-team"
        assert req2.request_id == req.request_id
        assert req2.codepaths_of_interest == ["GroupCoordinator.scala"]

    def test_new_factory_sets_all_required_fields(self):
        req = ImpactRequest.new(
            from_agent="a",
            to_agent="b",
            ticket_id="t",
            request_type="protocol_review",
        )
        assert req.from_agent == "a"
        assert req.to_agent == "b"
        assert req.ticket_id == "t"
        assert req.request_type == "protocol_review"
        assert req.request_id  # non-empty auto-generated UUID

    def test_consultation_depth_defaults_to_zero(self):
        req = ImpactRequest.new("a", "b", "t", "r")
        assert req.consultation_depth == 0


class TestImpactResponse:
    def test_open_questions_defaults_to_empty_list(self):
        resp = ImpactResponse(
            request_id="r1",
            from_agent="consumer-team",
            to_agent="kora-global",
        )
        assert resp.open_questions == []

    def test_timed_out_defaults_to_false(self):
        resp = ImpactResponse(
            request_id="r1",
            from_agent="consumer-team",
            to_agent="kora-global",
        )
        assert resp.timed_out is False

    def test_to_dict_round_trips(self):
        resp = ImpactResponse(
            request_id="r1",
            from_agent="consumer-team",
            to_agent="kora-global",
            verdict="needs_changes",
            confidence=0.9,
            open_questions=["ask oss-kafka about KIP"],
        )
        d = resp.to_dict()
        resp2 = ImpactResponse.from_dict(d)
        assert resp2.verdict == "needs_changes"
        assert resp2.open_questions == ["ask oss-kafka about KIP"]


class TestFinding:
    def test_confidence_field_accepts_float_between_0_and_1(self):
        f = Finding(
            ticket_id="t",
            owning_agent="kora-global",
            summary="ok",
            root_cause="cause",
            confidence=0.95,
        )
        assert 0.0 <= f.confidence <= 1.0

    def test_to_dict_includes_execution_order(self):
        f = Finding(
            ticket_id="t",
            owning_agent="kora-global",
            summary="ok",
            root_cause="cause",
            execution_order=["step 1", "step 2"],
        )
        d = f.to_dict()
        assert d["execution_order"] == ["step 1", "step 2"]

    def test_requires_human_defaults_to_false(self):
        f = Finding(
            ticket_id="t", owning_agent="a", summary="s", root_cause="r"
        )
        assert f.requires_human is False
