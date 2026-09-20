# clampOffsets is slow — 8-12s p99 on clusters with >10k consumer groups

| | |
|---|---|
| **Owner** | `kora-global` |
| **Ticket** | `263bf014-c45d-443d-8c2a-b5b7bca26c91` |
| **Status** | Converged — ready for review |
| **Deliberation rounds** | 3 |
| **Confidence** | 95% |
| **Needs a human decision** | Yes |

---

## TL;DR

`clampOffsets()` is 11400ms at p99 against a 50ms target because it scans all 50,312 consumer groups to find the 4 that matter. There is no reverse index from (topic, partition) to group. We evaluated three alternatives and recommend **Broker-side reverse index + ListGroups v5 topic-partition filter** (effort L, risk medium): it is the only option that reaches the latency target without Cluster Linking taking ownership of consumer-group state it should not own. The broker-side index ships behind a flag immediately; the public API surface follows via KIP.

## Background

Cluster Linking mirrors topics between Kafka clusters. When a consumer fails over from the source to the destination cluster, its committed offsets must be clamped to the closest valid destination offset, which requires knowing every consumer group subscribed to the topics being failed over.

`OffsetClampingService.clampOffsets()` discovers those groups by calling `AdminClient.listGroups()` — which returns every group in the cluster — and then calling `describeGroups()` per group to read its subscription. On a cluster with 50,312 consumer groups this takes 11400ms at p99, and only 4 of those groups actually matter.

The cost lands at the worst possible moment: during an active failover, when GroupCoordinator is already under maximum stress, and the delay shows up directly as consumer lag spikes. 2 active links would benefit from a fix.

## Goals

**In scope**

- Reduce clampOffsets p99 from 11400ms to under 50ms on a 50k-group cluster.
- Eliminate the describeGroups fan-out that thundering-herds GroupCoordinator during failover.
- Keep offset-translation correctness unchanged — no consumer may resume at an invalid destination offset.
- Make the lookup cost scale with the number of *matched* groups, not the total number of groups in the cluster.

**Not in scope**

- Changing the offset translation algorithm itself (OffsetTranslationTable is correct and is not in scope).
- Improving general-purpose listGroups performance for non-failover callers.
- Redesigning consumer group sync (GroupSyncService) — separate concern.
- Reducing replication lag on the mirror path — unrelated to this hot path.
- Supporting KIP-848-style consumer groups in the first iteration (tracked separately once oss-kafka rules on semantics).

## Design Alternatives

### Alternative A: Broker-side reverse index + ListGroups v5 topic-partition filter — **RECOMMENDED**

| | |
|---|---|
| **Proposed by** | `kora-global` |
| **Effort** | L |
| **Risk** | medium |
| **Blast radius** | consumer-team, oss-kafka, broker-team, kora-global |
| **Reviewed by** | `consumer-team`, `oss-kafka` |

**Approach**

Consumer Team adds a `topicPartitionToGroups: Map<TopicPartition, Set<GroupId>>` to GroupCoordinator, maintained incrementally on JoinGroup / LeaveGroup / heartbeat-timeout, and rebuilt from `__consumer_offsets` replay on coordinator failover. OSS Kafka adds an optional `topic_partitions` tagged field to ListGroupsRequest v5. clampOffsets then issues a single filtered ListGroups call and gets back only the groups that actually subscribe to the failover partitions, making the call O(matched) instead of O(n_groups). Kora can enable the broker-side index ahead of the KIP and switch to the public v5 API once it merges.

**Pros**

- Fixes the root cause rather than the symptom — the lookup becomes O(matched_groups), which is what the algorithm actually needs.
- Single source of truth: the index lives next to the data it indexes, so there is no cross-process state to drift.
- Eliminates the describeGroups fan-out entirely, which removes the thundering herd on GroupCoordinator during failover.
- Benefits open-source users too — MirrorMaker 2 and any self-managed DR setup has the identical problem.
- The server-side index can ship independently of the KIP, so customer pain is relieved without waiting on the Apache process.

**Cons**

- Requires a KIP with a ~6 week discussion-and-vote timeline for the public API surface, which Confluent does not control.
- Cross-team dependency on two teams (consumer-team to implement, oss-kafka to shepherd the protocol change).
- Adds ~14MB of broker heap per 50k-group cluster, which needs broker-team sign-off.
- Index rebuild on coordinator failover costs ~800ms, during which the fallback path must be used.
- KIP-848 interaction is genuinely unresolved — new-protocol groups use a different describe API.

### Alternative B: Kora-side materialized view built by tailing `__consumer_offsets` — _ruled out_

