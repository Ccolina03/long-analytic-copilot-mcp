# Runbook: OSS Kafka SME Agent
**Agent ID:** `oss-kafka`  
**Domain:** Apache Kafka protocol, KIPs, API versioning, open-source contribution  
**Role in multi-agent consultations:** Upstream gate / Protocol approver

---

## 1. What This Team Manages

The OSS Kafka team is Confluent's interface to the Apache Kafka open-source
project. They own the Kafka wire protocol specification, manage the KIP
(Kafka Improvement Proposal) process for new APIs, and are the decision-makers
on what becomes part of upstream Apache Kafka vs what stays as a
Confluent-only extension.

Any change to the Kafka protocol — new API keys, new request fields, new
response fields, version bumps — **must go through this team before it can
ship in Apache Kafka**. Kora-only changes can bypass OSS review, but then
they cannot be used by open-source users and create a protocol fork.

### Core Responsibilities

| Area | Description |
|---|---|
| **KIP process** | Authors, reviews, and votes on Kafka Improvement Proposals. Every new public API or behavioral change to Apache Kafka requires a KIP. |
| **Protocol versioning** | Owns the Kafka protocol spec (`kafka/clients/src/main/resources/common/message/`). Manages API key assignments, version bumps, and backward/forward compatibility rules. |
| **Wire format** | JSON-defined message schemas in `*.json` files under `common/message/`. These generate Java classes via code generation. Any field addition, removal, or type change is a protocol change. |
| **Backward compatibility** | Strict rule: old clients talking to new brokers must work. New clients talking to old brokers must degrade gracefully (UNSUPPORTED_VERSION). |
| **Apache Kafka releases** | Manages what lands in each Apache Kafka release. A KIP must be voted in before it can be merged to trunk. |
| **Community liaison** | Represents Confluent in the Apache Kafka PMC (Project Management Committee). Committers on this team have merge authority. |

### What This Team Does NOT Own

- Confluent Cloud/Kora internal implementations — those are owned by the
  respective Confluent teams
- Confluent-specific extensions that don't touch the wire protocol
- Kafka Connect, Kafka Streams, ksqlDB — separate teams

---

## 2. The KIP Process (Agent Context)

When a new API or protocol change is needed, the flow is:

```
1. DRAFT
   Author (usually the code owner team) writes a KIP document covering:
   - Motivation (the problem)
   - Proposed changes (exact protocol schema diff)
   - Compatibility / migration plan
   - Rejected alternatives

2. DISCUSSION (~2 weeks)
   Posted to kafka-dev@apache.org mailing list.
   Community asks questions, proposes alternatives.
   KIP is revised based on feedback.

3. VOTE (~1 week)
   Formal vote on the mailing list.
   Requires 3 +1 votes from PMC members (binding).
   Any -1 with reason blocks the KIP until addressed.

4. IMPLEMENTATION
   Code submitted as a GitHub PR to apache/kafka.
   Must include:
   - Protocol schema change (.json file)
   - Server-side handler change
   - Client-side API change
   - Tests (unit + integration)
   - Documentation update

5. MERGE
   Reviewed by 2+ committers.
   Merged to trunk.
   Backport to maintenance branches is optional.
```

**Typical timeline: 6–10 weeks** from draft to trunk merge for a
non-controversial protocol addition.

### How API Versioning Works

Every Kafka API has:
- An **API key** (integer, e.g., `LIST_GROUPS = 16`)
- A **version range** supported by the broker (e.g., `0–4`)
- A **version range** supported by the client

When a client connects, it calls `ApiVersions` (API key 18) to discover
what versions the broker supports. If the broker supports `LIST_GROUPS` v0–4
and the client wants v5, the client degrades to v4. If the broker is old and
supports only v0–3, the client uses v3.

**Schema for a version bump** lives in:
```
clients/src/main/resources/common/message/ListGroupsRequest.json
clients/src/main/resources/common/message/ListGroupsResponse.json
```

These JSON files are the source of truth. Java classes are generated from them.

---

## 3. Codebase Ownership

### Apache Kafka (Open Source) — Primary Ownership

