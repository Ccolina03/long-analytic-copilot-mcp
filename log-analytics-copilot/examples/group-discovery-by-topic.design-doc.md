# MirrorCheckpointConnector group discovery is slow — 8-12s p99 on clusters with >10k consumer groups

| | |
|---|---|
| **Owner** | `mirrormaker` |
| **Ticket** | `96d46f80-ba9f-4aee-9894-111d9fd1d65f` |
| **Status** | Converged — ready for review |
| **Deliberation rounds** | 3 |
| **Confidence** | 95% |
| **Needs a human decision** | Yes |

---

## TL;DR

MM2 checkpoint group discovery is 11400ms at p99 against a 500ms target because it scans all 50,312 consumer groups to find the 4 that matter. Kafka has no reverse index from (topic, partition) to group. We evaluated three alternatives and recommend **Broker-side reverse index + ListGroups v6 topic filter** (effort L, risk medium): it is the only option that reaches the latency target without MirrorMaker taking ownership of consumer-group state it should not own. The broker-side index lands behind a flag immediately; the public API surface follows via KIP.

## Background

MirrorMaker 2 replicates topics asynchronously between Kafka clusters. To let a consumer fail over from the source cluster to the target, MM2 must translate that consumer group's committed offsets into equivalent target offsets and emit them as checkpoints. Doing that requires knowing every consumer group that consumes the topics being replicated.

`MirrorCheckpointConnector.findConsumerGroups()` discovers those groups by calling `Admin.listConsumerGroups()` — which returns every group in the cluster — and then `describeConsumerGroups()` on each one to read its subscription. On a cluster with 50,312 consumer groups this takes 11400ms at p99, and only 4 of those groups actually matter.

The cost lands at the worst possible moment. Checkpoint emission runs on a 60s interval, so an 11400ms discovery pass burns a fifth of the budget on every cycle; during an active failover the coordinators are already under maximum stress and the delay shows up directly as consumer lag. 2 active replication flows would benefit from a fix, and the same problem affects any Kafka operator asking which groups consume a topic.

## Goals

**In scope**

- Reduce checkpoint group discovery p99 from 11400ms to under 500ms on a 50k-group cluster.
- Eliminate the describeConsumerGroups fan-out that thundering-herds the group coordinators during replication and failover.
- Keep offset translation correctness unchanged — no consumer may resume at an invalid offset on the target cluster.
- Make discovery cost scale with the number of *matched* groups, not the total number of groups in the source cluster.

**Not in scope**

- Changing the offset translation algorithm itself (OffsetSyncStore is correct and is not in scope).
- Improving general-purpose listConsumerGroups performance for unrelated callers.
- Redesigning MirrorSourceConnector topic replication — separate concern.
- Reducing replication lag on the data path — unrelated to this hot path.
- Changing MM2's groups / groups.exclude configuration semantics.

## Design Alternatives

### Alternative A: Broker-side reverse index + ListGroups v6 topic filter — **RECOMMENDED**

| | |
|---|---|
| **Proposed by** | `mirrormaker` |
| **Effort** | L |
| **Risk** | medium |
| **Blast radius** | group-coordinator, kafka-clients, kafka-broker, mirrormaker |
| **Reviewed by** | `group-coordinator`, `kafka-broker`, `kafka-clients` |

**Approach**

group-coordinator adds a `topicPartitionToGroups: Map<TopicPartition, Set<String>>` to GroupMetadataManager, maintained incrementally as members join, leave, and time out, and rebuilt from `__consumer_offsets` replay when a coordinator shard loads. kafka-clients adds an optional `topic_partitions` tagged field to ListGroupsRequest v6 and a matching `ListConsumerGroupsOptions.inTopicPartitions(...)`. MirrorCheckpointConnector.findConsumerGroups() then issues a single filtered Admin call and receives only the groups that actually consume the replicated partitions, making discovery O(matched) instead of O(n_groups). The broker-side index is not a wire change, so it can land and be validated ahead of the KIP vote.

**Pros**

