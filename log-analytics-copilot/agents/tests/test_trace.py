"""Tracer tests — the timeline the UI replays."""

from agents.network import build_network
from agents.trace import PHASES, Tracer, attach_tracer
from proto.sme_agents import Ticket


class TestTracer:
    def test_null_by_default_does_not_record(self):
        agent = build_network()["mirrormaker"]
        agent.own_ticket(Ticket.new(team="mirrormaker", title="t", description="d"))
        assert agent.tracer.events == [] or type(agent.tracer).__name__ == "NullTracer"

    def test_shared_tracer_sees_every_phase(self):
        tracer = Tracer(ticket_id="t1", title="demo")
        net = build_network(tracer=tracer)
        net["mirrormaker"].own_ticket(
            Ticket.new(team="mirrormaker", title="demo", description="p99")
        )
        reached = set(tracer.phases_reached())
        for phase in ("intake", "investigation", "discovery", "alternatives",
                      "deliberation", "decision", "artifact"):
            assert phase in reached, f"missing phase {phase}"

    def test_discovery_events_include_skips(self):
        tracer = Tracer()
        net = build_network(tracer=tracer)
        net["mirrormaker"].own_ticket(
            Ticket.new(team="mirrormaker", title="t", description="d")
        )
        decisions = [e for e in tracer.events if e.kind == "peer_decision"]
        skipped = [e for e in decisions if e.data.get("consult") is False]
        consulted = [e for e in decisions if e.data.get("consult") is True]
        assert any(e.to_agent == "kafka-storage" for e in skipped)
        assert any(e.to_agent == "kafka-security" for e in consulted)

    def test_deliberation_records_both_sides(self):
        tracer = Tracer()
        net = build_network(tracer=tracer)
        net["mirrormaker"].own_ticket(
            Ticket.new(team="mirrormaker", title="t", description="d")
        )
        requests = [e for e in tracer.events if e.kind == "request"]
        responses = [e for e in tracer.events if e.kind == "response"]
        assert requests
        assert len(requests) == len(responses)
        assert {e.to_agent for e in requests} >= {
            "group-coordinator", "kafka-broker", "kafka-clients", "kafka-security"
        }

    def test_listeners_see_events_as_they_are_emitted(self):
        seen = []
        tracer = Tracer()
        tracer.on_event(seen.append)
        tracer.emit(phase="intake", agent="mirrormaker", kind="ticket", title="hi")
        assert len(seen) == 1
        assert seen[0].title == "hi"

    def test_to_dict_is_json_serialisable(self):
        tracer = Tracer(ticket_id="x", title="y")
        tracer.emit(phase="intake", agent="a", kind="ticket", title="t")
        d = tracer.to_dict()
        assert d["ticket_id"] == "x"
        assert d["event_count"] == 1
        assert d["phases"] == list(PHASES)
        assert d["events"][0]["title"] == "t"

    def test_attach_tracer_shares_one_instance(self):
        tracer = Tracer()
        net = build_network(tracer=tracer)
        attach_tracer(tracer, net.values())
        for agent in net.values():
            assert agent.tracer is tracer
