# Runbook: Kafka Broker SME Agent

**Agent id:** `kafka-broker`
**Domain:** Broker request handling, KRaft metadata, broker heap and GC.
**Upstream:** Apache Kafka, `core/` (Scala server) and `metadata/` (KRaft).

This file is domain memory: architecture, ownership, and the tools this team
uses to inspect its own system. It is not a playbook for any particular ticket.

```
OWNS = [
    "core/src/main/scala/kafka/server/",
    "KafkaApis.scala",
    "BrokerServer.scala",
    "KafkaConfig.scala",
    "metadata/src/main/java/org/apache/kafka/controller/",
    "QuorumController.java",
    "MetadataImage.java",
]
```

---

## 1. Architecture

A Kafka broker is a request server plus a replica. This team owns the request
server: how an RPC is dispatched, which other brokers must be contacted to
answer it, what is legal to store in cluster metadata, and the heap budget
everything on the broker competes for.

### Request dispatch

`KafkaApis.scala` is the single dispatch table. Every client RPC lands here
first. Some requests are answered locally (Produce to a leader replica this
broker holds, Fetch, Metadata). Some are forwarded (a Produce to a partition
this broker does not lead). Some **fan out** to other brokers and the results
are unioned before the response is written back.

Fan-out is a property of the *request*, not of the caller. `ListGroups` is
the standing example: a group's coordinator shard is

```
abs(group_id.hashCode()) % __consumer_offsets.partitions
```

which is independent of the topics that group consumes. Any `ListGroups` —
filtered or not — is therefore a scatter-gather across every coordinator
shard. This team owns that dispatch. `group-coordinator` owns what each
shard does with the request once it arrives.

Per-shard errors on a fan-out must be visible in the response. A silently
truncated union is a correctness bug for every caller.

### KRaft metadata

Since Kafka 3.3 / 4.0, cluster metadata lives in the `__cluster_metadata`
log, maintained by `QuorumController` and materialized on every broker as
`MetadataImage`. The log's design intent is **cluster topology that changes
rarely**:

- topics, partitions, replica assignments
- broker registrations
- ACLs and SCRAM credentials
- configs

It is replicated to every broker and replayed on startup. Snapshot size and
startup time grow with the log. Anything that churns at consumer-group
rebalance frequency (~orders of magnitude above topology changes) does not
belong here. Group membership in particular is `group-coordinator` state,
persisted in `__consumer_offsets`, and must stay there.

### Heap

The broker JVM is a shared budget. The large consumers, in typical order:

1. log index / page-cache metadata
2. replica fetcher buffers
3. group metadata (`group-coordinator`, resident on coordinator shards)
4. `MetadataImage`

Any new in-memory structure this team is asked to host is evaluated as a
percentage of total heap and against G1 pause p99, not as an absolute
megabyte number. Unbounded maps require a config cap and a documented
degraded path.

---

## 2. CODEOWNERS

```
core/src/main/scala/kafka/server/
├── KafkaApis.scala       # RPC dispatch, including fan-out
├── BrokerServer.scala    # broker lifecycle
├── KafkaConfig.scala     # broker configuration surface
└── ReplicaManager.scala  # (read-mostly from this agent's point of view)

metadata/src/main/java/org/apache/kafka/
├── controller/QuorumController.java
└── image/MetadataImage.java
```

### Not owned — other teams change these

| Path | Owner | Boundary |
|---|---|---|
| `group-coordinator/` | `group-coordinator` | what a coordinator shard *does*; this team routes *to* it |
| `clients/src/main/resources/common/message/` | `kafka-clients` | request schema; this team owns the handler |
| `clients/src/main/java/org/apache/kafka/clients/` | `kafka-clients` | client-side FindCoordinator / AdminClient |
| `storage/` | `kafka-storage` | log segments, retention, compaction |
| `metadata/.../authorizer/` | `kafka-security` | Authorizer is invoked from KafkaApis but owned there |

A protocol change needs both this team (can the handler serve it, at what
cost) and `kafka-clients` (is the schema legal). Neither can ship it alone.

---

## 3. Tools

### `get_request_routing(api_name)`
How `api_name` is dispatched: handler, routing strategy (local / forward /
fan-out), shard count, and the reason it routes that way.

### `get_coordinator_distribution(topic)`
How groups consuming a topic sit across coordinator shards: groups matched,
shards that actually hold a match, shards that must be queried to find them.

### `get_broker_heap_profile(broker_id)`
Heap total / used / headroom, GC collector, pause p99, largest consumers by
component.

### `get_kraft_metadata_budget()`
Metadata log size, steady-state record rate, snapshot interval, design
intent, and measured consumer-group churn for comparison.

---

## 4. Invariants this team will not violate

- **Group→shard placement stays `hash(group_id)`.** Re-hashing by subscribed
  topic is not migratable (client `FindCoordinator`, existing
  `__consumer_offsets` layout) and creates per-topic hotspots.
- **Group state does not go in `__cluster_metadata`.** Topology log, rare
  changes, replayed everywhere. Group churn would dominate it and would
  couple rebalance to controller availability.
- **Fan-out responses carry per-shard errors.** Callers must be able to tell
  a complete result from a partial one.
- **New heap is capped.** A map that grows with cluster size ships with a
  config bound and a fallback, or it does not ship.
