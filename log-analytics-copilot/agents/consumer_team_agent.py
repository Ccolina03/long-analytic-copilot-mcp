"""
Consumer Team SME Agent — Principal Engineer, GroupCoordinator.

Domain: Consumer groups, GroupCoordinator, offset storage, rebalancing.
Runbook: runbooks/consumer-team-sme.md

Tools
-----
  get_group_state(group_id)
  get_groups_for_topic_partition(topic, partition)
  get_offset_storage_schema()
  estimate_index_memory_cost(group_count, avg_subscriptions_per_group)
  get_rebalance_history(topic, last_minutes=60)

Behaviour
---------
When kora-global consults it, this agent does real diligence: it validates the
memory cost with its own tool, corrects kora-global's alternative B on a
genuine correctness point (committed offsets are not live subscriptions), and
proposes three implementation alternatives for the index itself.  It consults
oss-kafka on its own initiative about the protocol surface.  It moves to
'agreed' only once its concerns have been folded in.
"""

from __future__ import annotations

from typing import Any

from agents.base_agent import SMEAgentBase, tool
from proto.sme_agents import DesignAlternative, ImpactRequest, ImpactResponse, Ticket


class ConsumerTeamAgent(SMEAgentBase):
    AGENT_NAME = "consumer-team"
    DOMAIN = "Consumer groups, GroupCoordinator, offset storage, rebalancing"
    OWNS = [
        "apache/kafka/core/src/main/scala/kafka/coordinator/group/",
        "GroupCoordinator.scala",
        "GroupMetadata.scala",
        "GroupCoordinatorAdapter.scala",
        "GroupCoordinatorConfig.scala",
        "confluent/kora-group-coordinator/",
        "KoraGroupCoordinator.java",
        "KoraGroupMetadataManager.java",
        "TopicPartitionGroupIndex.java",
    ]

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    @tool("get_group_state")
    def get_group_state(self, group_id: str) -> dict:
        """Return the current in-memory state of a consumer group."""
        if not group_id:
            raise ValueError("group_id is required")
        return {
            "group_id": group_id,
            "state": "Stable",
            "member_count": 6,
            "subscribed_topics": ["orders", "payments", "refunds"],
            "coordinator_broker": 12,
            "last_rebalance_ms_ago": 840000,
        }

    @tool("get_groups_for_topic_partition")
    def get_groups_for_topic_partition(self, topic: str, partition: int) -> dict:
        """Return groups subscribed to (topic, partition).

        Shows the current slow-scan path; will use the index once it exists.
        """
        if not topic:
            raise ValueError("topic is required")
        return {
            "topic": topic,
            "partition": partition,
            "method": "full_scan",
            "groups": [
                "billing-consumer",
                "analytics-consumer",
                "fraud-detector",
                "audit-logger",
            ],
            "duration_ms": 9100,
            "groups_scanned": 50312,
            "note": "index not available — fell back to full scan",
        }

    @tool("get_offset_storage_schema")
    def get_offset_storage_schema(self) -> dict:
        """Return the __consumer_offsets key/value schema."""
        return {
            "key_format": "(group_id: string, topic: string, partition: int16)",
            "value_format": "(offset: int64, metadata: string, commit_timestamp: int64)",
            "num_partitions": 50,
            "total_records_sample_cluster": 2_400_000,
            "coordinator_partition_formula": "abs(group_id.hashCode()) % 50",
        }

    @tool("estimate_index_memory_cost")
    def estimate_index_memory_cost(
        self, group_count: int, avg_subscriptions_per_group: int
    ) -> dict:
        """Calculate projected memory overhead for the inverted index."""
        if group_count <= 0:
            raise ValueError("group_count must be > 0")
        if avg_subscriptions_per_group <= 0:
            raise ValueError("avg_subscriptions_per_group must be > 0")
        entries = group_count * avg_subscriptions_per_group
        bytes_per_entry = 36
        total_bytes = entries * bytes_per_entry
        total_mb = total_bytes / (1024 * 1024)
        per_coordinator_mb = total_mb / 50
        return {
            "group_count": group_count,
            "avg_subscriptions": avg_subscriptions_per_group,
            "entries": entries,
            "bytes_per_entry": bytes_per_entry,
            "total_mb": round(total_mb, 1),
            "per_coordinator_mb": round(per_coordinator_mb, 2),
            "assessment": (
                "acceptable — p99 Kora cluster uses < 1% of coordinator heap"
                if total_mb < 100 else
                "WARNING — may require broker JVM heap increase"
            ),
        }

    @tool("get_rebalance_history")
    def get_rebalance_history(self, topic: str, last_minutes: int = 60) -> dict:
        """Return rebalance events for groups subscribed to ``topic``."""
        if not topic:
            raise ValueError("topic is required")
        return {
            "topic": topic,
            "window_minutes": last_minutes,
            "rebalance_count": 14,
            "avg_rebalance_ms": 340,
            "groups_affected": ["billing-consumer", "analytics-consumer"],
            "triggers": {"member_join": 8, "heartbeat_timeout": 4, "member_leave": 2},
        }

    # ------------------------------------------------------------------
    # Implementation alternatives for the index itself
    # ------------------------------------------------------------------

    def _index_alternatives(self, mem_cost: dict, rebalances: dict) -> list[DesignAlternative]:
        """Three ways to actually build the reverse index inside GroupCoordinator."""
        return [
            DesignAlternative(
                label="A",
                name="Eager in-memory HashMap maintained incrementally",
                proposed_by=self.AGENT_NAME,
                approach=(
                    "Add `topicPartitionToGroups: Map<TopicPartition, Set<GroupId>>` "
                    "alongside the existing `groupMetadataCache` in GroupCoordinator. "
                    "Mutate it in `handleJoinGroup()` (line 312–401) when a member's "
                    "subscription is set, and in `handleLeaveGroup()` (line 534–612) plus "
                    "the heartbeat-timeout path when membership drops. On coordinator "
                    "failover, rebuild by replaying `__consumer_offsets` for the owned "
                    "partitions. `handleListGroups()` (line 702–798) checks the new filter "
                    "field and serves from the index when present. New file "
                    "`TopicPartitionGroupIndex.java` holds the structure and its metrics."
                ),
                pros=[
                    f"O(1) reads — exactly what the failover path needs.",
                    f"Memory cost is measured, not guessed: {mem_cost['total_mb']}MB total "
                    f"for {mem_cost['group_count']:,} groups at "
                    f"{mem_cost['avg_subscriptions']} subscriptions each, which is "
                    f"{mem_cost['per_coordinator_mb']}MB per coordinator partition.",
                    f"Low GC pressure — entries mutate only on join/leave, and the measured "
                    f"rebalance rate is {rebalances['rebalance_count']} per hour for a busy "
                    f"topic, so churn is nowhere near hot-path frequency.",
                    "The index is derived from live in-memory membership, so it reflects "
                    "actual subscriptions rather than committed offsets.",
                    "Simple enough to reason about correctness by inspection, which matters "
                    "for code every Kafka cluster in the world runs.",
                ],
                cons=[
                    "Rebuild on coordinator failover costs roughly 800ms for 50k groups; "
                    "requests during that window must fall back to the scan.",
                    "Memory is proportional to *all* subscriptions, including topics nobody "
                    "ever queries by partition.",
                    "Unbounded if a pathological workload churns groups, so it needs a "
                    "config-capped size with a documented fallback.",
                ],
                effort="M",
                risk="low",
                blast_radius=["consumer-team", "broker-team"],
            ),
            DesignAlternative(
                label="B",
                name="Lazy index computed on first query, with TTL and rebalance invalidation",
                proposed_by=self.AGENT_NAME,
                approach=(
                    "Do not maintain an index eagerly. On the first "
                    "`listGroups(topic_partitions=...)` call for a given topic-partition, "
                    "compute the matching group set by scanning `groupMetadataCache` once, "
                    "then cache the result with a TTL. Invalidate the cache entry whenever "
                    "any group subscribed to that topic-partition rebalances."
                ),
                pros=[
                    "Memory is proportional to the topic-partitions actually queried, which "
                    "for the failover use case is a tiny fraction of the cluster.",
                    "No rebuild cost on coordinator failover — the cache simply starts cold.",
                    "No write-path changes at all, so zero risk to the JoinGroup / "
                    "LeaveGroup hot paths that every consumer depends on.",
                ],
                cons=[
                    "The first query for any topic-partition is still O(n_groups), and "
                    "failover is precisely a cold-start event — the one case where the "
                    "cache is guaranteed to be empty. This misses the point of the ticket.",
                    "Cache-stampede risk: a failover touching 142 mirrored topics would "
                    "issue 142 concurrent full scans.",
                    "Invalidation correctness is subtle and easy to get wrong — a missed "
                    "invalidation silently returns a stale group set, and clamping against "
                    "a stale set is a correctness bug, not a performance bug.",
                    "Unpredictable latency makes the p99 target essentially unachievable.",
                ],
                effort="M",
                risk="high",
                blast_radius=["consumer-team"],
            ),
            DesignAlternative(
                label="C",
                name="Persist the reverse index as a new `__consumer_offsets` record type",
                proposed_by=self.AGENT_NAME,
                approach=(
                    "Write reverse-index entries into the `__consumer_offsets` compacted "
                    "topic as a new key type, so the index is durable and survives "
                    "coordinator failover and broker restart with no replay-and-rebuild "
                    "step. GroupCoordinator loads it directly as part of normal partition "
                    "load."
                ),
                pros=[
                    "No rebuild cost on failover — the index is already durable, which "
                    "removes the 800ms fallback window from alternative A entirely.",
                    "Survives full broker restart, so cold-start behaviour is strictly "
                    "better than any in-memory option.",
                ],
                cons=[
                    "Changes the `__consumer_offsets` schema, which is a far larger blast "
                    "radius than the problem justifies — it is a public-ish format that "
                    "tooling, backups, and every Kafka operator depends on.",
                    "Write amplification on every join and leave, on the hot path, to "
                    "optimize a cold path that runs during failover only.",
                    "Log-compaction semantics for the new record type are genuinely tricky "
                    "to get right, and getting them wrong corrupts offset storage.",
                    "Needs its own KIP independent of the ListGroups change, with broker "
                    "team and OSS sign-off — strictly more coordination than alternative A "
                    "for strictly less benefit.",
                ],
                effort="XL",
                risk="high",
                blast_radius=["consumer-team", "broker-team", "oss-kafka", "operators"],
            ),
        ]

    # ------------------------------------------------------------------
    # Consultation handler — round-aware
    # ------------------------------------------------------------------

    def _handle_consultation(
        self, request: ImpactRequest, depth: int
    ) -> ImpactResponse:
        """Do real diligence on kora-global's request, round by round."""
        mem_cost = self.estimate_index_memory_cost(50_000, 8)
        rebalances = self.get_rebalance_history("orders")
        offset_schema = self.get_offset_storage_schema()
        alternatives = self._index_alternatives(mem_cost, rebalances)

        # Always back alternative A — it is the only one that meets the target
        # without an unacceptable blast radius.
        alternatives[0].recommended = True
        alternatives[1].rejected_reason = (
            "Failover is a cold-start event, so the lazy cache is guaranteed to be empty "
            "exactly when it is needed. Does not meet the p99 target."
        )
        alternatives[2].rejected_reason = (
            "Changing the __consumer_offsets schema is a disproportionate blast radius, "
            "and it puts write amplification on the hot path to optimize a cold path."
        )

        round_num = request.round_number

        # --- consult oss-kafka on our own initiative, in round 1 ---
        oss_response: ImpactResponse | None = None
        if round_num == 1 and depth < self.MAX_CONSULTATION_DEPTH:
            oss_request = ImpactRequest.new(
                from_agent=self.AGENT_NAME,
                to_agent="oss-kafka",
                ticket_id=request.ticket_id,
                request_type="protocol_review",
                context=(
                    "We can implement a reverse (topic, partition) → group index inside "
                    "GroupCoordinator with acceptable memory cost "
                    f"({mem_cost['total_mb']}MB for 50k groups). The server side is ours. "
                    "What we do not own is the public API surface that lets a client ask "
                    "for a filtered list.\n\n"
                    f"Offset storage context: {offset_schema['key_format']}."
                ),
                question=(
                    "Does exposing a topic-partition filter on ListGroups require a KIP, "
                    "and what is the right protocol shape — a v5 tagged field on "
                    "ListGroupsRequest, a new API key, or a Confluent-internal extension? "
                    "Also tell us what KIP-848 means for this, since new-protocol groups "
                    "do not go through ListGroups the same way."
                ),
                codepaths_of_interest=["ListGroupsRequest.json", "ApiKeys.java"],
                proposed_change=(
                    "ListGroupsRequest v5 with an optional topic_partitions filter field; "
                    "server routes to TopicPartitionGroupIndex when the field is present."
                ),
                round_number=round_num,
                consultation_depth=depth + 1,
            )
            oss_response = self._transport.consult("oss-kafka", oss_request)

        # --- build the response for this round ---
        new_concerns: list[str] = []
        open_questions: list[str] = []
        follow_ups: list[str] = []
        needs_org_authority = False
        org_reason = ""

        if round_num == 1:
            # Round 1: raise the substantive concerns, including correcting
            # kora-global's alternative B.
            new_concerns = [
                "Your alternative B is not correct as specified: `__consumer_offsets` holds "
                "*committed offsets*, not live subscriptions. A group that has joined and "
                "been assigned partitions but has not committed yet is invisible in that "
                "view, so you would silently skip clamping it. That is a correctness bug, "
                "not a performance tradeoff.",
                "Alternative C will not reach your 50ms target — batching describeGroups "
                "reduces RPC count but the coordinator still reads every group's metadata, "
                "so you land around 1-2s.",
                f"The index costs ~{mem_cost['total_mb']}MB per 50k-group cluster in broker "
                f"heap. That is acceptable to us but broker-team must be looped in before "
                f"it is enabled by default.",
                "Coordinator failover needs a ~800ms index rebuild from "
                "`__consumer_offsets` replay; we need the scan path retained as the "
                "fallback during that window.",
            ]
            verdict = "needs_changes"
            summary = (
                f"Alternative A is implementable and we will own it. Memory cost verified "
                f"at {mem_cost['total_mb']}MB total / "
                f"{mem_cost['per_coordinator_mb']}MB per coordinator partition for 50k "
                f"groups — under 1% of coordinator heap. Changes are scoped to "
                f"handleJoinGroup, handleLeaveGroup, handleListGroups and a new "
                f"TopicPartitionGroupIndex. We reject your B on correctness grounds "
                f"(committed offsets are not live subscriptions) and your C on the target "
                f"(still O(n_groups), lands at 1-2s). We have three implementation options "
                f"for the index itself and recommend the eager in-memory HashMap. "
                f"We consulted oss-kafka on the protocol surface."
            )
            if oss_response and not oss_response.timed_out:
                summary += f" oss-kafka says: {oss_response.summary}"
                follow_ups.append("oss-kafka")
                open_questions.extend(oss_response.open_questions)
            else:
                open_questions.append(
                    "Protocol surface for the public API filter is unconfirmed — "
                    "oss-kafka was unreachable."
                )
                follow_ups.append("oss-kafka")

        elif round_num == 2:
            # Round 2: concerns were folded in; converge on a recommendation.
            verdict = "needs_changes"
            new_concerns = [
                "One remaining ask: the feature flag must be per-cluster, not global, so we "
                "can enable on small clusters first and keep a blast-radius-limited rollout."
            ]
            summary = (
                "Agreed on alternative A with the eager in-memory HashMap implementation. "
                "Rebuild-on-failover at ~800ms is acceptable given the scan path stays as "
                "fallback behind the same flag. We will add index size and hit-rate metrics "
                "to GroupCoordinatorMetrics so shadow mode is verifiable. Requesting a "
                "per-cluster feature flag rather than a global one."
            )

        else:
            # Round 3: everything settled from our side.
            verdict = "agreed"
            new_concerns = []
            summary = (
                "Agreed and settled from our side. Alternative A, eager in-memory index in "
                "TopicPartitionGroupIndex, per-cluster feature flag, scan retained as "
                "fallback, shadow mode with a mismatch metric before we trust the index. "
                "Our test requirements are captured below. No further concerns."
            )

        return ImpactResponse(
            request_id=request.request_id,
            from_agent=self.AGENT_NAME,
            to_agent=request.from_agent,
            verdict=verdict,
            confidence=0.92,
            summary=summary,
            principal_review=(
                "The structural problem is that GroupCoordinator indexes group → "
                "subscriptions but the failover path asks the inverse question. Adding the "
                "reverse index is the correct fix and it belongs on our side of the "
                "boundary, not in Cluster Linking. I want to be explicit about why I am "
                "pushing back on your alternative B: it is not merely a duplicate-state "
                "tradeoff, it is incorrect. `__consumer_offsets` is a record of commits, "
                "and subscription state is not commit state. Building a clamping decision "
                "on it means a group that joined moments before failover and has not "
                "committed yet gets silently skipped, and a skipped clamp is a consumer "
                "resuming at an invalid offset. That is a data-correctness failure in a "
                "disaster-recovery path, which is the worst possible place for one. "
                "Alternative A costs us more calendar time and a KIP dependency, and it is "
                "still the right call."
            ),
            design_alternatives=alternatives,
            recommendation=(
                "Alternative A: Eager in-memory HashMap maintained incrementally. It is the "
                "only option that delivers O(1) reads on a cold-start failover path, its "
                f"memory cost is measured at {mem_cost['total_mb']}MB rather than assumed, "
                "and it keeps consumer-group state inside the team that owns it."
            ),
            cited_codepaths=[
                "GroupCoordinator.scala",
                "GroupMetadata.scala",
                "TopicPartitionGroupIndex.java",
                "GroupCoordinatorConfig.scala",
            ],
            new_concerns=new_concerns,
            open_questions=open_questions,
            follow_up_consultations=follow_ups,
            test_requirements=[
                "Unit: index contents match a brute-force scan of groupMetadataCache after "
                "10k randomized join/leave/timeout sequences.",
                "Unit: handleListGroups with an empty topic_partitions filter returns "
                "byte-identical results to v4 (backward compatibility).",
                "Unit: index mutation on heartbeat-timeout removal, not just explicit "
                "LeaveGroup.",
                "Integration: coordinator failover rebuilds the index from "
                "`__consumer_offsets` replay and converges to the same contents within "
                "1 second for 50k groups.",
                "Integration: requests served from the scan fallback while a rebuild is in "
                "progress return correct results.",
                "Load: index memory growth stays within the configured cap under a "
                "group-churn workload, and falls back to scan when the cap is hit.",
                "Metrics: index size and hit rate are exported so shadow mode can be "
                "validated before the flag is enabled.",
            ],
            needs_org_authority=needs_org_authority,
            org_authority_reason=org_reason,
        )
