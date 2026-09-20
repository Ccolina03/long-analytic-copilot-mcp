# Runbook: Group Coordinator SME Agent

**Agent id:** `group-coordinator`
**Domain:** Consumer groups — membership, rebalance protocols, offset storage.
**Upstream:** Apache Kafka, `group-coordinator/` module (Java, since 4.0 / KIP-848).

This file is domain memory: architecture, ownership, and the tools this team
uses to inspect its own system. It is not a playbook for any particular ticket.

```
OWNS = [
    "group-coordinator/src/main/java/org/apache/kafka/coordinator/group/",
    "GroupMetadataManager.java",
    "GroupCoordinatorService.java",
    "GroupCoordinatorShard.java",
    "GroupCoordinatorConfig.java",
    "OffsetMetadataManager.java",
    "ConsumerGroup.java",
    "ClassicGroup.java",
]
```

---

## 1. Architecture

The group coordinator is the broker-side state machine for consumer groups.
It is a separate Gradle module as of Kafka 4.0. It tracks who is in a group,
what they subscribe to, which partitions they are assigned, and the offsets
they have committed. Clients never talk to "the coordinator" as a process —
they talk to whichever broker currently hosts the group's shard.

### Two protocols, one module

The module serves both the classic client-driven protocol and the KIP-848
server-driven protocol at the same time. A cluster will have groups of both
types for years. Any state this module stores, any query it answers, and any
index it maintains has to be correct for both.

| | Classic | KIP-848 (`consumer`) |
|---|---|---|
| Group type | `classic` | `consumer` |
| Membership RPCs | `JoinGroup`, `SyncGroup`, `Heartbeat`, `LeaveGroup` | `ConsumerGroupHeartbeat` |
| Assignor | Client-side | Server-side |
| Offset commit | `OffsetCommit` / `OffsetFetch` | same RPCs, shared storage |
| Class | `classic/ClassicGroup.java` | `modern/ConsumerGroup.java` |

`GroupMetadataManager` is the in-memory source of truth for both. Persistence
is the compacted internal topic `__consumer_offsets`.

### Sharding

Group state is partitioned across `__consumer_offsets`:

```
shard = abs(group_id.hashCode()) % num_partitions   # default 50
```

Placement is by **group id**, never by subscribed topic. A group's coordinator
can move (broker failure, partition reassignment) but the hash itself is a
de facto contract: every client library computes it via `FindCoordinator`,
and existing `__consumer_offsets` records are already laid out by it.

`kafka-broker` owns how a request is *routed* to those shards.
This team owns what happens *on* a shard once the request arrives.

### Two different facts, two different stores

This is the distinction other teams get wrong most often:

| Fact | Where it lives | When it is written |
|---|---|---|
| Live membership + subscription | `GroupMetadataManager` (memory), replayed from group-metadata records in `__consumer_offsets` | join / leave / heartbeat timeout / subscription change |
| Committed offset | `OffsetMetadataManager`, offset-commit records in `__consumer_offsets` | `OffsetCommit` |

A group that has joined, been assigned partitions, and not yet committed is
fully real in membership and invisible in the offset-commit stream. Designs
that treat `__consumer_offsets` as "who is consuming this topic" are reading
the commit log, not the membership log.

### Request surface this module handles

`GroupCoordinatorService` is the entry point for:

- membership RPCs (both protocols)
- `OffsetCommit` / `OffsetFetch`
- `ListGroups` / `DescribeGroups` / `DeleteGroups`
- `Heartbeat` / `LeaveGroup`

`ListGroups` today walks every group the local shard hosts and applies
in-memory filters (`states_filter` since v4, `types_filter` since v5). There
is no index from topic to group in this module.

---

## 2. CODEOWNERS

```
group-coordinator/src/main/java/org/apache/kafka/coordinator/group/
├── GroupMetadataManager.java     # membership + subscription, both protocols
├── GroupCoordinatorService.java  # request entry points
├── GroupCoordinatorShard.java    # per-__consumer_offsets-partition state machine
├── GroupCoordinatorConfig.java
├── OffsetMetadataManager.java    # committed offset storage
├── classic/ClassicGroup.java
└── modern/ConsumerGroup.java
```

### Not owned — other teams change these

| Path | Owner | Why this module depends on it |
|---|---|---|
| `clients/src/main/resources/common/message/` | `kafka-clients` | request/response schemas this service implements |
| `core/src/main/scala/kafka/server/KafkaApis.scala` | `kafka-broker` | dispatch into `GroupCoordinatorService` |
| `core/src/main/scala/kafka/server/KafkaConfig.scala` | `kafka-broker` | broker-level config this module reads |
| `metadata/` | `kafka-broker` | KRaft metadata is cluster topology, not group state |
| `connect/mirror/` | `mirrormaker` | a caller, not a component of this module |

---

## 3. Tools

### `get_group_state(group_id)`
In-memory state of one group: protocol type, state, members, subscribed
topics, coordinator shard, time since last rebalance.

### `get_groups_for_topic_partition(topic, partition)`
Which groups currently subscribe to a topic-partition. Reports the method
used (today: walk every group on every shard) and how long that walk took.

### `get_offset_storage_schema()`
`__consumer_offsets` key/value format, partition count, and the group→shard
hash. Required reading before anything that would add a record type or
change placement.

### `estimate_index_memory_cost(group_count, avg_subscriptions_per_group)`
Heap projection for an in-memory structure sized off group metadata. Used
whenever this team is asked to hold additional derived state.

### `get_rebalance_history(topic, last_minutes=60)`
Recent rebalance events for groups on a topic, with trigger breakdown
(join, heartbeat timeout, leave). Write-path churn for anything maintained
on membership change.

---

## 4. Invariants this team will not violate

- **Committed offsets are not live subscriptions.** Correcting that
  confusion is this team's job in any cross-team review.
- **Both protocols or neither.** A structure that is only correct for
  classic groups is a bug, not an MVP.
- **The group-id hash is not ours to change.** It is a client-visible
  placement rule. `kafka-broker` owns the routing implications; this team
  does not re-hash to make a query cheaper.
- **Group membership does not belong in KRaft `__cluster_metadata`.** That
  log is cluster topology. Membership churn would dominate it. This is a
  `kafka-broker` invariant this team will back.
