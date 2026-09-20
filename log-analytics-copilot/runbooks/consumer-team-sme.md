# Runbook: Consumer Team SME Agent
**Agent ID:** `consumer-team`  
**Domain:** Consumer groups, GroupCoordinator, offset storage, rebalancing  
**Role in multi-agent consultations:** Code owner / Approver

---

## 1. What This Team Manages

The Consumer Team owns the GroupCoordinator — the component inside every
Kafka broker that tracks which consumers belong to which group, manages
partition assignments via rebalancing, and stores committed offsets in the
internal `__consumer_offsets` topic.

Every consumer group in every Kafka cluster in the world — open source or
Confluent Cloud — goes through this team's code.

### Core Responsibilities

| Area | Description |
|---|---|
| **GroupCoordinator** | The broker-side component that manages group membership, heartbeats, and rebalancing. One coordinator per group, elected by hashing `group_id % num_partitions(__consumer_offsets)`. |
| **Offset storage** | Committed offsets are written to `__consumer_offsets` (a compacted internal topic). Key format: `(group_id, topic, partition)`. Value: `(offset, metadata, commit_timestamp)`. |
| **Group state machine** | Empty → PreparingRebalance → CompletingRebalance → Stable → Dead. Every consumer join/leave/heartbeat-timeout triggers a transition. |
| **Rebalancing protocol** | JoinGroup / SyncGroup / Heartbeat / LeaveGroup RPCs. Handles cooperative rebalancing (KIP-429) and static membership (KIP-345). |
| **Consumer group listing APIs** | `ListGroups` (API key 16), `DescribeGroups` (API key 15), `OffsetFetch` (API key 9). This team owns the server-side handler for all of these. |
| **Consumer group deletion** | `DeleteGroups` (API key 42). Cleans up coordinator state and offset records. |

### What This Team Does NOT Own

- The consumer client library (`KafkaConsumer`) — owned by Client Libraries team
- The `__consumer_offsets` topic itself as infrastructure — owned by Broker team
- Cluster Linking's use of group data — owned by Kora Global team

---

## 2. The Technical Internals (Agent Context)

### How Groups Are Currently Indexed

The GroupCoordinator holds an in-memory map:

```
groupMetadataCache: Map<GroupId, GroupMetadata>
```

`GroupMetadata` contains:
```
GroupMetadata {
  groupId:    String
  state:      GroupState
  members:    Map<MemberId, MemberMetadata>
  offsets:    Map<(Topic, Partition), OffsetAndMetadata>
  ...
}
```

**There is no reverse index from `(topic, partition) → [group_id]`.**

This is why `listGroups` returns everything — the coordinator can only
iterate `groupMetadataCache.values()`. Filtering by topic requires loading
every group's metadata and checking its subscription.

### Why the Current API Cannot Be Fixed Without a Protocol Change

`ListGroupsRequest` (v0–v4) has these filter fields:
- `states_filter` (added in v4 via KIP-518) — filter by group state
- `types_filter` (added in v4 via KIP-518) — filter by group type (classic/consumer)

There is **no topic or partition filter field**. Adding one requires either:

1. A new version of `ListGroupsRequest` (v5+) — needs a KIP, backward compat
   guaranteed because old brokers ignore unknown fields in requests with
   known version numbers
2. A new API entirely — cleaner but higher coordination cost

### The Proposed Fix: In-Memory Inverted Index

Add a second index to GroupCoordinator:

```
topicPartitionToGroups: Map<(Topic, Partition), Set<GroupId>>
```

Maintained alongside `groupMetadataCache`:
- On `JoinGroup` / subscription update → add entries
- On `LeaveGroup` / group deletion → remove entries
- On coordinator failover → rebuild from `__consumer_offsets` replay

**Memory cost estimate:**
```
entry size ≈ 8 bytes (topic hash) + 4 bytes (partition int) + 
             avg 24 bytes (group_id string ref)
           ≈ 36 bytes per (topic, partition, group_id) tuple

Confluent p99 cluster: 50,000 groups × avg 8 subscribed tp per group
= 400,000 entries × 36 bytes = ~14 MB per coordinator partition

__consumer_offsets has 50 partitions → 50 coordinators per broker
= ~700 MB total across all coordinators in a large cluster

Acceptable for Kora (cloud JVM heap). May need guardrail for OSS.
```

---

## 3. Codebase Ownership

### Apache Kafka (Open Source) — Primary Ownership

```
apache/kafka (https://github.com/apache/kafka)

core/src/main/scala/kafka/coordinator/group/
  GroupCoordinator.scala          ← main coordinator logic
    Line 312–401:  handleJoinGroup()      ← update inverted index here
    Line 402–489:  handleSyncGroup()
    Line 534–612:  handleLeaveGroup()     ← remove from inverted index here
    Line 613–701:  handleHeartbeat()
    Line 702–798:  handleListGroups()     ← ADD topic-partition filter here
    Line 799–891:  handleDescribeGroups()

  GroupMetadata.scala             ← group state + subscription tracking
    Line 89–134:   MemberMetadata    ← holds subscription (list of topics)
    Line 135–290:  GroupMetadata     ← ADD topicPartitionIndex field here
    Line 291–340:  GroupSummary      ← returned by ListGroups

  GroupCoordinatorAdapter.scala   ← bridges new/old coordinator API
  GroupCoordinatorConfig.scala    ← config knobs (add index size limit here)

clients/src/main/java/org/apache/kafka/common/requests/
  ListGroupsRequest.java
    Line 45–89:    ListGroupsRequest.Builder
    Line 90–130:   toStruct()           ← ADD topicPartitions field in v5
  ListGroupsResponse.java
    Line 55–110:   ListedGroup          ← response shape (no change needed)

clients/src/main/java/org/apache/kafka/common/protocol/
  ApiKeys.java
    Line 78:       LIST_GROUPS(16, ...)  ← bump max version 4 → 5 here
```

