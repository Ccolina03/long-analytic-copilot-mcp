"""
Phase 2 — Typed Protocol tests.

Covers Ticket, DesignAlternative, DeliberationRound, ImpactRequest,
ImpactResponse, TeamInvolvement, and Finding.
"""

import pytest

from proto.sme_agents import (
    DeliberationRound,
    DesignAlternative,
    Finding,
    ImpactRequest,
    ImpactResponse,
    TeamInvolvement,
    Ticket,
)


class TestTicket:
    def test_from_dict_uses_provided_team(self):
        t = Ticket.from_dict({"team": "kora-global", "title": "slow clamp"})
        assert t.team == "kora-global"
        assert t.title == "slow clamp"

    def test_from_dict_missing_team_raises(self):
        with pytest.raises(KeyError):
            Ticket.from_dict({"title": "no team"})

    def test_new_auto_generates_unique_ticket_id(self):
        t1 = Ticket.new(team="kora-global", title="a", description="b")
        t2 = Ticket.new(team="kora-global", title="a", description="b")
        assert t1.ticket_id != t2.ticket_id

    def test_to_dict_round_trips(self):
        t = Ticket.new(team="kora-global", title="test", description="desc")
        t2 = Ticket.from_dict(t.to_dict())
        assert t2.ticket_id == t.ticket_id
        assert t2.team == t.team


class TestDesignAlternative:
    def _alt(self, **kw):
        defaults = dict(
            label="A", name="Some approach", proposed_by="kora-global",
            approach="do the thing",
        )
        defaults.update(kw)
        return DesignAlternative(**defaults)

    def test_defaults_are_sane(self):
        alt = self._alt()
        assert alt.effort == "M"
        assert alt.risk == "medium"
        assert alt.recommended is False
        assert alt.is_ruled_out is False

    def test_invalid_effort_raises(self):
        with pytest.raises(ValueError, match="effort must be one of"):
            self._alt(effort="ENORMOUS")

    def test_invalid_risk_raises(self):
        with pytest.raises(ValueError, match="risk must be one of"):
            self._alt(risk="catastrophic")

    def test_is_ruled_out_reflects_rejected_reason(self):
        alt = self._alt(rejected_reason="misses the latency target")
        assert alt.is_ruled_out is True

    def test_round_trips_through_dict(self):
        alt = self._alt(
            pros=["fast"], cons=["risky"], effort="L", risk="high",
            blast_radius=["consumer-team"], reviewed_by=["oss-kafka"],
        )
        alt2 = DesignAlternative.from_dict(alt.to_dict())
        assert alt2.name == alt.name
        assert alt2.pros == ["fast"]
        assert alt2.blast_radius == ["consumer-team"]
        assert alt2.reviewed_by == ["oss-kafka"]


class TestDeliberationRound:
    def test_round_trips_through_dict(self):
        r = DeliberationRound(
            round_number=2,
            from_agent="kora-global",
            to_agent="consumer-team",
            question="is the index feasible?",
            response_summary="yes, 13.7MB",
            alternatives_discussed=["Eager in-memory HashMap"],
            new_concerns_raised=["needs per-cluster flag"],
            verdict="needs_changes",
        )
        r2 = DeliberationRound.from_dict(r.to_dict())
        assert r2.round_number == 2
        assert r2.new_concerns_raised == ["needs per-cluster flag"]
        assert r2.converged is False


class TestImpactRequest:
    def test_round_trips_with_nested_alternatives(self):
        alt = DesignAlternative(
            label="A", name="Reverse index", proposed_by="kora-global",
            approach="add a map",
        )
        req = ImpactRequest.new(
            from_agent="kora-global",
            to_agent="consumer-team",
            ticket_id="ticket-123",
            request_type="design_review",
            question="feasible?",
            alternatives_on_table=[alt],
            round_number=2,
        )
        req2 = ImpactRequest.from_dict(req.to_dict())
        assert req2.round_number == 2
        assert len(req2.alternatives_on_table) == 1
        assert isinstance(req2.alternatives_on_table[0], DesignAlternative)
        assert req2.alternatives_on_table[0].name == "Reverse index"

    def test_round_number_defaults_to_one(self):
        req = ImpactRequest.new("a", "b", "t", "design_review")
        assert req.round_number == 1

    def test_consultation_depth_defaults_to_zero(self):
        req = ImpactRequest.new("a", "b", "t", "design_review")
        assert req.consultation_depth == 0


class TestImpactResponse:
    def test_list_fields_default_to_empty(self):
        resp = ImpactResponse(request_id="r1", from_agent="a", to_agent="b")
        assert resp.open_questions == []
        assert resp.new_concerns == []
        assert resp.design_alternatives == []
        assert resp.test_requirements == []

    def test_flags_default_to_false(self):
        resp = ImpactResponse(request_id="r1", from_agent="a", to_agent="b")
        assert resp.timed_out is False
        assert resp.needs_org_authority is False

    def test_round_trips_with_nested_alternatives(self):
        alt = DesignAlternative(
            label="A", name="Eager index", proposed_by="consumer-team", approach="map",
        )
        resp = ImpactResponse(
            request_id="r1", from_agent="consumer-team", to_agent="kora-global",
            verdict="needs_changes", design_alternatives=[alt],
            new_concerns=["B is incorrect"], test_requirements=["unit: index matches scan"],
        )
        resp2 = ImpactResponse.from_dict(resp.to_dict())
        assert resp2.verdict == "needs_changes"
        assert isinstance(resp2.design_alternatives[0], DesignAlternative)
        assert resp2.new_concerns == ["B is incorrect"]


class TestFinding:
    def test_recommended_alternative_returns_the_marked_one(self):
        a = DesignAlternative(label="A", name="A-way", proposed_by="x", approach="…")
        b = DesignAlternative(
            label="B", name="B-way", proposed_by="x", approach="…", recommended=True,
        )
        f = Finding(ticket_id="t", owning_agent="x", design_alternatives=[a, b])
        assert f.recommended_alternative is b

    def test_recommended_alternative_is_none_when_unmarked(self):
        a = DesignAlternative(label="A", name="A-way", proposed_by="x", approach="…")
        f = Finding(ticket_id="t", owning_agent="x", design_alternatives=[a])
        assert f.recommended_alternative is None

    def test_requires_human_defaults_to_false(self):
        f = Finding(ticket_id="t", owning_agent="a")
        assert f.requires_human is False

    def test_one_pager_fields_default_empty(self):
        f = Finding(ticket_id="t", owning_agent="a")
        assert f.goals == []
        assert f.non_goals == []
        assert f.testing_strategy == []
        assert f.teams_involved == []

    def test_round_trips_with_all_nested_types(self):
        f = Finding(
            ticket_id="t",
            owning_agent="kora-global",
            title="clampOffsets is slow",
            design_alternatives=[
                DesignAlternative(label="A", name="X", proposed_by="k", approach="…")
            ],
            teams_involved=[
                TeamInvolvement(team="consumer-team", role="implementer", owns="GC")
            ],
            deliberation=[
                DeliberationRound(
                    round_number=1, from_agent="k", to_agent="c",
                    question="?", response_summary="!",
                )
            ],
        )
        f2 = Finding.from_dict(f.to_dict())
        assert isinstance(f2.design_alternatives[0], DesignAlternative)
        assert isinstance(f2.teams_involved[0], TeamInvolvement)
        assert isinstance(f2.deliberation[0], DeliberationRound)
        assert f2.title == "clampOffsets is slow"
