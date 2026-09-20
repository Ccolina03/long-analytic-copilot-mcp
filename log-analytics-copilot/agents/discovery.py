"""
Peer self-discovery — how an SME agent finds out who it needs to talk to.

The problem this solves
----------------------
An SME agent starts a ticket knowing only its own domain. It must *not* carry
a hardcoded list of peers, for two reasons:

  1. A hardcoded list is a lie about how engineering organizations work. The
     MirrorMaker engineer investigating a replication bug does not begin with
     the knowledge that the security team needs to review it. They discover
     that, because their proposed change turns out to have an authorization
     consequence.
  2. A hardcoded list does not scale. Adding a team would mean editing every
     other agent.

How discovery works
-------------------
The agent finishes its own investigation and declares **consequences**, not
teams. Each consequence is an ``ImpactSignal`` tagged with a concern from the
:data:`CONCERNS` taxonomy — "this change mutates consumer group state", "this
change adds a field to a wire protocol schema", "this change lets a caller
infer which groups consume a topic".

:func:`discover_peers` then resolves those signals against the org directory
(:data:`DIRECTORY`), where each team declares which concerns it is
authoritative on. A team whose concerns intersect the signals gets consulted.
A team whose concerns do not gets **skipped with a recorded reason**.

Recording the skips is as important as recording the consults. It is the
evidence that the agent knew the storage team existed, considered whether a
group-metadata index touches log segments, and decided it does not — rather
than simply never having heard of them. A system that consults all nine teams
on every ticket is not intelligent, it is just expensive and annoying.

Reading the result
------------------
:func:`discover_peers` returns a ``PeerDecision`` for every team in the
directory. ``summarize()`` renders the tally a reviewer actually wants:
"considered 9 teams, consulted 4, skipped 5".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from proto.sme_agents import ImpactSignal, PeerDecision

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Concern taxonomy
# ---------------------------------------------------------------------------
#
# The shared vocabulary between "what my change does" and "what your team is
# authoritative on".  Keeping it a closed set is what makes discovery
# auditable: an agent cannot invent a concern nobody owns, and a typo surfaces
# as an unroutable signal instead of silently dropping a team from review.

CONCERNS: dict[str, str] = {
    "cross_cluster_replication":
        "replicating topics, offsets, and consumer groups between clusters",
    "group_state_mutation":
        "the membership, subscription, or lifecycle state of a consumer group",
    "offset_storage_format":
        "the record format written to the __consumer_offsets topic",
    "wire_protocol":
        "the binary request/response schemas and API version negotiation",
    "public_java_api":
        "the public Java client surface (AdminClient, Consumer, Producer)",
    "request_routing":
        "how a request is dispatched to a broker and which brokers must serve it",
    "broker_memory":
        "broker heap budget, GC pressure, and in-memory index sizing",
    "cluster_metadata":
        "the KRaft metadata log and what is legal to store in cluster metadata",
    "authorization":
        "ACLs, principal permissions, and information disclosure through APIs",
    "log_storage":
        "log segments, retention, compaction, and tiered storage",
    "streams_runtime":
        "the Kafka Streams runtime, its topologies and internal topics",
    "connect_runtime":
        "the Connect framework, worker lifecycle, and connector rebalancing",
    "cli_tooling":
        "the shipped command-line tools under tools/",
}


# ---------------------------------------------------------------------------
# Org directory
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AgentCard:
    """A team's entry in the org directory.

    This is the only thing an agent knows about other teams before discovery
    runs: a domain description, the codepaths they own, and the concerns they
    answer for. It deliberately contains no opinion about any particular
    ticket.
    """

    agent_id: str
    domain: str
    owns: tuple[str, ...]
    answers: tuple[str, ...]
    # False for teams that exist in the org but have no SME agent deployed yet.
    # Discovery still evaluates them, so a ticket that genuinely needs them
    # surfaces the gap instead of silently routing around it.
    implemented: bool = True

    def concern_labels(self) -> tuple[str, ...]:
        return tuple(CONCERNS.get(c, c) for c in self.answers)


DIRECTORY: tuple[AgentCard, ...] = (
    AgentCard(
        agent_id="mirrormaker",
        domain="MirrorMaker 2 — async cross-cluster replication, offset "
               "translation, checkpointing",
        owns=(
            "connect/mirror/src/main/java/org/apache/kafka/connect/mirror/",
        ),
        answers=("cross_cluster_replication",),
    ),
    AgentCard(
        agent_id="group-coordinator",
        domain="Consumer group coordination — membership, rebalance protocols, "
               "offset commits",
        owns=(
            "group-coordinator/src/main/java/org/apache/kafka/coordinator/group/",
        ),
        answers=("group_state_mutation", "offset_storage_format"),
    ),
    AgentCard(
        agent_id="kafka-broker",
        domain="Broker request handling — KafkaApis dispatch, KRaft metadata, "
               "broker resource budgets",
        owns=(
            "core/src/main/scala/kafka/server/",
            "metadata/src/main/java/org/apache/kafka/",
        ),
        answers=("request_routing", "broker_memory", "cluster_metadata"),
    ),
    AgentCard(
        agent_id="kafka-clients",
        domain="Wire protocol, RPC schemas, AdminClient, and the KIP process",
        owns=(
            "clients/src/main/java/org/apache/kafka/clients/admin/",
            "clients/src/main/resources/common/message/",
        ),
        answers=("wire_protocol", "public_java_api"),
    ),
    AgentCard(
        agent_id="kafka-security",
        domain="Authorization, ACLs, and information disclosure through the "
               "public APIs",
        owns=(
            "metadata/src/main/java/org/apache/kafka/metadata/authorizer/",
            "clients/src/main/java/org/apache/kafka/common/acl/",
        ),
        answers=("authorization",),
    ),
    # --- teams in the directory with no agent deployed yet ---
    AgentCard(
        agent_id="kafka-storage",
        domain="Log subsystem — segments, retention, compaction, tiered storage",
        owns=("storage/src/main/java/org/apache/kafka/storage/",),
        answers=("log_storage",),
        implemented=False,
    ),
    AgentCard(
        agent_id="kafka-streams",
        domain="Kafka Streams runtime, topology building, internal topics",
        owns=("streams/src/main/java/org/apache/kafka/streams/",),
        answers=("streams_runtime",),
        implemented=False,
    ),
    AgentCard(
        agent_id="kafka-connect",
        domain="Connect framework — worker lifecycle, connector rebalancing, "
               "the REST API",
        owns=("connect/runtime/src/main/java/org/apache/kafka/connect/runtime/",),
        answers=("connect_runtime",),
        implemented=False,
    ),
    AgentCard(
        agent_id="kafka-tools",
        domain="Shipped command-line tools (kafka-consumer-groups.sh and friends)",
        owns=("tools/src/main/java/org/apache/kafka/tools/",),
        answers=("cli_tooling",),
        implemented=False,
    ),
)


def directory_by_id(
    directory: tuple[AgentCard, ...] = DIRECTORY,
) -> dict[str, AgentCard]:
    return {card.agent_id: card for card in directory}


def card_for(
    agent_id: str, directory: tuple[AgentCard, ...] = DIRECTORY
) -> AgentCard | None:
    return directory_by_id(directory).get(agent_id)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

@dataclass
class DiscoveryResult:
    """Everything discovery produced, for the audit trail and the UI."""

    signals: list[ImpactSignal] = field(default_factory=list)
    decisions: list[PeerDecision] = field(default_factory=list)

    @property
    def consulted(self) -> list[PeerDecision]:
        return [d for d in self.decisions if d.consult]

    @property
    def skipped(self) -> list[PeerDecision]:
        return [d for d in self.decisions if not d.consult]

    @property
    def unroutable_concerns(self) -> list[str]:
        """Signals no team in the directory claims. A gap in the org model."""
        claimed = {c for card in DIRECTORY for c in card.answers}
        return sorted({s.concern for s in self.signals if s.concern not in claimed})

    def peers(self) -> list[tuple[str, str]]:
        """The ``(agent_id, codepath)`` pairs the deliberation loop consumes."""
        return [(d.agent_id, d.codepath) for d in self.consulted]

    def summarize(self) -> str:
        return (
            f"considered {len(self.decisions)} teams, "
            f"consulted {len(self.consulted)}, skipped {len(self.skipped)}"
        )


def discover_peers(
    signals: list[ImpactSignal],
    *,
    self_id: str,
    directory: tuple[AgentCard, ...] = DIRECTORY,
) -> DiscoveryResult:
    """Resolve impact signals to the teams that need to weigh in.

    Produces a ``PeerDecision`` for every team in ``directory`` — consulted or
    skipped — so the reason a team was left out is on the record.

    Args:
        signals:   what the investigating agent believes its change affects.
        self_id:   the investigating agent, excluded from its own consultation.
        directory: the org directory to resolve against.

    Returns:
        A :class:`DiscoveryResult` carrying the signals and one decision per team.
    """
    by_concern: dict[str, list[ImpactSignal]] = {}
    for sig in signals:
        by_concern.setdefault(sig.concern, []).append(sig)

    decisions: list[PeerDecision] = []

    for card in directory:
        if card.agent_id == self_id:
            decisions.append(PeerDecision(
                agent_id=card.agent_id,
                consult=False,
                reason="This is the owning team — it is already investigating.",
                reachable=True,
            ))
            continue

        matched = [c for c in card.answers if c in by_concern]

        if not matched:
            decisions.append(PeerDecision(
                agent_id=card.agent_id,
                consult=False,
                reason=_skip_reason(card),
                reachable=card.implemented,
            ))
            continue

        # A team can own several concerns; ask about the codepath belonging to
        # the first signal that actually named one, falling back to the team's
        # primary owned path so the question is never unanchored.
        codepath = next(
            (s.codepath for c in matched for s in by_concern[c] if s.codepath),
            card.owns[0] if card.owns else "",
        )

        decisions.append(PeerDecision(
            agent_id=card.agent_id,
            consult=True,
            reason=_consult_reason(card, matched, by_concern),
            matched_concerns=matched,
            codepath=codepath,
            reachable=card.implemented,
        ))

    result = DiscoveryResult(signals=list(signals), decisions=decisions)

    logger.info("[%s] peer discovery: %s", self_id, result.summarize())
    for d in result.consulted:
        logger.info(
            "[%s]   consult %s on %s", self_id, d.agent_id,
            ", ".join(d.matched_concerns),
        )
    for d in result.skipped:
        if d.agent_id != self_id:
            logger.info("[%s]   skip %s — %s", self_id, d.agent_id, d.reason)

    unreachable = [d.agent_id for d in result.consulted if not d.reachable]
    if unreachable:
        logger.warning(
            "[%s] discovery needs %s but no agent is deployed for them",
            self_id, ", ".join(unreachable),
        )

    return result


def _consult_reason(
    card: AgentCard,
    matched: list[str],
    by_concern: dict[str, list[ImpactSignal]],
) -> str:
    """Why this team was pulled in, in the investigating agent's own words."""
    parts = []
    for concern in matched:
        evidence = by_concern[concern][0].evidence
        parts.append(f"{CONCERNS.get(concern, concern)} — {evidence}")
    joined = "; ".join(parts)
    return f"Authoritative on {joined}"


def _skip_reason(card: AgentCard) -> str:
    """Why this team was considered and left out.

    Names what the team owns so a reviewer can check the judgement rather than
    taking "not relevant" on faith.
    """
    owned = " or ".join(card.concern_labels())
    return (
        f"Nothing in this investigation touches {owned}. "
        f"Consulting {card.agent_id} would cost them a review cycle and "
        f"return no signal."
    )