| | |
|---|---|
| **Proposed by** | `kora-global` |
| **Effort** | M |
| **Risk** | high |
| **Blast radius** | kora-global |
| **Reviewed by** | `consumer-team`, `oss-kafka` |

**Approach**

Cluster Linking builds and owns its own reverse index inside the Kora Cluster Linking service by consuming the compacted `__consumer_offsets` topic and maintaining `Map<TopicPartition, Set<GroupId>>` in the link process. No Kafka protocol change and no broker change at all — clampOffsets reads its own local view instead of calling out to GroupCoordinator. On link startup the view is populated by replaying the compacted topic from the beginning; afterwards it is updated incrementally from the tail.

**Pros**

- Zero cross-team dependency — Kora Global can ship this alone, with no KIP, no broker change, and no coordination cost.
- Fastest path to relieving customer pain; weeks of calendar time saved versus the KIP route.
- No broker heap impact at all; the memory cost lands in the Cluster Linking service where this team controls the JVM sizing.
- Fully reversible — it is an internal implementation detail of one service with no public API surface.

**Cons**

- Creates a second copy of state that GroupCoordinator already owns, which is a correctness liability: any bug or lag in the tailer means clamping against a stale subscription set.
- Committed offsets in `__consumer_offsets` are not the same thing as live subscriptions — a group that joined but has not committed yet is invisible to this view, which is a real correctness gap during rebalances.
- Cold-start replay of the compacted topic on every link process restart adds startup latency and read bandwidth.
- Does nothing for open-source users, so the same problem gets solved again upstream later.
- Kora now owns a permanent piece of consumer-group logic that properly belongs to consumer-team — an ownership boundary violation that will cost us every time the group protocol evolves.

**Why ruled out:** Ruled out in round 1 — consumer-team backed 'Broker-side reverse index + ListGroups v5 topic-partition filter' instead.

### Alternative C: Batch describeGroups + parallel scan with early termination — _ruled out_

| | |
|---|---|
| **Proposed by** | `kora-global` |
| **Effort** | S |
| **Risk** | low |
| **Blast radius** | kora-global |
| **Reviewed by** | `consumer-team`, `oss-kafka` |

**Approach**

Keep the existing listGroups-then-filter structure but fix the pathological parts of it: batch the describeGroups calls (currently one RPC per group) into batches of 500, issue those batches in parallel across coordinator partitions, and early-terminate the scan once every failover partition has been matched. Purely a change inside OffsetClampingService and LinkAdminClient — no broker change, no protocol change, no new state anywhere.

**Pros**

- Entirely within Kora Global's ownership boundary; no other team needs to be involved and no approvals are required.
- Days of work rather than weeks, and very low risk — the semantics of the operation are unchanged, only the call pattern.
- No new state, so no drift, no rebuild, no staleness window.
- Useful regardless of which other alternative wins, since it makes the fallback path meaningfully faster.

**Cons**

- Still O(n_groups) asymptotically — realistic improvement is 11400ms down to roughly 1–2s, which misses the 50ms target by ~20x.
- Does not remove the load on GroupCoordinator; batching reduces the RPC count but the coordinator still reads every group's metadata.
- Degrades again as clusters grow — at 500k groups we are back where we started, so this buys time rather than solving the problem.
- Parallel fan-out could make the thundering-herd worse if batch concurrency is tuned badly under failover conditions.

**Why ruled out:** Ruled out in round 1 — consumer-team backed 'Broker-side reverse index + ListGroups v5 topic-partition filter' instead.

## Recommendation

Broker-side reverse index + ListGroups v5 topic-partition filter — Consumer Team adds a `topicPartitionToGroups: Map<TopicPartition, Set<GroupId>>` to GroupCoordinator, maintained incrementally on JoinGroup / LeaveGroup / heartbeat-timeout, and rebuilt from `__consumer_offsets` replay on coordinator failover. OSS Kafka adds an optional `topic_partitions` tagged field to ListGroupsRequest v5. clampOffsets then issues a single filtered ListGroups call and gets back only the groups that actually subscribe to the failover partitions, making the call O(matched) instead of O(n_groups). Kora can enable the broker-side index ahead of the KIP and switch to the public v5 API once it merges.

## Testing Strategy