- Fixes the root cause rather than the symptom — discovery becomes O(matched_groups), which is what the algorithm actually needs.
- Single source of truth: the index lives next to the group metadata it indexes, so there is no cross-process state to drift.
- Eliminates the describeConsumerGroups fan-out entirely, removing the thundering herd on the coordinators during checkpoint emission.
- Every Kafka user benefits — MM2, any DR deployment, and any operator tool that asks 'who is consuming this topic' has the same problem.
- The server-side index ships independently of the KIP, so operator pain is relieved without waiting on the Apache process.

**Cons**

- Requires a KIP with a roughly 6-week discussion-and-vote timeline for the public API surface, which no single contributor controls.
- Cross-team dependency on three teams: group-coordinator to implement the index, kafka-broker for routing and heap, kafka-clients to shepherd the protocol change.
- Adds ~14MB of broker heap per 50k-group cluster, which needs kafka-broker sign-off.
- Index rebuild on coordinator shard load costs ~800ms, during which the fallback path must be used.
- Behaviour for KIP-848 consumer groups versus classic groups must be specified, since they are described through different APIs.

### Alternative B: MM2-local materialized view built by tailing `__consumer_offsets` — _ruled out_

| | |
|---|---|
| **Proposed by** | `mirrormaker` |
| **Effort** | M |
| **Risk** | high |
| **Blast radius** | mirrormaker |
| **Reviewed by** | `group-coordinator`, `kafka-broker`, `kafka-clients` |

**Approach**

MirrorMaker builds and owns its own reverse index inside the MirrorCheckpointTask by consuming the compacted `__consumer_offsets` topic on the source cluster and maintaining `Map<TopicPartition, Set<String>>` in the Connect worker. No Kafka protocol change and no broker change at all — findConsumerGroups() reads its own local view instead of calling out to the coordinators. On task startup the view is populated by replaying the compacted topic from the beginning; afterwards it is updated from the tail.

**Pros**

- Zero cross-team dependency — MM2 can ship this alone, with no KIP, no broker change, and no coordination cost.
- Fastest path to relieving operator pain; weeks of calendar time saved versus the KIP route.
- No broker heap impact at all; the memory cost lands in the Connect worker where this team controls the JVM sizing.
- Fully reversible — an internal implementation detail of one connector with no public API surface.

**Cons**

- Creates a second copy of state the group coordinator already owns, which is a correctness liability: any bug or lag in the tailer means checkpointing against a stale subscription set.
- Committed offsets in `__consumer_offsets` are not the same thing as live subscriptions — a group that joined but has not committed yet is invisible to this view, which is a real correctness gap during rebalances.
- Cold-start replay of the compacted topic on every task restart adds startup latency and read bandwidth, and Connect restarts tasks often.
- Requires read access to an internal topic, which many operators restrict by ACL, so it will not work everywhere.
- MM2 would own a permanent piece of consumer-group logic that belongs to group-coordinator — an ownership boundary violation that costs us every time the group protocol evolves.

**Why ruled out:** Ruled out in round 1 — group-coordinator backed 'Broker-side reverse index + ListGroups v6 topic filter' instead.

### Alternative C: Batch describeConsumerGroups + parallel scan with early termination — _ruled out_

| | |
|---|---|
| **Proposed by** | `mirrormaker` |
| **Effort** | S |
| **Risk** | low |
| **Blast radius** | mirrormaker |
| **Reviewed by** | `group-coordinator`, `kafka-broker`, `kafka-clients` |

**Approach**

Keep the existing list-then-describe structure but fix the pathological parts of it: batch the describeConsumerGroups calls into batches of 500 instead of describing groups one coordinator round trip at a time, issue those batches in parallel across coordinator shards, and early-terminate once every replicated topic has been matched. Purely a change inside MirrorCheckpointConnector — no broker change, no protocol change, no new state anywhere.

**Pros**

- Entirely within MirrorMaker's ownership boundary; no other team needs to be involved and no approvals are required.
- Days of work rather than weeks, and very low risk — the semantics of the operation are unchanged, only the call pattern.
- No new state, so no drift, no rebuild, no staleness window.
- Useful regardless of which other alternative wins, since it makes the fallback path meaningfully faster.

**Cons**

