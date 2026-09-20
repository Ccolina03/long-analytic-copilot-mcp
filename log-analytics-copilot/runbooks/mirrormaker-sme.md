# Runbook: MirrorMaker SME Agent

**Agent id:** `mirrormaker`
**Domain:** MirrorMaker 2 — asynchronous replication between Kafka clusters.
**Upstream:** Apache Kafka, `connect/mirror/`, introduced by KIP-382.

This file is domain memory: architecture, ownership, and the tools this team
uses to inspect its own system. It is not a playbook for any particular ticket.

```
OWNS = [
    "connect/mirror/src/main/java/org/apache/kafka/connect/mirror/",
    "MirrorCheckpointConnector.java",
    "MirrorCheckpointTask.java",
    "MirrorSourceConnector.java",
    "MirrorHeartbeatConnector.java",
    "OffsetSyncStore.java",
    "MirrorClient.java",
    "Checkpoint.java",
    "MirrorMakerConfig.java",
]
```

---

## 1. Architecture

MirrorMaker 2 is a Kafka Connect-based replicator. It is the only Kafka
subsystem that has to reason about two clusters at once: a source it reads
from and a target it writes to. That is why cross-cluster tickets start here —
not because this team owns group metadata or the wire protocol, but because
the work spans a boundary no single-cluster team sees.

Three Connect connectors, one replication flow:

| Connector | What it moves | Cadence |
|---|---|---|
| `MirrorSourceConnector` | Topic records and topic configs | Continuous, driven by source lag |
| `MirrorCheckpointConnector` | Consumer-group offset checkpoints | `emit.checkpoints.interval.seconds` (default 60s) |
| `MirrorHeartbeatConnector` | Heartbeats used to measure replication lag | `emit.heartbeats.interval.seconds` |

A **replication flow** is the unit of deployment: one source cluster, one
target cluster, a topic filter, and a set of connector configs. A single MM2
installation can run many flows. Failover for a consumer means: stop consuming
on the source, wait for checkpoints to catch up, resume on the target at the
translated offset.

### Offset translation

`OffsetSyncStore` records, for each replicated partition, the mapping
`(source_offset → target_offset)` produced as records land. A checkpoint for a
consumer group is then:

```
checkpoint(group, tp) = translate(committed_offset_on_source(group, tp))
```

Translation is this team's problem. Discovering *which groups* need
translation is not — that information lives in group metadata, which this
team does not own. The current implementation asks the AdminClient for every
group in the source cluster and filters client-side. That is an ownership
boundary, not a design choice documented here.

### Checkpoint record

`Checkpoint.java` is the on-wire format written to the target's
`mm2-offset-syncs.<source>.internal` / checkpoints topic. Downstream consumers
(`MirrorClient`) read it to compute a resume offset. Changing this format is
a compatibility event for every failover client.

---

## 2. CODEOWNERS

This agent is authoritative over, and may only claim facts about, paths under
`connect/mirror/`.

```
connect/mirror/src/main/java/org/apache/kafka/connect/mirror/
├── MirrorSourceConnector.java      # record + config replication
├── MirrorSourceTask.java
├── MirrorCheckpointConnector.java  # group offset checkpoints
├── MirrorCheckpointTask.java
├── MirrorHeartbeatConnector.java   # lag / liveness heartbeats
├── OffsetSyncStore.java            # source→target offset map
├── MirrorClient.java               # client-facing translation helpers
├── Checkpoint.java                 # checkpoint record format
├── MirrorMakerConfig.java          # connector configuration surface
└── MirrorConnectorConfig.java
```

### Not owned — other teams change these

| Path | Owner | Why MM2 depends on it |
|---|---|---|
| `group-coordinator/` | `group-coordinator` | group membership, subscriptions, committed offsets |
| `clients/src/main/java/org/apache/kafka/clients/admin/` | `kafka-clients` | AdminClient is how MM2 talks to both clusters |
| `clients/src/main/resources/common/message/` | `kafka-clients` | every Admin RPC schema |
| `core/src/main/scala/kafka/server/` | `kafka-broker` | request serving on both clusters |
| `connect/runtime/` | `kafka-connect` | worker lifecycle, task rebalancing (Connect framework, not MM2) |
| `metadata/.../authorizer/` | `kafka-security` | ACLs on internal MM2 topics and on Admin calls |

This agent does not propose diffs to those paths. It states a requirement
and the owning team decides.

---

## 3. Tools

Capabilities for inspecting this team's own runtime. They answer questions
about MM2, not about other teams' subsystems.

### `get_checkpoint_latency(topic, last_hours=24)`
Percentiles for checkpoint emission on a topic, which phase of the connector
task dominates, and the configured emit interval.

### `get_group_discovery_trace(topic)`
Phase-by-phase timing of the most recent `findConsumerGroups()` run on a
topic: list, describe, filter, translate. Counts in and out of each phase.

### `list_replication_flows()`
Active flows: source/target, state, replicated topic count, lag, whether
group offset sync is enabled.

### `get_group_discovery_cost(flow_id)`
Per-flow efficiency: duration, groups examined, groups that actually needed
a checkpoint, hit rate.

---

## 4. Invariants this team will not violate

- **Do not own consumer-group state.** Membership, subscriptions, and the
  meaning of a committed offset belong to `group-coordinator`. Anything MM2
  infers about live membership by reading `__consumer_offsets` itself is
  reading the wrong data structure (committed offsets, not subscriptions).
- **Do not change AdminClient or request schemas.** Those are
  `kafka-clients`. MM2 is a caller of that API.
- **Checkpoint format changes are compatibility events.** `MirrorClient`
  and every failover tool reads `Checkpoint.java`.
