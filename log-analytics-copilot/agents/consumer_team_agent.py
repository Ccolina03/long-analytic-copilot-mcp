"""
Consumer Team SME Agent.

Domain: Consumer groups, GroupCoordinator, offset storage, rebalancing.
Runbook: runbooks/consumer-team-sme.md

Tools
-----
  get_group_state(group_id)
  get_groups_for_topic_partition(topic, partition)
  get_offset_storage_schema()
  estimate_index_memory_cost(group_count, avg_subscriptions_per_group)
  get_rebalance_history(topic, last_minutes=60)

When consulted by kora-global this agent:
  1. Runs estimate_index_memory_cost() to validate feasibility.
  2. Determines a protocol change is needed → consults oss-kafka itself.
  3. Returns ImpactResponse with open_questions pointing to the KIP need.
"""

from __future__ import annotations

from typing import Any

from agents.base_agent import SMEAgentBase, tool
from proto.sme_agents import ImpactRequest, ImpactResponse, Ticket


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

        Shows both the current slow-scan path and the proposed indexed path
        so the agent can demonstrate before/after.
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
        per_coordinator_mb = total_mb / 50  # 50 __consumer_offsets partitions
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
    # Consultation handler
    # ------------------------------------------------------------------

    def _handle_consultation(
        self, request: ImpactRequest, depth: int
    ) -> ImpactResponse:
        """Respond to kora-global's ImpactRequest.

        1. Validate the inverted index is feasible (memory cost).
        2. Recognise that the API filter needs a protocol change → ask oss-kafka.
        3. Return structured response.
        """
        # Step 1: validate memory cost
        mem_cost = self.estimate_index_memory_cost(50_000, 8)
        groups_data = self.get_groups_for_topic_partition("orders", 3)
        offset_schema = self.get_offset_storage_schema()

        # Step 2: consult oss-kafka on the protocol change (on own initiative)
        oss_response = None
        oss_kip_note = "needs a new Kafka API version — ask oss-kafka"
        if depth < self.MAX_CONSULTATION_DEPTH:
            oss_request = ImpactRequest.new(
                from_agent=self.AGENT_NAME,
                to_agent="oss-kafka",
                ticket_id=request.ticket_id,
                request_type="protocol_review",
                context=(
                    "Proposing ListGroupsRequest v5 — add topic_partitions filter field.\n"
                    f"Schema: topic_partitions ARRAY(STRUCT(topic STRING, partition INT32))\n"
                    f"Backward compat: empty field = existing behavior.\n"
                    f"Offset schema for context: {offset_schema['key_format']}"
                ),
                codepaths_of_interest=["ListGroupsRequest.json", "ApiKeys.java"],
                proposed_change="ListGroupsRequest v5 with optional topic_partitions filter",
                consultation_depth=depth + 1,
            )
            oss_response = self._transport.consult("oss-kafka", oss_request)

        follow_ups = ["oss-kafka"] if oss_response else []
        open_qs = []
        if oss_response and oss_response.timed_out:
            open_qs.append(oss_kip_note)
        elif not oss_response:
            open_qs.append(oss_kip_note)

        return ImpactResponse(
            request_id=request.request_id,
            from_agent=self.AGENT_NAME,
            to_agent=request.from_agent,
            verdict="needs_changes",
            confidence=0.9,
            summary=(
                f"Inverted index feasible: {mem_cost['total_mb']}MB total "
                f"({mem_cost['per_coordinator_mb']}MB per coordinator). "
                "GroupCoordinator.scala changes scoped to handleListGroups() + "
                "topicPartitionToGroups Map. "
                "ListGroupsRequest v5 field requires KIP — consulted oss-kafka."
            ),
            cited_codepaths=[
                "GroupCoordinator.scala",
                "GroupMetadata.scala",
                "TopicPartitionGroupIndex.java",
            ],
            open_questions=open_qs,
            follow_up_consultations=follow_ups,
        )