- Still O(n_groups) asymptotically — realistic improvement is 11400ms down to roughly 1-2s, which misses the 500ms target by ~4x.
- Does not remove the load on the coordinators; batching reduces the round-trip count but each coordinator still reads every group's metadata.
- Degrades again as clusters grow — at 500k groups we are back where we started, so this buys time rather than solving the problem.
- Parallel fan-out could make the thundering herd worse if batch concurrency is tuned badly under failover conditions.

**Why ruled out:** Ruled out in round 1 — group-coordinator backed 'Broker-side reverse index + ListGroups v6 topic filter' instead.

## Recommendation

Broker-side reverse index + ListGroups v6 topic filter — group-coordinator adds a `topicPartitionToGroups: Map<TopicPartition, Set<String>>` to GroupMetadataManager, maintained incrementally as members join, leave, and time out, and rebuilt from `__consumer_offsets` replay when a coordinator shard loads. kafka-clients adds an optional `topic_partitions` tagged field to ListGroupsRequest v6 and a matching `ListConsumerGroupsOptions.inTopicPartitions(...)`. MirrorCheckpointConnector.findConsumerGroups() then issues a single filtered Admin call and receives only the groups that actually consume the replicated partitions, making discovery O(matched) instead of O(n_groups). The broker-side index is not a wire change, so it can land and be validated ahead of the KIP vote.

## Testing Strategy

- [mirrormaker] Unit: findConsumerGroups() returns identical group sets from the filtered path and the scan path for 1k randomized subscription fixtures.
- [mirrormaker] Unit: fallback to the scan when the broker does not advertise ListGroups v6.
- [mirrormaker] Integration: failover drill on a 50k-group staging cluster, asserting discovery p99 < 500ms.
- [mirrormaker] Correctness: no consumer resumes at an offset outside the valid target range, verified across 100 simulated failovers.
- [mirrormaker] Load: describeConsumerGroups call count during checkpoint emission must be zero on the filtered path.
- [group-coordinator] Unit: index contents match a brute-force scan of group metadata after 10k randomized join/leave/timeout sequences.
- [group-coordinator] Unit: filtered ListGroups with an empty topic_partitions filter returns byte-identical results to v5 (backward compatibility).
- [group-coordinator] Unit: index mutation on session-timeout eviction, not just explicit LeaveGroup.
- [group-coordinator] Unit: index is maintained identically for classic groups and KIP-848 consumer groups.
- [group-coordinator] Integration: coordinator shard load rebuilds the index from `__consumer_offsets` replay and converges to the same contents within 1 second for 50k groups.
- [group-coordinator] Integration: requests served from the scan fallback while a rebuild is in progress return correct results.
- [group-coordinator] Load: index memory growth stays within the configured cap under a group-churn workload, and falls back to scan when the cap is hit.
- [group-coordinator] Metrics: index size and hit rate are exported so shadow mode can be validated before the flag is enabled.
- [kafka-broker] Unit: handleListGroupsRequest fans out to every coordinator shard when the topic_partitions filter is present, and unions the per-shard results.
- [kafka-broker] Unit: a shard whose index is not yet built falls back to its own scan and the unioned result is still complete.
- [kafka-broker] Integration: a filtered ListGroups against a 50-shard cluster returns the same group set as the unfiltered scan, with p99 under 50ms.
- [kafka-broker] Integration: when one coordinator shard is unavailable the response marks the result partial rather than returning a silently short list.
- [kafka-broker] Load: broker heap growth from the index stays within the configured cap, and G1 pause p99 does not regress.
- [kafka-broker] Metrics: per-shard fan-out latency is exported so the slow-shard tail is observable.
- [kafka-clients] Protocol: ListGroupsRequest v6 round-trips through the generated serde with the tagged field both present and absent.
- [kafka-clients] Compatibility: a v5 client against a v6-capable broker receives byte-identical responses to the pre-change behavior.
- [kafka-clients] Compatibility: a v6 client against a v5-only broker receives UNSUPPORTED_VERSION and degrades to v5 without error.
- [kafka-clients] Interop: a cluster with both classic and KIP-848 consumer groups returns the documented behavior for the filter.
- [kafka-clients] Generated-code: ApiKeys.java max version for LIST_GROUPS reports 6 and ApiVersionsResponse advertises it correctly.
- [kafka-clients] AdminClient: inTopicPartitions() degrades to the unfiltered path when the broker does not advertise v6.

