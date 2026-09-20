"""
Group Coordinator SME Agent — Principal Engineer, consumer group coordination.

Domain: Consumer groups, the group-coordinator module, offset storage,
rebalancing (classic and KIP-848 consumer protocols).
Runbook: runbooks/group-coordinator-sme.md

Tools
-----
  get_group_state(group_id)
  get_groups_for_topic_partition(topic, partition)
  get_offset_storage_schema()
  estimate_index_memory_cost(group_count, avg_subscriptions_per_group)
  get_rebalance_history(topic, last_minutes=60)

Behaviour
---------
When mirrormaker consults it, this agent does real diligence: it validates the
memory cost with its own tool, corrects mirrormaker's alternative B on a
genuine correctness point (committed offsets are not live subscriptions), and
proposes three implementation alternatives for the index itself. It consults
kafka-clients on its own initiative about the protocol surface. It moves to
'agreed' only once its concerns have been folded in.
"""

from __future__ import annotations

from agents.base_agent import SMEAgentBase, tool
from proto.sme_agents import DesignAlternative, ImpactRequest, ImpactResponse


class GroupCoordinatorAgent(SMEAgentBase):
    AGENT_NAME = "group-coordinator"
    DOMAIN = (
        "Consumer groups, group coordination, offset storage, rebalancing "
        "(classic and KIP-848 consumer protocols)"
    )
    OWNS = [
        "group-coordinator/src/main/java/org/apache/kafka/coordinator/group/",
        "GroupMetadataManager.java",
        "GroupCoordinatorService.java",
        "GroupCoordinatorShard.java",
        "GroupCoordinatorConfig.java",
        "OffsetMetadataManager.java",
        "ConsumerGroup.java",
        "ClassicGroup.java",
        "TopicPartitionGroupIndex.java",
    ]

    # Routine work (memory estimates, rebalance history) is arithmetic over
    # tool output and needs no model depth; the design review that argues
    # against a peer's proposal escalates to LLM_DESIGN_TIER on its own.
    LLM_TIER = "small"

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
            "group_type": "consumer",
            "member_count": 6,
            "subscribed_topics": ["orders", "payments", "refunds"],
            "coordinator_shard": 12,
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
            "key_format": "(group_id: string, topic: string, partition: int32)",
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
        per_shard_mb = total_mb / 50
        return {
            "group_count": group_count,
            "avg_subscriptions": avg_subscriptions_per_group,
            "entries": entries,
            "bytes_per_entry": bytes_per_entry,
            "total_mb": round(total_mb, 1),
            "per_shard_mb": round(per_shard_mb, 2),
            "assessment": (
                "acceptable — under 1% of default broker heap"
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

    def _index_alternatives(
        self, mem_cost: dict, rebalances: dict
    ) -> list[DesignAlternative]:
        """Three ways to actually build the reverse index inside the coordinator."""
        return [
            DesignAlternative(
                label="A",
                name="Eager in-memory HashMap maintained incrementally",
                proposed_by=self.AGENT_NAME,
                approach=(
                    "Add `topicPartitionToGroups: Map<TopicPartition, Set<String>>` "
                    "alongside the existing group metadata in GroupMetadataManager. Mutate "
                    "it wherever subscriptions change: the classic JoinGroup path, the "
                    "KIP-848 ConsumerGroupHeartbeat path, LeaveGroup, and the "
                    "session-timeout eviction path. When a GroupCoordinatorShard loads its "
                    "`__consumer_offsets` partition, rebuild the index from the replay it "
                    "already performs. A filtered ListGroups request then serves from the "
                    "index instead of walking every group. New file "
                    "`TopicPartitionGroupIndex.java` holds the structure and its metrics."
                ),
                pros=[
                    "O(1) reads — exactly what a cold-start failover path needs.",
                    f"Memory cost is measured, not guessed: {mem_cost['total_mb']}MB total "
                    f"for {mem_cost['group_count']:,} groups at "
                    f"{mem_cost['avg_subscriptions']} subscriptions each, which is "
                    f"{mem_cost['per_shard_mb']}MB per coordinator shard.",
                    f"Low GC pressure — entries mutate only on membership change, and the "
                    f"measured rate is {rebalances['rebalance_count']} rebalances per hour "
                    f"for a busy topic, nowhere near hot-path frequency.",
                    "The index is derived from live in-memory membership, so it reflects "
                    "actual subscriptions rather than committed offsets.",
                    "Simple enough to reason about correctness by inspection, which matters "
                    "for code every Kafka cluster in the world runs.",
                    "Works identically for classic and KIP-848 consumer groups, because "
                    "both funnel their subscription changes through this manager.",
                ],
                cons=[
                    "Rebuild on shard load costs roughly 800ms for 50k groups; requests "
                    "during that window must fall back to the scan.",
                    "Memory is proportional to *all* subscriptions, including topics nobody "
                    "ever queries by partition.",
                    "Unbounded if a pathological workload churns groups, so it needs a "
                    "config-capped size with a documented fallback.",
                ],
                effort="M",
                risk="low",
                blast_radius=["group-coordinator", "kafka-broker"],
            ),
            DesignAlternative(
                label="B",
                name="Lazy index computed on first query, with TTL and rebalance invalidation",
                proposed_by=self.AGENT_NAME,
                approach=(
                    "Do not maintain an index eagerly. On the first filtered ListGroups "
                    "call for a given topic-partition, compute the matching group set by "
                    "scanning group metadata once, then cache the result with a TTL. "
                    "Invalidate the cache entry whenever any group subscribed to that "
                    "topic-partition rebalances."
                ),
                pros=[
                    "Memory is proportional to the topic-partitions actually queried, which "
                    "for the replication use case is a tiny fraction of the cluster.",
                    "No rebuild cost on shard load — the cache simply starts cold.",
                    "No write-path changes at all, so zero risk to the JoinGroup and "
                    "ConsumerGroupHeartbeat hot paths every consumer depends on.",
                ],
                cons=[
                    "The first query for any topic-partition is still O(n_groups), and "
                    "failover is precisely a cold-start event — the one case where the "
                    "cache is guaranteed to be empty. This misses the point of the ticket.",
                    "Cache-stampede risk: a failover touching 142 replicated topics would "
                    "issue 142 concurrent full scans.",
                    "Invalidation correctness is subtle and easy to get wrong — a missed "
                    "invalidation silently returns a stale group set, and checkpointing "
                    "against a stale set is a correctness bug, not a performance bug.",
                    "Unpredictable latency makes the p99 target essentially unachievable.",
                ],
                effort="M",
                risk="high",
                blast_radius=["group-coordinator"],
            ),
            DesignAlternative(
                label="C",
                name="Persist the reverse index as a new `__consumer_offsets` record type",
                proposed_by=self.AGENT_NAME,
                approach=(
                    "Write reverse-index entries into the `__consumer_offsets` compacted "
                    "topic as a new record type, so the index is durable and survives shard "
                    "reassignment and broker restart with no replay-and-rebuild step. The "
                    "coordinator loads it directly as part of normal partition load."
                ),
                pros=[
                    "No rebuild cost on shard load — the index is already durable, which "
                    "removes the 800ms fallback window from alternative A entirely.",
                    "Survives full broker restart, so cold-start behaviour is strictly "
                    "better than any in-memory option.",
                ],
                cons=[
                    "Changes the `__consumer_offsets` record schema, which is a far larger "
                    "blast radius than the problem justifies — tooling, backups, and every "
                    "Kafka operator depend on that format.",
                    "Write amplification on every membership change, on the hot path, to "
                    "optimize a cold path that runs during checkpointing only.",
                    "Log-compaction semantics for the new record type are genuinely tricky "
                    "to get right, and getting them wrong corrupts offset storage.",
                    "Needs its own KIP independent of the ListGroups change, with broker "
                    "and client sign-off — strictly more coordination than alternative A "
                    "for strictly less benefit.",
                ],
                effort="XL",
                risk="high",
                blast_radius=[
                    "group-coordinator", "kafka-broker", "kafka-clients", "operators",
                ],
            ),
        ]

    # ------------------------------------------------------------------
    # Consultation handler — round-aware
    # ------------------------------------------------------------------

    def _handle_consultation(
        self, request: ImpactRequest, depth: int
    ) -> ImpactResponse:
        """Do real diligence on mirrormaker's request, round by round."""
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
            "Changing the __consumer_offsets record schema is a disproportionate blast "
            "radius, and it puts write amplification on the hot path to optimize a cold "
            "path."
        )

        round_num = request.round_number

        # --- consult kafka-clients on our own initiative, in round 1 ---
        clients_response: ImpactResponse | None = None
        if round_num == 1 and depth < self.MAX_CONSULTATION_DEPTH:
            clients_request = ImpactRequest.new(
                from_agent=self.AGENT_NAME,
                to_agent="kafka-clients",
                ticket_id=request.ticket_id,
                request_type="protocol_review",
                context=(
                    "We can implement a reverse (topic, partition) → group index inside "
                    "GroupMetadataManager with acceptable memory cost "
                    f"({mem_cost['total_mb']}MB for 50k groups). The server side is ours. "
                    "What we do not own is the public API surface that lets a client ask "
                    "for a filtered list.\n\n"
                    f"Offset storage context: {offset_schema['key_format']}."
                ),
                question=(
                    "Does exposing a topic-partition filter on ListGroups require a KIP, "
                    "and what is the right protocol shape — a v6 tagged field on "
                    "ListGroupsRequest or a new API key? Also tell us how the filter should "
                    "behave for KIP-848 consumer groups versus classic groups, since they "
                    "are described through different APIs."
                ),
                codepaths_of_interest=["ListGroupsRequest.json", "ApiKeys.java"],
                proposed_change=(
                    "ListGroupsRequest v6 with an optional topic_partitions filter field; "
                    "server routes to TopicPartitionGroupIndex when the field is present."
                ),
                round_number=round_num,
                consultation_depth=depth + 1,
            )
            clients_response = self._transport.consult("kafka-clients", clients_request)

        # --- build the response for this round ---
        new_concerns: list[str] = []
        open_questions: list[str] = []
        follow_ups: list[str] = []

        if round_num == 1:
            # Round 1: raise the substantive concerns, including correcting
            # mirrormaker's alternative B.
            new_concerns = [
                "Your alternative B is not correct as specified: `__consumer_offsets` holds "
                "*committed offsets*, not live subscriptions. A group that has joined and "
                "been assigned partitions but has not committed yet is invisible in that "
                "view, so you would silently skip checkpointing it. That is a correctness "
                "bug, not a performance tradeoff.",
                "Alternative C will not reach your target — batching describeConsumerGroups "
                "reduces round trips but each coordinator still reads every group's "
                "metadata, so you land around 1-2s.",
                f"The index costs ~{mem_cost['total_mb']}MB per 50k-group cluster in broker "
                f"heap. That is acceptable to us but kafka-broker must sign off before it "
                f"is enabled by default.",
                "Coordinator shard load needs a ~800ms index rebuild from "
                "`__consumer_offsets` replay; we need the scan path retained as the "
                "fallback during that window.",
            ]
            verdict = "needs_changes"
            summary = (
                f"Alternative A is implementable and we will own it. Memory cost verified "
                f"at {mem_cost['total_mb']}MB total / {mem_cost['per_shard_mb']}MB per "
                f"coordinator shard for 50k groups — under 1% of broker heap. Changes are "
                f"scoped to the subscription-mutation paths in GroupMetadataManager, the "
                f"filtered ListGroups handler, and a new TopicPartitionGroupIndex. We "
                f"reject your B on correctness grounds (committed offsets are not live "
                f"subscriptions) and your C on the target (still O(n_groups), lands at "
                f"1-2s). We have three implementation options for the index itself and "
                f"recommend the eager in-memory HashMap. We consulted kafka-clients on the "
                f"protocol surface."
            )
            if clients_response and not clients_response.timed_out:
                summary += f" kafka-clients says: {clients_response.summary}"
                follow_ups.append("kafka-clients")
                open_questions.extend(clients_response.open_questions)
            else:
                open_questions.append(
                    "Protocol surface for the public API filter is unconfirmed — "
                    "kafka-clients was unreachable."
                )
                follow_ups.append("kafka-clients")

        elif round_num == 2:
            # Round 2: concerns were folded in; converge on a recommendation.
            verdict = "needs_changes"
            new_concerns = [
                "One remaining ask: the feature flag must be per-broker config, not a "
                "cluster-wide switch, so operators can enable it on small clusters first "
                "and keep a blast-radius-limited rollout."
            ]
            summary = (
                "Agreed on alternative A with the eager in-memory HashMap implementation. "
                "Rebuild-on-load at ~800ms is acceptable given the scan path stays as "
                "fallback behind the same flag. We will add index size and hit-rate metrics "
                "to the coordinator metrics group so shadow mode is verifiable. Requesting "
                "a per-broker config rather than a cluster-wide switch."
            )

        else:
            # Round 3: everything settled from our side.
            verdict = "agreed"
            summary = (
                "Agreed and settled from our side. Alternative A, eager in-memory index in "
                "TopicPartitionGroupIndex, per-broker config, scan retained as fallback, "
                "shadow mode with a mismatch metric before we trust the index. Our test "
                "requirements are captured below. No further concerns."
            )

        return ImpactResponse(
            request_id=request.request_id,
            from_agent=self.AGENT_NAME,
            to_agent=request.from_agent,
            verdict=verdict,
            confidence=0.92,
            summary=summary,
            principal_review=(
                "The structural problem is that the coordinator indexes group → "
                "subscriptions but the replication path asks the inverse question. Adding "
                "the reverse index is the correct fix and it belongs on our side of the "
                "boundary, not in MirrorMaker. I want to be explicit about why I am pushing "
                "back on your alternative B: it is not merely a duplicate-state tradeoff, "
                "it is incorrect. `__consumer_offsets` is a record of commits, and "
                "subscription state is not commit state. Building a checkpoint decision on "
                "it means a group that joined moments before failover and has not committed "
                "yet gets silently skipped, and a skipped checkpoint is a consumer resuming "
                "at an invalid offset on the target cluster. That is a data-correctness "
                "failure in a disaster-recovery path, which is the worst possible place for "
                "one. Alternative A costs more calendar time and a KIP dependency, and it "
                "is still the right call. Worth noting the index is protocol-agnostic: both "
                "classic and KIP-848 consumer groups mutate subscriptions through this "
                "manager, so one index serves both."
            ),
            design_alternatives=alternatives,
            recommendation=(
                "Alternative A: Eager in-memory HashMap maintained incrementally. It is the "
                "only option that delivers O(1) reads on a cold-start failover path, its "
                f"memory cost is measured at {mem_cost['total_mb']}MB rather than assumed, "
                "and it keeps consumer-group state inside the team that owns it."
            ),
            cited_codepaths=[
                "GroupMetadataManager.java",
                "GroupCoordinatorShard.java",
                "TopicPartitionGroupIndex.java",
                "GroupCoordinatorConfig.java",
            ],
            new_concerns=new_concerns,
            open_questions=open_questions,
            follow_up_consultations=follow_ups,
            test_requirements=[
                "Unit: index contents match a brute-force scan of group metadata after 10k "
                "randomized join/leave/timeout sequences.",
                "Unit: filtered ListGroups with an empty topic_partitions filter returns "
                "byte-identical results to v5 (backward compatibility).",
                "Unit: index mutation on session-timeout eviction, not just explicit "
                "LeaveGroup.",
                "Unit: index is maintained identically for classic groups and KIP-848 "
                "consumer groups.",
                "Integration: coordinator shard load rebuilds the index from "
                "`__consumer_offsets` replay and converges to the same contents within "
                "1 second for 50k groups.",
                "Integration: requests served from the scan fallback while a rebuild is in "
                "progress return correct results.",
                "Load: index memory growth stays within the configured cap under a "
                "group-churn workload, and falls back to scan when the cap is hit.",
                "Metrics: index size and hit rate are exported so shadow mode can be "
                "validated before the flag is enabled.",
            ],
        )