```
apache/kafka (https://github.com/apache/kafka)

clients/src/main/resources/common/message/
  ListGroupsRequest.json            ← ADD topic_partitions field here (v5)
  ListGroupsResponse.json           ← response shape (likely no change for v5)
  ApiVersionsResponse.json          ← broker advertises new version ranges here

clients/src/main/java/org/apache/kafka/common/protocol/
  ApiKeys.java
    Line 78:  LIST_GROUPS(16, "ListGroups",
                ApiKeys.MIN_API_KEY, ApiKeys.MAX_API_KEY,
                ListGroupsRequest.SCHEMAS,
                ListGroupsResponse.SCHEMAS)
              ← bump max version 4 → 5

clients/src/main/java/org/apache/kafka/clients/admin/
  Admin.java                        ← public interface — add listGroups overload
  KafkaAdminClient.java
    Line 3841: listGroups(ListGroupsOptions options)
              ← add ListGroupsOptions.forTopicPartitions(...)

core/src/main/scala/kafka/server/
  KafkaApis.scala
    Line 2201: handleListGroupsRequest()   ← routes to GroupCoordinator handler
```

### KIP Tracker (reference only — not editable code)

```
https://cwiki.apache.org/confluence/display/KAFKA/Kafka+Improvement+Proposals

Relevant existing KIPs:
  KIP-518  — Filter ListGroups by state/type (DONE — shipped in 2.6)
             Author: Justine Olshan
             Adds: states_filter, types_filter to ListGroupsRequest v4
             Does NOT add topic-partition filter

  KIP-345  — Static membership (DONE — shipped in 2.4)
  KIP-429  — Incremental cooperative rebalancing (DONE — shipped in 2.4)
  KIP-848  — New consumer group protocol (IN PROGRESS — trunk only)
             Important: new protocol has different group management —
             check compatibility with proposed index approach
```

---

## 4. Agent Tools

---

### `search_kips(query: str) → list`

Searches the KIP wiki and mailing list archives for existing proposals
related to a query. Prevents filing a duplicate KIP.

```json
{
  "query": "ListGroups topic partition filter",
  "results": [
    {
      "kip": "KIP-518",
      "title": "ListGroups API to filter by State",
      "status": "DONE",
      "shipped_version": "2.6.0",
      "relevance": "Adds state/type filter to ListGroups v4. Does NOT add topic-partition filter. This KIP is the direct predecessor."
    },
    {
      "kip": "KIP-848",
      "title": "The Next Generation of the Consumer Rebalance Protocol",
      "status": "IN_PROGRESS",
      "relevance": "New group protocol — proposed change must be compatible with KIP-848 group model."
    }
  ],
  "verdict": "No existing KIP covers topic-partition scoped ListGroups. New KIP required."
}
```

**Used when:** First thing this agent calls on any ticket involving a
protocol change. Prevents wasted work on duplicate proposals.

---

### `get_api_spec(api_name: str) → dict`

Returns the current protocol schema for a Kafka API — field names, types,
versions, and which version introduced each field.

```json
{
  "api": "ListGroups",
  "api_key": 16,
  "current_max_version": 4,
  "versions": [
    { "version": 0, "fields": [] },
    { "version": 1, "fields": ["throttle_time_ms"] },
    { "version": 2, "fields": ["throttle_time_ms"] },
    { "version": 3, "fields": ["throttle_time_ms"], "notes": "flexible version" },
    {
      "version": 4,
      "fields": ["throttle_time_ms", "states_filter", "types_filter"],
      "kip": "KIP-518"
    }
  ],
  "proposed_v5_field": "topic_partitions: [{topic: string, partition: int32}]"
}
```

**Used when:** Drafting the protocol schema diff for the KIP. Also sent to
Consumer Team so they know exactly which JSON file to modify.

---

### `check_compat(api_key: int, proposed_version: int, proposed_fields: list) → dict`

Validates that a proposed protocol change follows Kafka's compatibility rules.
Returns pass/fail and specific violations.

```json
{
  "api_key": 16,
  "proposed_version": 5,
  "checks": [
    {
      "rule": "new fields must be optional (tagged fields in flexible versions)",
      "status": "PASS",
      "note": "ListGroups v3+ uses flexible encoding — tagged fields are safe"
    },
    {
      "rule": "old clients sending v4 must get v4 behavior",
      "status": "PASS",
      "note": "empty topic_partitions = no filter = return all groups"
    },
    {
      "rule": "old brokers must return UNSUPPORTED_VERSION for v5",
      "status": "PASS",
      "note": "standard version negotiation handles this"
    },
    {
      "rule": "KIP-848 new protocol compatibility",
      "status": "REVIEW_NEEDED",
      "note": "New consumer protocol uses ConsumerGroupDescribeRequest (API 69) not ListGroups. Confirm behavior for mixed classic/consumer groups."
    }
  ],
  "overall": "PASS_WITH_NOTE",
  "kip_848_note": "Must specify behavior when topic_partitions filter is used against KIP-848 style groups"
}
```