## Teams Involved

| Team | Role | Owns | Sign-off | Contribution |
|---|---|---|---|---|
| `mirrormaker` | owner | MirrorMaker 2: cross-cluster replication, checkpointing, MirrorCheckpointConnector.java | No | Diagnosed the O(n_groups) scan, quantified the impact, proposed the three alternatives, and drove the deliberation to a recommendation. |
| `group-coordinator` | implementer | GroupMetadataManager.java, GroupCoordinatorShard.java, group state | No | Alternative A: Eager in-memory HashMap maintained incrementally. It is the only option that delivers O(1) reads on a cold-start failover path, its memory cost is measured at 13.7MB rather than assumed, and it keeps consumer-group state inside the team that owns it. |
| `kafka-broker` | implementer | KafkaApis.scala request routing, broker heap budget, KRaft metadata | No | Alternative A: keep the existing scatter-gather and make each coordinator shard serve the filter from its local index. It requires no routing change, introduces no new failure mode, and the parallel fan-out means wall-clock latency stays inside the 50ms target. |
| `kafka-clients` | approver | Kafka wire protocol, ListGroupsRequest.json, AdminClient, KIP process | **Yes** | Alternative A: ListGroupsRequest v6 with an optional tagged topic_partitions field. It follows the KIP-518 and KIP-848 precedent, is wire-safe because ListGroups is flexible since v3, and lets the server-side index land ahead of the vote. |

## Execution Plan

1. group-coordinator adds the reverse index behind a feature flag, with metrics on index size and hit rate.
2. kafka-broker confirms the scatter-gather routing across all 50 __consumer_offsets coordinator shards and signs off on the heap budget.
3. kafka-clients drafts and posts the ListGroups v6 KIP for the public API surface, citing KIP-518 as prior art.
4. Once the KIP merges, MirrorCheckpointConnector switches to the filtered Admin call, keeping the full scan behind a config as fallback.
5. Validate discovery p99 on a 50k-group staging cluster across one full failover drill per region, then remove the fallback.

## Risks & Mitigations

- **Index drift** — the reverse index disagrees with live group metadata. Mitigation: run the scan in shadow mode for one week and alert on any mismatch before trusting the index.
- **Coordinator failover rebuild** — the index must be rebuilt from `__consumer_offsets` replay (~800ms for 50k groups). Mitigation: serve the scan path while a rebuild is in progress.
- **Broker heap growth** — ~14MB per 50k-group cluster. Mitigation: config-capped index size with a documented fallback to full scan when the cap is hit.
- **KIP timeline** — the Apache vote is outside any single contributor's control. Mitigation: the server-side index is not a wire change, so it lands independently of the KIP schedule.

## Rollout & Rollback

- Broker config `group.coordinator.topic.partition.index.enable` defaults off; enable per cluster starting with the smallest group counts.
- Shadow mode first: run the indexed lookup and the full scan in parallel, compare results, emit a mismatch metric, do not act on the index.
- MM2 side is gated by `checkpoints.group.discovery.use.filter`, which falls back to the scan when the broker does not advertise v6.
- Rollback is flipping either flag off — the scan path stays in the binary until the v6 API is in production everywhere.
- Bake for one full failover drill per region before enabling by default.

## Success Metrics

- Checkpoint group discovery p99 < 500ms on a 50k-group cluster (from 11400ms).
- Zero index/scan mismatches over a 7-day shadow-mode window.
- describeConsumerGroups call volume during checkpointing drops by >99%.
- No increase in consumer lag spikes attributable to failover.
- Group coordinator p99 request latency unchanged or improved during checkpoint emission.

## Decisions Requiring a Human

- kafka-clients: The ListGroups v6 KIP requires a sponsoring Apache Kafka PMC committer and 3 binding +1 votes on dev@kafka.apache.org. Securing a sponsor and shepherding that vote is an external organizational process that cannot be delegated to an agent. Note this gates the *public API* only — the broker-side index and MM2's internal fast path can land without it.

## Open Questions