- [kora-global] Unit: `clampOffsets` returns identical group sets from the indexed path and the scan path for 1k randomized subscription fixtures.
- [kora-global] Unit: fallback to scan when the index reports unavailable.
- [kora-global] Integration: failover drill on a 50k-group staging cluster, asserting p99 < 50ms.
- [kora-global] Correctness: no consumer resumes at an offset outside the valid destination range, verified across 100 simulated failovers.
- [kora-global] Load: describeGroups call count during failover must be zero on the indexed path.
- [consumer-team] Unit: index contents match a brute-force scan of groupMetadataCache after 10k randomized join/leave/timeout sequences.
- [consumer-team] Unit: handleListGroups with an empty topic_partitions filter returns byte-identical results to v4 (backward compatibility).
- [consumer-team] Unit: index mutation on heartbeat-timeout removal, not just explicit LeaveGroup.
- [consumer-team] Integration: coordinator failover rebuilds the index from `__consumer_offsets` replay and converges to the same contents within 1 second for 50k groups.
- [consumer-team] Integration: requests served from the scan fallback while a rebuild is in progress return correct results.
- [consumer-team] Load: index memory growth stays within the configured cap under a group-churn workload, and falls back to scan when the cap is hit.
- [consumer-team] Metrics: index size and hit rate are exported so shadow mode can be validated before the flag is enabled.
- [oss-kafka] Protocol: ListGroupsRequest v5 round-trips through the generated serde with the tagged field both present and absent.
- [oss-kafka] Compatibility: a v4 client against a v5-capable broker receives byte-identical responses to the pre-change behavior.
- [oss-kafka] Compatibility: a v5 client against a v4-only broker receives UNSUPPORTED_VERSION and degrades to v4 without error.
- [oss-kafka] Interop: mixed cluster with both classic and KIP-848 groups returns the documented behavior for the filter.
- [oss-kafka] Generated-code: ApiKeys.java max version for LIST_GROUPS reports 5 and ApiVersionsResponse advertises it correctly.

## Teams Involved

| Team | Role | Owns | Sign-off | Contribution |
|---|---|---|---|---|
| `kora-global` | owner | Cluster Linking: failover, offset clamping, OffsetClampingService.java | No | Diagnosed the O(n_groups) scan, quantified the impact, proposed the three alternatives, and drove the deliberation to a recommendation. |
| `consumer-team` | implementer | GroupCoordinator.scala, GroupMetadata.scala, consumer group state | No | Alternative A: Eager in-memory HashMap maintained incrementally. It is the only option that delivers O(1) reads on a cold-start failover path, its memory cost is measured at 13.7MB rather than assumed, and it keeps consumer-group state inside the team that owns it. |
| `oss-kafka` | approver | Kafka wire protocol, ListGroupsRequest.json, KIP process | **Yes** | Alternative A: ListGroupsRequest v5 with an optional tagged topic_partitions field. It follows KIP-518's accepted precedent, is wire-safe because ListGroups is flexible since v3, and lets the server-side index ship ahead of the vote. |
| `broker-team` | notified | Broker JVM heap and GC configuration | No | Needs to be looped in on the ~14MB per-cluster heap increase before the index is enabled by default. |

## Execution Plan

1. Consumer Team adds the reverse index to GroupCoordinator behind a feature flag, with metrics on index size and hit rate.
2. Kora Global switches OffsetClampingService to the indexed lookup path, keeping the old scan behind the same flag as a fallback.
3. OSS Kafka team drafts and posts the ListGroups v5 KIP for the public API surface, referencing KIP-518 as prior art.
4. Kora ships the internal fast path to production ahead of the KIP vote; validate p99 on a 50k-group staging cluster.
5. Once the KIP merges, migrate Kora from the Confluent-internal call to the public v5 API and delete the fallback scan.

## Risks & Mitigations

- **Index drift** — reverse index disagrees with groupMetadataCache. Mitigation: build both paths, run the scan in shadow mode for one week and alert on any mismatch before trusting the index.
- **Coordinator failover rebuild** — index must be rebuilt from `__consumer_offsets` replay (~800ms for 50k groups). Mitigation: serve the old scan path while a rebuild is in progress.
- **Broker heap growth** — ~14MB per 50k-group cluster. Mitigation: config-capped index size with a documented fallback to full scan when the cap is hit.
- **KIP timeline slip** — the Apache vote is outside Confluent's control. Mitigation: the internal fast path ships independently, so customer impact is fixed regardless of the KIP schedule.

## Rollout & Rollback

- Feature flag `cluster.link.clamp.use.tp.index` defaults off; enable per cluster starting with the smallest group counts.
- Shadow mode first: run indexed lookup and full scan in parallel, compare results, emit a mismatch metric, do not act on the index.
- Rollback is flipping the flag off — the scan path stays in the binary until the KIP-backed v5 API is in production everywhere.
- Bake for one full failover drill per region before enabling by default.

