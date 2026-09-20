# Runbook: Kora Global SME Agent
**Agent ID:** `kora-global`  
**Domain:** Cluster Linking — failover, offset clamping, mirror operations  
**Role in multi-agent consultations:** Proposer / Integration consumer

---

## 1. What This Team Manages

Cluster Linking is the Confluent Cloud feature that mirrors topics between
Kafka clusters — across regions, clouds, or between on-prem and cloud. This
team owns everything from the time a link is created to the moment a consumer
can transparently fail over to a mirror topic.

### Core Responsibilities

| Area | Description |
|---|---|
| **Link lifecycle** | Create, pause, resume, delete cluster links. Owns the link metadata store. |
| **Topic mirroring** | Continuous replication of topic data from source to destination cluster. Partition-level offset tracking. |
| **Offset translation** | Source cluster offsets ≠ destination cluster offsets. Maintains the offset translation table for every `(link_id, topic, partition)`. |
| **Failover / clamping** | When a consumer fails over from source to destination, its committed offsets must be "clamped" to the closest valid destination offset. **This is where the current bottleneck lives.** |
| **Consumer group sync** | Optionally mirrors consumer group committed offsets to the destination cluster so consumers resume without re-processing. |
| **Link health monitoring** | Replication lag, mirror topic staleness, link state transitions. |

### The Current Failover Problem (Why This Agent Is Filing the Ticket)

During a failover operation, `ClusterLinking.clampOffsets()` must find every
consumer group that is subscribed to the topics being failed over, then
translate and clamp their offsets to valid destination positions.

**Current implementation:**

```
clampOffsets(failover_topics: List[TopicPartition]):
  # Step 1 — get all groups in cluster (EXPENSIVE)
  all_groups = AdminClient.listGroups()          # returns ALL groups, O(n_groups)

  # Step 2 — for each group, check if it touches our topics (EXPENSIVE)
  for group_id in all_groups:
    description = AdminClient.describeGroups([group_id])
    subscribed_topics = extract_topics(description)
    if any(tp.topic in failover_topics for tp in subscribed_topics):
      clamp(group_id, failover_topics)
```

**Observed impact:**
- Cluster with 50,000 consumer groups: `clampOffsets` takes **8–12 seconds**
- During an active failover this delay causes consumer lag spikes
- `listGroups` + `describeGroups` fan-out generates thundering herd on
  GroupCoordinator during exactly the moment it is under most stress

**What this agent needs from Consumer Team:**
An indexed API — `listGroupsForTopicPartition(topic, partition)` — so
clamping becomes O(1) instead of O(n_groups).

---

## 2. Codebase Ownership

### Internal Confluent Repositories

```
confluent/kora-cluster-linking/
  src/main/java/io/confluent/clusterlink/
    failover/
      FailoverCoordinator.java          ← owns the failover orchestration
      OffsetClampingService.java        ← THE bottleneck — listGroups() call is here
      OffsetTranslationTable.java       ← source ↔ destination offset mapping
    mirror/
      MirrorTopicReplicator.java        ← per-partition replication loop
      PartitionOffsetTracker.java       ← tracks replication progress
    admin/
      LinkAdminClient.java              ← wraps Kafka AdminClient for link ops
      GroupSyncService.java             ← mirrors consumer group offsets
    health/
      LinkHealthMonitor.java            ← lag alerts, stale mirror detection
```

**Key file for this ticket:**

```
OffsetClampingService.java

Line 142–189:  clampOffsets(List<TopicPartition> failoverPartitions)
  Line 151:    AdminClient.listGroups()          ← replace this
  Line 162:    AdminClient.describeGroups(...)   ← eliminate fan-out
  Line 178:    applyOffsetTranslation(...)       ← this part stays
```

### Relevant Apache Kafka Files (read-only — Consumer Team owns changes)