- KIP-848 interaction: specify whether topic-partition filtering applies to consumer-protocol groups, classic groups, or both, and document the behaviour explicitly in the first version.
- Partial-result semantics for a fan-out where one or more coordinator shards error must be specified in the KIP, not left to the implementation.

---

## Appendix: Deliberation Record

| Round | From | To | Verdict | New concerns raised | Alternatives discussed |
|---|---|---|---|---|---|
| 1 | `mirrormaker` | `group-coordinator` | needs_changes | Your alternative B is not correct as specified: `__consumer_offsets` holds *committed offsets*, not live subscriptions. A group that has joined and been assigned partitions but has not committed yet is invisible in that view, so you would silently skip checkpointing it. That is a correctness bug, not a performance tradeoff.; Alternative C will not reach your target — batching describeConsumerGroups reduces round trips but each coordinator still reads every group's metadata, so you land around 1-2s.; The index costs ~13.7MB per 50k-group cluster in broker heap. That is acceptable to us but kafka-broker must sign off before it is enabled by default.; Coordinator shard load needs a ~800ms index rebuild from `__consumer_offsets` replay; we need the scan path retained as the fallback during that window. | Eager in-memory HashMap maintained incrementally; Lazy index computed on first query, with TTL and rebalance invalidation; Persist the reverse index as a new `__consumer_offsets` record type |
| 1 | `mirrormaker` | `kafka-broker` | needs_changes | Important correction to the design as proposed: a filtered ListGroups cannot be a single targeted lookup. Group→coordinator placement is `abs(group_id.hashCode()) % 50`, which has nothing to do with subscribed topics, so the groups consuming one topic are spread across all 50 shards. The request must still fan out to every shard. The index makes each shard's work O(1) instead of O(groups_on_shard) — that is the real win, and it is still worth it, but the fan-out does not go away and the design doc should say so.; Tail latency on the fan-out is set by the slowest shard, so a single G1 pause (currently p99 18ms) shows up in the caller's p99. Budget for that rather than assuming the index lookup cost.; Heap is acceptable but not free: 14MB against 2254MB headroom is 0.23% of total heap. We want the config-capped size with a documented fallback to scan when the cap is hit, not an unbounded map.; Per-shard errors must be surfaced individually. If one coordinator is unavailable the caller needs to know the result is partial rather than receiving a silently short list — MM2 checkpointing against a partial group set is the same correctness bug we are trying to avoid. | Scatter-gather across all coordinator shards, indexed per shard; Global topic→group index materialized in KRaft metadata; Co-locate groups on shards by subscribed topic |
| 1 | `mirrormaker` | `kafka-clients` | needs_changes | Must specify behavior when the topic_partitions filter is used against KIP-848 consumer groups as well as classic groups. This must be written into the KIP before it goes to a vote — reviewers will catch it otherwise and it costs a cycle.; The Apache vote itself is outside any contributor's control: 3 binding +1 votes are required and any reasoned -1 blocks. Do not build an operator commitment around the KIP date. | ListGroupsRequest v6 with an optional tagged topic_partitions field; New dedicated API key: ListGroupsForTopicPartition; AdminClient convenience method only, no wire change |
| 2 | `mirrormaker` | `group-coordinator` | needs_changes | One remaining ask: the feature flag must be per-broker config, not a cluster-wide switch, so operators can enable it on small clusters first and keep a blast-radius-limited rollout. | Eager in-memory HashMap maintained incrementally; Lazy index computed on first query, with TTL and rebalance invalidation; Persist the reverse index as a new `__consumer_offsets` record type |
| 2 | `mirrormaker` | `kafka-broker` | needs_changes | Confirming one thing before we sign off: the partial-result semantics need to be in the KIP, not just the implementation, because clients have to know how to interpret a response where some shards errored. | Scatter-gather across all coordinator shards, indexed per shard; Global topic→group index materialized in KRaft metadata; Co-locate groups on shards by subscribed topic |
| 2 | `mirrormaker` | `kafka-clients` | agreed | — | ListGroupsRequest v6 with an optional tagged topic_partitions field; New dedicated API key: ListGroupsForTopicPartition; AdminClient convenience method only, no wire change |
| 3 | `mirrormaker` | `group-coordinator` | agreed | — | Eager in-memory HashMap maintained incrementally; Lazy index computed on first query, with TTL and rebalance invalidation; Persist the reverse index as a new `__consumer_offsets` record type |
| 3 | `mirrormaker` | `kafka-broker` | agreed | — | Scatter-gather across all coordinator shards, indexed per shard; Global topic→group index materialized in KRaft metadata; Co-locate groups on shards by subscribed topic |
| 3 | `mirrormaker` | `kafka-clients` | agreed | — | ListGroupsRequest v6 with an optional tagged topic_partitions field; New dedicated API key: ListGroupsForTopicPartition; AdminClient convenience method only, no wire change |