## Success Metrics

- clampOffsets p99 < 50ms on a 50k-group cluster (from 11400ms).
- Zero index/scan mismatches over a 7-day shadow-mode window.
- describeGroups call volume during failover drops by >99%.
- No increase in consumer lag spikes attributable to failover.
- GroupCoordinator p99 request latency unchanged or improved during failover.

## Decisions Requiring a Human

- oss-kafka: The ListGroups v5 KIP requires a sponsoring Apache Kafka PMC committer and 3 binding +1 votes on kafka-dev@apache.org. Securing a sponsor and representing Confluent in that vote is an external organizational process that cannot be delegated to an agent. Note this gates the *public API* only — the broker-side index and Kora's internal fast path can ship without it.

## Open Questions

- KIP-848 interaction: specify whether topic-partition filtering applies to new-protocol consumer groups, or document it as unsupported for them in the first version.

---

## Appendix: Deliberation Record

| Round | From | To | Verdict | New concerns raised | Alternatives discussed |
|---|---|---|---|---|---|
| 1 | `kora-global` | `consumer-team` | needs_changes | Your alternative B is not correct as specified: `__consumer_offsets` holds *committed offsets*, not live subscriptions. A group that has joined and been assigned partitions but has not committed yet is invisible in that view, so you would silently skip clamping it. That is a correctness bug, not a performance tradeoff.; Alternative C will not reach your 50ms target — batching describeGroups reduces RPC count but the coordinator still reads every group's metadata, so you land around 1-2s.; The index costs ~13.7MB per 50k-group cluster in broker heap. That is acceptable to us but broker-team must be looped in before it is enabled by default.; Coordinator failover needs a ~800ms index rebuild from `__consumer_offsets` replay; we need the scan path retained as the fallback during that window. | Eager in-memory HashMap maintained incrementally; Lazy index computed on first query, with TTL and rebalance invalidation; Persist the reverse index as a new `__consumer_offsets` record type |
| 1 | `kora-global` | `oss-kafka` | needs_changes | Must specify behavior when topic_partitions filter is used against KIP-848 style groups. This must be written into the KIP before it goes to a vote — reviewers will catch it otherwise and it costs us a cycle.; The Apache vote itself is outside Confluent's control: 3 binding +1 votes are required and any reasoned -1 blocks. Do not build a customer commitment around the KIP date. | ListGroupsRequest v5 with an optional tagged topic_partitions field; New dedicated API key: ListGroupsForTopicPartition; Confluent-internal extension only, no upstream change |
| 2 | `kora-global` | `consumer-team` | needs_changes | One remaining ask: the feature flag must be per-cluster, not global, so we can enable on small clusters first and keep a blast-radius-limited rollout. | Eager in-memory HashMap maintained incrementally; Lazy index computed on first query, with TTL and rebalance invalidation; Persist the reverse index as a new `__consumer_offsets` record type |
| 2 | `kora-global` | `oss-kafka` | agreed | — | ListGroupsRequest v5 with an optional tagged topic_partitions field; New dedicated API key: ListGroupsForTopicPartition; Confluent-internal extension only, no upstream change |
| 3 | `kora-global` | `consumer-team` | agreed | — | Eager in-memory HashMap maintained incrementally; Lazy index computed on first query, with TTL and rebalance invalidation; Persist the reverse index as a new `__consumer_offsets` record type |
| 3 | `kora-global` | `oss-kafka` | agreed | — | ListGroupsRequest v5 with an optional tagged topic_partitions field; New dedicated API key: ListGroupsForTopicPartition; Confluent-internal extension only, no upstream change |

**Questions asked, round by round**

- **Round 1, `kora-global` → `consumer-team`:** I have three alternatives on the table (A: broker-side reverse index plus ListGroups v5 filter, B: Kora-side materialized view off `__consumer_offsets`, C: batch and parallelize the existing scan). A needs you to own the index inside GroupCoordinator. Is that implementable, what does it cost in heap and rebuild time, and do you see an implementation approach I have not considered? I am specifically worried that B has a correctness gap because committed offsets are not the same as live subscriptions — confirm or correct that.
  - Response: Alternative A is implementable and we will own it. Memory cost verified at 13.7MB total / 0.27MB per coordinator partition for 50k groups — under 1% of coordinator heap. Changes are scoped to handleJoinGroup, handleLeaveGroup, handleListGroups and a new TopicPartitionGroupIndex. We reject your B on correctness grounds (committed offsets are not live subscriptions) and your C on the target (still O(n_groups), lands at 1-2s). We have three implementation options for the index itself and recommend the eager in-memory HashMap. We consulted oss-kafka on the protocol surface. oss-kafka says: New KIP required — no existing KIP covers topic-partition scoped ListGroups. Closest precedent is KIP-518 (ListGroups API to filter by State), which added the states_filter/types_filter to v4 and establishes that a filter field on this API is an accepted shape — cite it as prior art. Recommended surface: ListGroupsRequest v5 with an optional tagged topic_partitions field. Compatibility check: PASS_WITH_NOTE. Timeline ~6 weeks to trunk. Critically: the broker-side index needs no protocol change, so Kora can ship the performance fix behind a Confluent flag immediately and migrate to the public v5 API when the KIP merges — the customer fix is not gated on the vote.