**Used when:** Before approving or drafting a KIP — ensures the change won't
break existing clients or the upcoming KIP-848 protocol.

---

### `get_kip_template(api_name: str, change_type: str) → str`

Returns a pre-filled KIP draft template based on the API being changed and
the type of change (new field, new API, behavioral change).

```markdown
# KIP-XXX: ListGroups filter by topic-partition

**Author:** [Consumer Team]  
**Status:** Under Discussion  
**Discussion thread:** [link]

## Motivation
[Cluster Linking clampOffsets() currently calls ListGroups() and scans
all 50k groups to find the 4 groups subscribed to a given topic-partition.
This takes 8–12 seconds. The bottleneck is the absence of a reverse index
on GroupCoordinator and the lack of a topic-partition filter in the API.]

## Proposed Changes

### Protocol
ListGroupsRequest v5 adds an optional tagged field:
  `topic_partitions: ARRAY(STRUCT(topic STRING, partition INT32))`

### Server-side (GroupCoordinator)
- Add in-memory `topicPartitionToGroups: Map<TopicPartition, Set<GroupId>>`
- Maintain index on JoinGroup / LeaveGroup / heartbeat timeout
- handleListGroups(): if topic_partitions filter present → use index

### Client-side (AdminClient)
- Add `ListGroupsOptions.withTopicPartitions(List<TopicPartition>)`

## Compatibility
[...]

## Rejected Alternatives
1. New API key: higher cost, no clear benefit over versioned extension
2. Scan with early exit: still O(n) worst case, doesn't solve root cause
```

**Used when:** Consumer Team needs to file the KIP — this agent generates
the template so they only have to fill in specifics.

---

## 5. Downstream Impact — Who This Agent Consults

| Agent | Why Consulted | Type |
|---|---|---|
| `consumer-team` | KIP needs their detailed protocol schema diff and implementation plan | **REQUIRED — KIP author** |
| `kora-global` | Confirm Confluent is the primary stakeholder driving the KIP | **CONFIRM — sponsorship** |
| `broker-team` | KIP-848 compatibility note requires broker team to weigh in on mixed group scenarios | **NOTIFY — compatibility** |

**What this agent sends back to `consumer-team`:**

```
KIP verdict: NEW KIP REQUIRED

Closest precedent: KIP-518 (ListGroups state/type filter, v4)
  - Your change is a natural extension of KIP-518
  - Reference it as prior art in the KIP

Proposed API version: ListGroupsRequest v5
  - Use tagged fields (flexible version encoding) — safe because
    ListGroups has been flexible since v3
  - Field: topic_partitions ARRAY(STRUCT(topic STRING, partition INT32))
  - Empty = no filter (backward compatible)

Compatibility: PASS — one note
  - Must specify behavior for KIP-848 style (new protocol) groups
  - Recommend: return KIP-848 groups only if they have classic offsets committed
    OR explicitly document "not supported for new protocol groups"

KIP author: Consumer Team (you own the code)
KIP sponsor (PMC): [name from OSS Kafka team — will confirm internally]

Estimated timeline:
  Draft + discussion: 2 weeks
  Vote: 1 week
  Implementation review: 2 weeks
  Merge to trunk: 1 week
  Total: ~6 weeks to trunk

Kora can ship ahead of trunk using a Confluent extension flag if needed,
then align with OSS when the KIP merges.
```

**What this agent sends back to `kora-global`:**

```
Scope confirmed: this goes to Apache Kafka (not Kora-only).

Reason: MirrorMaker 2 users and any operator running DR/failover
on open-source Kafka have the identical problem. Shipping as
OSS benefits the community and avoids a protocol fork.

Fast-path option: Kora can implement the inverted index now as an
internal optimization (no protocol change needed for the server side).
The ListGroups v5 API filter is the part that needs the KIP.

Kora CAN ship the performance improvement (server-side index) before
the KIP is approved. The API surface (v5 filter) must wait for the KIP.
Timeline for Kora internal fix: immediate.
Timeline for public API: ~6 weeks.
```