**Questions asked, round by round**

- **Round 1, `mirrormaker` → `group-coordinator`:** I have 3 alternatives on the table (A: broker-side reverse index plus a ListGroups v6 filter, B: MM2-local materialized view off `__consumer_offsets`, C: batch and parallelize the existing scan). A needs you to own the index inside GroupMetadataManager. Is that implementable, what does it cost in heap and rebuild time, and do you see an implementation approach I have not considered? I am specifically worried that B has a correctness gap because committed offsets are not the same as live subscriptions — confirm or correct that.
  - Response: Alternative A is implementable and we will own it. Memory cost verified at 13.7MB total / 0.27MB per coordinator shard for 50k groups — under 1% of broker heap. Changes are scoped to the subscription-mutation paths in GroupMetadataManager, the filtered ListGroups handler, and a new TopicPartitionGroupIndex. We reject your B on correctness grounds (committed offsets are not live subscriptions) and your C on the target (still O(n_groups), lands at 1-2s). We have three implementation options for the index itself and recommend the eager in-memory HashMap. We consulted kafka-clients on the protocol surface. kafka-clients says: New KIP required — no existing KIP covers topic-partition scoped ListGroups. Closest precedent is KIP-518 (Allow listing consumer groups per state), which added states_filter in v4; KIP-848 then added types_filter in v5. Two accepted filter fields on this exact API is strong prior art — cite both. Recommended surface: ListGroupsRequest v6 with an optional tagged topic_partitions field. Compatibility check: PASS_WITH_NOTE. Timeline ~6 weeks to trunk. Critically: the broker-side index needs no protocol change, so the coordinator work can land behind a broker config immediately and MM2 can migrate to the public v6 API when the KIP merges — the performance fix is not gated on the vote.
- **Round 1, `mirrormaker` → `kafka-broker`:** A filtered ListGroups has to be served by the brokers. Groups for one topic can hash to any of the 50 `__consumer_offsets` partitions, so I need to understand how a filtered request routes: does it fan out to every coordinator shard, and if so does that undo the win? Also tell me whether ~14MB of extra heap per 50k-group cluster is acceptable, and whether any of this belongs in KRaft metadata instead.
  - Response: Routing is fine but the design's framing is wrong in one important way: the filtered call still fans out to all 50 coordinator shards, because group placement is by group-id hash and is unrelated to subscribed topics. Only 4 shards actually hold a match, but the broker cannot know which ones without asking. The index is still the right fix — it changes per-shard cost from a full metadata walk to a hash lookup, and shards are queried in parallel so wall-clock latency is one lookup plus aggregation. Heap at 14MB is 0.23% of total and acceptable with a config cap. We reject putting this in KRaft metadata (churn is ~32x the log's steady-state rate) and reject re-hashing group placement (breaks every client).
- **Round 1, `mirrormaker` → `kafka-clients`:** Does a topic-partition filter on ListGroups need a KIP, and is there existing prior art? I want to know whether the public API surface is a version bump on ListGroups, a brand-new API key, or something else entirely. Give me the alternatives and the timeline for each, and flag anything about KIP-848 consumer groups that would make this change wrong.
  - Response: New KIP required — no existing KIP covers topic-partition scoped ListGroups. Closest precedent is KIP-518 (Allow listing consumer groups per state), which added states_filter in v4; KIP-848 then added types_filter in v5. Two accepted filter fields on this exact API is strong prior art — cite both. Recommended surface: ListGroupsRequest v6 with an optional tagged topic_partitions field. Compatibility check: PASS_WITH_NOTE. Timeline ~6 weeks to trunk. Critically: the broker-side index needs no protocol change, so the coordinator work can land behind a broker config immediately and MM2 can migrate to the public v6 API when the KIP merges — the performance fix is not gated on the vote.