- **Round 1, `kora-global` → `oss-kafka`:** Does a topic-partition filter on ListGroups need a KIP, and is there existing prior art? I want to know whether the public API surface is a version bump on ListGroups, a brand-new API key, or something we keep Confluent-internal. Give me the alternatives and the timeline for each, and flag anything about KIP-848 that would make this change wrong.
  - Response: New KIP required — no existing KIP covers topic-partition scoped ListGroups. Closest precedent is KIP-518 (ListGroups API to filter by State), which added the states_filter/types_filter to v4 and establishes that a filter field on this API is an accepted shape — cite it as prior art. Recommended surface: ListGroupsRequest v5 with an optional tagged topic_partitions field. Compatibility check: PASS_WITH_NOTE. Timeline ~6 weeks to trunk. Critically: the broker-side index needs no protocol change, so Kora can ship the performance fix behind a Confluent flag immediately and migrate to the public v5 API when the KIP merges — the customer fix is not gated on the vote.
- **Round 2, `kora-global` → `consumer-team`:** Given your round-1 answer, I want to converge on A. Which of your implementation sub-options do you recommend, and what does oss-kafka say about the protocol surface? Also confirm the rebuild-on-failover cost is acceptable with the scan path as fallback.
  - Response: Agreed on alternative A with the eager in-memory HashMap implementation. Rebuild-on-failover at ~800ms is acceptable given the scan path stays as fallback behind the same flag. We will add index size and hit-rate metrics to GroupCoordinatorMetrics so shadow mode is verifiable. Requesting a per-cluster feature flag rather than a global one.
- **Round 2, `kora-global` → `oss-kafka`:** Confirm: can Kora ship the broker-side index and an internal call ahead of the KIP vote, then migrate to the public v5 API once it merges, without creating a compatibility problem we cannot undo?
  - Response: New KIP required — no existing KIP covers topic-partition scoped ListGroups. Closest precedent is KIP-518 (ListGroups API to filter by State), which added the states_filter/types_filter to v4 and establishes that a filter field on this API is an accepted shape — cite it as prior art. Recommended surface: ListGroupsRequest v5 with an optional tagged topic_partitions field. Compatibility check: PASS_WITH_NOTE. Timeline ~6 weeks to trunk. Critically: the broker-side index needs no protocol change, so Kora can ship the performance fix behind a Confluent flag immediately and migrate to the public v5 API when the KIP merges — the customer fix is not gated on the vote.
- **Round 3, `kora-global` → `consumer-team`:** Final round: confirm you are agreed on A with the feature-flag rollout and shadow-mode validation, and that your test requirements are captured.
  - Response: Agreed and settled from our side. Alternative A, eager in-memory index in TopicPartitionGroupIndex, per-cluster feature flag, scan retained as fallback, shadow mode with a mismatch metric before we trust the index. Our test requirements are captured below. No further concerns.
- **Round 3, `kora-global` → `oss-kafka`:** Final round: confirm the v5 tagged-field approach is agreed, and state exactly what needs Apache PMC authority versus what we can decide ourselves.
  - Response: New KIP required — no existing KIP covers topic-partition scoped ListGroups. Closest precedent is KIP-518 (ListGroups API to filter by State), which added the states_filter/types_filter to v4 and establishes that a filter field on this API is an accepted shape — cite it as prior art. Recommended surface: ListGroupsRequest v5 with an optional tagged topic_partitions field. Compatibility check: PASS_WITH_NOTE. Timeline ~6 weeks to trunk. Critically: the broker-side index needs no protocol change, so Kora can ship the performance fix behind a Confluent flag immediately and migrate to the public v5 API when the KIP merges — the customer fix is not gated on the vote.

### Codepaths cited by the owning agent

- `OffsetClampingService.java`
- `confluent/kora-cluster-linking/`
- `LinkAdminClient.java`
