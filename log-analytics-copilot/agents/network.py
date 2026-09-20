"""Wire the Apache Kafka SME network for tests, the API, and the UI.

Every implemented agent is reachable. Which of them get *consulted* on a
ticket is decided at runtime by ``agents.discovery``, not by this wiring.
Unimplemented directory entries (storage, streams, connect, tools) are
deliberately absent from the transport — if discovery ever decides they
must review, the finding records that gap instead of inventing a peer.
"""

from __future__ import annotations

from typing import Any

from agents.base_agent import DirectTransport, SMEAgentBase
from agents.group_coordinator_agent import GroupCoordinatorAgent
from agents.kafka_broker_agent import KafkaBrokerAgent
from agents.kafka_clients_agent import KafkaClientsAgent
from agents.kafka_security_agent import KafkaSecurityAgent
from agents.mirrormaker_agent import MirrorMakerAgent
from agents.trace import Tracer, attach_tracer


IMPLEMENTED = (
    "mirrormaker",
    "group-coordinator",
    "kafka-broker",
    "kafka-clients",
    "kafka-security",
)


def build_network(
    *,
    tracer: Tracer | None = None,
    llms: dict[str, Any] | None = None,
) -> dict[str, SMEAgentBase]:
    """Return a dict of implemented agents, fully wired to each other.

    The owning agent for a ticket is ``network[ticket.team]``.
    """
    llms = llms or {}

    mirrormaker = MirrorMakerAgent(llm=llms.get("mirrormaker"))
    coordinator = GroupCoordinatorAgent(llm=llms.get("group-coordinator"))
    broker = KafkaBrokerAgent(llm=llms.get("kafka-broker"))
    clients = KafkaClientsAgent(llm=llms.get("kafka-clients"))
    security = KafkaSecurityAgent(llm=llms.get("kafka-security"))

    agents: dict[str, SMEAgentBase] = {
        "mirrormaker": mirrormaker,
        "group-coordinator": coordinator,
        "kafka-broker": broker,
        "kafka-clients": clients,
        "kafka-security": security,
    }

    # Every agent can reach every other agent. Discovery, not this map,
    # decides who is actually called.
    for agent in agents.values():
        agent._transport = DirectTransport(agents)

    if tracer is not None:
        attach_tracer(tracer, agents.values())

    return agents