- **Round 2, `mirrormaker` → `group-coordinator`:** Given your round-1 answer, I want to converge on A. Which of your implementation sub-options do you recommend, and what do kafka-broker and kafka-clients say about routing and the protocol surface? Also confirm the rebuild-on-load cost is acceptable with the scan path as fallback.
  - Response: Agreed on alternative A with the eager in-memory HashMap implementation. Rebuild-on-load at ~800ms is acceptable given the scan path stays as fallback behind the same flag. We will add index size and hit-rate metrics to the coordinator metrics group so shadow mode is verifiable. Requesting a per-broker config rather than a cluster-wide switch.
- **Round 2, `mirrormaker` → `kafka-broker`:** Confirm the scatter-gather routing across all coordinator shards is the right shape and that it still hits my latency target, and that the heap budget is signed off with the config-capped fallback.
  - Response: Agreed on alternative A: scatter-gather unchanged, per-shard indexed lookup, results unioned. Expected p99 is comfortably inside the 50ms target since the shards are queried in parallel and each does a hash lookup. Heap budget signed off at 14MB with the config cap and scan fallback. Asking that partial-result semantics be specified in the KIP rather than left to the implementation.
- **Round 2, `mirrormaker` → `kafka-clients`:** Confirm: can the broker-side index land and be validated ahead of the KIP vote, with MM2 migrating to the public v6 API once it merges, without creating a compatibility problem we cannot undo?
  - Response: New KIP required — no existing KIP covers topic-partition scoped ListGroups. Closest precedent is KIP-518 (Allow listing consumer groups per state), which added states_filter in v4; KIP-848 then added types_filter in v5. Two accepted filter fields on this exact API is strong prior art — cite both. Recommended surface: ListGroupsRequest v6 with an optional tagged topic_partitions field. Compatibility check: PASS_WITH_NOTE. Timeline ~6 weeks to trunk. Critically: the broker-side index needs no protocol change, so the coordinator work can land behind a broker config immediately and MM2 can migrate to the public v6 API when the KIP merges — the performance fix is not gated on the vote.
- **Round 3, `mirrormaker` → `group-coordinator`:** Final round: confirm you are agreed on A with the feature-flag rollout and shadow-mode validation, and that your test requirements are captured.
  - Response: Agreed and settled from our side. Alternative A, eager in-memory index in TopicPartitionGroupIndex, per-broker config, scan retained as fallback, shadow mode with a mismatch metric before we trust the index. Our test requirements are captured below. No further concerns.
- **Round 3, `mirrormaker` → `kafka-broker`:** Final round: confirm routing and heap are agreed, and that your test requirements for the fan-out path are captured.
  - Response: Agreed and settled from our side. Routing unchanged, per-shard indexed lookup, parallel fan-out, union of results with per-shard errors surfaced. Heap capped by config with scan fallback. Our test requirements for the fan-out and partial-failure paths are captured below.
- **Round 3, `mirrormaker` → `kafka-clients`:** Final round: confirm the v6 tagged-field approach is agreed, and state exactly what needs Apache PMC authority versus what we can decide ourselves.
  - Response: New KIP required — no existing KIP covers topic-partition scoped ListGroups. Closest precedent is KIP-518 (Allow listing consumer groups per state), which added states_filter in v4; KIP-848 then added types_filter in v5. Two accepted filter fields on this exact API is strong prior art — cite both. Recommended surface: ListGroupsRequest v6 with an optional tagged topic_partitions field. Compatibility check: PASS_WITH_NOTE. Timeline ~6 weeks to trunk. Critically: the broker-side index needs no protocol change, so the coordinator work can land behind a broker config immediately and MM2 can migrate to the public v6 API when the KIP merges — the performance fix is not gated on the vote.

### Codepaths cited by the owning agent

- `MirrorCheckpointConnector.java`
- `MirrorCheckpointTask.java`
- `connect/mirror/src/main/java/org/apache/kafka/connect/mirror/`