### Confluent Internal (Kora Extensions)

```
confluent/kora-group-coordinator/
  src/main/java/io/confluent/coordinator/
    KoraGroupCoordinator.java         ← Kora-specific extensions to OSS GC
    KoraGroupMetadataManager.java     ← offset persistence layer for Kora
    TopicPartitionGroupIndex.java     ← NEW FILE — the inverted index
    GroupCoordinatorMetrics.java      ← metrics for the index (hit rate, size)
```

---

## 4. Agent Tools

---

### `get_group_state(group_id: str) → dict`

Returns the current in-memory state of a consumer group from the coordinator.

```json
{
  "group_id": "billing-consumer",
  "state": "Stable",
  "member_count": 6,
  "subscribed_topics": ["orders", "payments", "refunds"],
  "coordinator_broker": 12,
  "last_rebalance_ms_ago": 840000
}
```

**Used when:** Verifying a specific group's subscription to confirm it should
appear in a `listGroupsForTopicPartition` result.

---

### `get_groups_for_topic_partition(topic: str, partition: int) → dict`

**This tool represents the PROPOSED new capability.** Currently it runs the
slow scan; when the index exists it will use it. The tool exposes both paths
so the agent can show before/after.

```json
{
  "topic": "orders",
  "partition": 3,
  "method": "full_scan",
  "groups": ["billing-consumer", "analytics-consumer",
             "fraud-detector", "audit-logger"],
  "duration_ms": 9100,
  "groups_scanned": 50312,
  "note": "index not available — fell back to full scan"
}
```

After implementation:
```json
{
  "method": "inverted_index",
  "groups": ["billing-consumer", "analytics-consumer",
             "fraud-detector", "audit-logger"],
  "duration_ms": 3,
  "note": "served from TopicPartitionGroupIndex"
}
```

---

### `get_offset_storage_schema() → dict`

Returns the current `__consumer_offsets` key/value schema and representative
samples. Used to explain to OSS Kafka agent what a protocol change would
touch.

```json
{
  "key_format": "(group_id: string, topic: string, partition: int16)",
  "value_format": "(offset: int64, metadata: string, commit_timestamp: int64)",
  "num_partitions": 50,
  "total_records_sample_cluster": 2400000,
  "coordinator_partition_formula": "abs(group_id.hashCode()) % 50"
}
```

---

### `estimate_index_memory_cost(group_count: int, avg_subscriptions_per_group: int) → dict`

Calculates projected memory overhead of the inverted index for a cluster
of given size. Used when responding to Broker Team's concern about JVM heap.

```json
{
  "group_count": 50000,
  "avg_subscriptions": 8,
  "entries": 400000,
  "bytes_per_entry": 36,
  "total_mb": 13.7,
  "per_coordinator_mb": 0.27,
  "assessment": "acceptable — p99 Kora cluster uses < 1% of coordinator heap"
}
```

---

### `get_rebalance_history(topic: str, last_minutes: int = 60) → dict`

Recent rebalance events across all groups subscribed to a topic. Used to
assess whether the index rebuild-on-failover cost is acceptable.

```json
{
  "topic": "orders",
  "window_minutes": 60,
  "rebalance_count": 14,
  "avg_rebalance_ms": 340,
  "groups_affected": ["billing-consumer", "analytics-consumer"],
  "triggers": ["member_join: 8", "heartbeat_timeout: 4", "member_leave: 2"]
}
```

---

## 5. Downstream Impact — Who This Agent Consults

| Agent | Why Consulted | Type |
|---|---|---|
| `oss-kafka` | Inverted index is Kora-only; but the ListGroups v5 API must go through KIP for OSS | **REQUIRED — protocol change** |
| `broker-team` | GroupCoordinator runs inside the broker JVM — memory + GC impact assessment needed | **REQUIRED — approval** |
| `kora-global` | Confirm the proposed API contract matches what ClusterLinking actually needs | **CONFIRM — API shape** |

**What this agent sends to `oss-kafka`:**

```
Proposing: ListGroupsRequest v5 — add topic_partitions filter

Schema change:
  ListGroupsRequest v5 adds field:
    topic_partitions: [{ topic: string, partition: int32 }]  (optional)

  If empty → existing behavior (return all groups)
  If populated → return only groups subscribed to any of the given tp

Server-side: GroupCoordinator.handleListGroups() checks new field,
  routes to TopicPartitionGroupIndex if present.

Backward compat:
  - Old clients send v4 → no filter field → full list (unchanged)
  - New clients send v5 → filtered response
  - Old brokers receive v5 request → reject with UNSUPPORTED_VERSION
    (clients must check broker version before using v5)

KIP draft owner: Consumer Team
Need from OSS Kafka: sponsoring committer + vote timeline
```

**What this agent sends to `broker-team`:**

```
Memory impact assessment for new GroupCoordinator inverted index:

  Additional heap per broker: ~14 MB for p99 cluster (50k groups, 8 subs each)
  GC impact: index uses HashMap<TopicPartition, HashSet<String>>
             entries are short-lived only on group join/leave (low GC pressure)
  Coordinator failover rebuild time: ~800ms for 50k groups (replay __consumer_offsets)

  Asking: is 14MB additional heap acceptable in broker JVM config?
  Asking: any conflict with existing broker-side memory guardrails?
```