```
clients/src/main/java/org/apache/kafka/clients/admin/
  Admin.java                            ← interface Cluster Linking calls
  KafkaAdminClient.java                 ← implementation

clients/src/main/java/org/apache/kafka/common/requests/
  ListGroupsRequest.java                ← needs new topic-partition filter field
  ListGroupsResponse.java               ← needs to return filtered results
```

---

## 3. Agent Tools

These are the tools the `kora-global` SME agent can call autonomously
when investigating a ticket or responding to a `ConsultAbout` request.

---

### `get_failover_latency(topic: str, last_hours: int = 24) → dict`

Returns latency percentiles for recent `clampOffsets` executions for a topic.

```json
{
  "topic": "orders",
  "sample_count": 12,
  "p50_ms": 3200,
  "p95_ms": 8800,
  "p99_ms": 11400,
  "bottleneck_phase": "listGroups",
  "bottleneck_pct": 91
}
```

**Used when:** Filing a ticket — provides the evidence that the problem is real
and quantifies the impact.

---

### `get_offset_clamp_trace(topic: str, partition: int) → dict`

Replays the most recent offset clamping operation for a specific
`(topic, partition)` and shows exactly which groups were matched and how long
each phase took.

```json
{
  "topic": "orders",
  "partition": 3,
  "total_ms": 9200,
  "phases": [
    { "name": "listGroups",      "ms": 840,  "groups_returned": 50312 },
    { "name": "describeGroups",  "ms": 8100, "groups_scanned": 50312 },
    { "name": "filterMatch",     "ms": 180,  "groups_matched": 4 },
    { "name": "applyTranslation","ms": 80,   "offsets_clamped": 4 }
  ],
  "matched_groups": ["billing-consumer", "analytics-consumer",
                     "fraud-detector", "audit-logger"]
}
```

**Used when:** Proving to Consumer Team what the actual hot path is so they
scope the fix correctly.

---

### `list_active_links() → list`

Returns all active cluster links this Kora cluster is participating in,
with their current state and replication lag.

```json
[
  {
    "link_id": "us-east-1-to-eu-west-1",
    "state": "ACTIVE",
    "mirrored_topics": 142,
    "replication_lag_ms": 320,
    "consumer_group_sync": true
  }
]
```

**Used when:** Scoping impact — how many links would benefit from the new API.

---

### `get_consumer_groups_for_link(link_id: str) → dict`

Current slow implementation, exposed as a tool so the agent can demonstrate
the problem and propose the replacement.

```json
{
  "link_id": "us-east-1-to-eu-west-1",
  "method": "listGroups_then_filter",
  "duration_ms": 9200,
  "total_groups_scanned": 50312,
  "matched_groups": 4,
  "efficiency_pct": 0.008
}
```

---

## 4. Downstream Impact — Who This Agent Consults

When this agent files or investigates a ticket, it identifies these downstream
agents and initiates consultation:

| Agent | Why Consulted | Type |
|---|---|---|
| `consumer-team` | Owns GroupCoordinator — must implement the inverted index | **REQUIRED — code owner** |
| `oss-kafka` | New API key or protocol change needs KIP and Apache vote | **REQUIRED — upstream gate** |
| `broker-team` | GroupCoordinator runs on brokers — memory + performance impact on broker JVM | **NOTIFY — potential impact** |

**Consultation message template this agent sends:**

```
Requesting: ListGroupsForTopicPartition(topic, partition) → [group_id]

Evidence:
  - clampOffsets p99 = 11,400ms for cluster with 50k groups
  - 91% of time spent in listGroups + describeGroups fan-out
  - Only 4 of 50,312 groups actually matched (0.008% hit rate)

Proposed contract:
  - Input:  topic string, partition int32
  - Output: list of group_ids subscribed to that topic-partition
  - Latency target: <50ms

Needs from Consumer Team: implementation approach + approval
Needs from OSS Kafka: KIP or Confluent-extension decision
```
