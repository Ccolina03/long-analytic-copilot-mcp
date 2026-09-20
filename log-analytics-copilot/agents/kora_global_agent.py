"""
Kora Global SME Agent.

Domain: Cluster Linking — failover, offset clamping, mirror operations.
Runbook: runbooks/kora-global-sme.md

Tools
-----
  get_failover_latency(topic, last_hours=24)
  get_offset_clamp_trace(topic, partition)
  list_active_links()
  get_consumer_groups_for_link(link_id)

When this agent owns a ticket it:
  1. Calls get_offset_clamp_trace() to prove the bottleneck is real.
  2. Looks up owning_team("GroupCoordinator.scala") → "consumer-team".
  3. Looks up owning_team("ListGroupsRequest.json") → "oss-kafka".
  4. Consults both with an ImpactRequest.
  5. Assembles the final Finding once both have responded.
"""

from __future__ import annotations

from typing import Any

from agents.base_agent import SMEAgentBase, PeerTransport, tool
from proto.sme_agents import Finding, ImpactRequest, ImpactResponse, Ticket


class KoraGlobalAgent(SMEAgentBase):
    AGENT_NAME = "kora-global"
    DOMAIN = "Cluster Linking — failover, offset clamping, mirror operations"
    OWNS = [
        "confluent/kora-cluster-linking/",
        "OffsetClampingService.java",
        "FailoverCoordinator.java",
        "OffsetTranslationTable.java",
        "MirrorTopicReplicator.java",
        "PartitionOffsetTracker.java",
        "LinkAdminClient.java",
        "GroupSyncService.java",
        "LinkHealthMonitor.java",
    ]

    # Peers this agent consults for the clampOffsets ticket
    _CONSULT_PEERS = [
        ("consumer-team", "GroupCoordinator.scala"),
        ("oss-kafka",     "ListGroupsRequest.json"),
    ]

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    @tool("get_failover_latency")
    def get_failover_latency(self, topic: str, last_hours: int = 24) -> dict:
        """Return latency percentiles for clampOffsets executions on ``topic``."""
        if not topic:
            raise ValueError("topic is required")
        if last_hours <= 0:
            raise ValueError("last_hours must be > 0")
        return {
            "topic": topic,
            "window_hours": last_hours,
            "sample_count": 12,
            "p50_ms": 3200,
            "p95_ms": 8800,
            "p99_ms": 11400,
            "bottleneck_phase": "listGroups",
            "bottleneck_pct": 91,
        }

    @tool("get_offset_clamp_trace")
    def get_offset_clamp_trace(self, topic: str, partition: int) -> dict:
        """Replay the most recent offset clamping for (topic, partition)."""
        if not topic:
            raise ValueError("topic is required")
        phases = [
            {"name": "listGroups",      "ms": 840,  "groups_returned": 50312},
            {"name": "describeGroups",  "ms": 8100, "groups_scanned": 50312},
            {"name": "filterMatch",     "ms": 180,  "groups_matched": 4},
            {"name": "applyTranslation","ms": 80,   "offsets_clamped": 4},
        ]
        total_ms = sum(p["ms"] for p in phases)
        return {
            "topic": topic,
            "partition": partition,
            "total_ms": total_ms,
            "phases": phases,
            "matched_groups": [
                "billing-consumer",
                "analytics-consumer",
                "fraud-detector",
                "audit-logger",
            ],
        }

    @tool("list_active_links")
    def list_active_links(self) -> list:
        """Return all active cluster links with state and replication lag."""
        return [
            {
                "link_id": "us-east-1-to-eu-west-1",
                "state": "ACTIVE",
                "mirrored_topics": 142,
                "replication_lag_ms": 320,
                "consumer_group_sync": True,
            }
        ]

    @tool("get_consumer_groups_for_link")
    def get_consumer_groups_for_link(self, link_id: str) -> dict:
        """Show the current slow implementation stats for ``link_id``."""
        if not link_id:
            raise ValueError("link_id is required")
        return {
            "link_id": link_id,
            "method": "listGroups_then_filter",
            "duration_ms": 9200,
            "total_groups_scanned": 50312,
            "matched_groups": 4,
            "efficiency_pct": 0.008,
        }

    # ------------------------------------------------------------------
    # Workflow overrides
    # ------------------------------------------------------------------

    def _investigate(self, ticket: Ticket) -> dict[str, Any]:
        trace = self.get_offset_clamp_trace("orders", 3)
        latency = self.get_failover_latency("orders")
        links = self.list_active_links()

        # The root cause is clear: O(n_groups) scan in clampOffsets
        bottleneck_pct = latency["bottleneck_pct"]
        return {
            "agent": self.AGENT_NAME,
            "ticket_id": ticket.ticket_id,
            "root_cause": (
                f"clampOffsets scans ALL {trace['phases'][0]['groups_returned']:,} groups "
                f"to find {trace['matched_groups']} (0.008% hit rate); "
                f"{bottleneck_pct}% of {trace['total_ms']}ms is spent in "
                "listGroups + describeGroups fan-out"
            ),
            "cited_codepaths": [
                "OffsetClampingService.java",
                "confluent/kora-cluster-linking/",
            ],
            "trace": trace,
            "latency": latency,
            "affected_links": len(links),
            "execution_order": [
                "1. Consumer Team adds topicPartitionToGroups inverted index to GroupCoordinator",
                "2. Consumer Team adds ListGroupsRequest v5 topic_partitions filter field",
                "3. OSS Kafka team files + votes KIP for ListGroups v5",
                "4. Kora team updates OffsetClampingService to call new v5 API",
                "5. QA: measure clampOffsets p99 on 50k-group cluster — target < 50ms",
            ],
        }

    def _select_peers(
        self, ticket: Ticket, investigation: dict[str, Any]
    ) -> list[tuple[str, str]]:
        """Look up owning teams for the codepaths we need to change."""
        peers = []
        target_paths = [
            ("GroupCoordinator.scala", "consumer-team"),
            ("ListGroupsRequest.json", "oss-kafka"),
        ]
        for codepath, expected_team in target_paths:
            # In production: owning_team(codepath, conn=self._kg_conn)
            # Here we use the known mapping for the demo ticket
            peers.append((expected_team, codepath))
        return peers

    def _build_impact_request(
        self,
        peer_id: str,
        ticket: Ticket,
        investigation: dict[str, Any],
        codepath: str = "",
    ) -> ImpactRequest:
        trace = investigation.get("trace", {})
        if peer_id == "consumer-team":
            context = (
                "Requesting: listGroupsForTopicPartition(topic, partition) → [group_id]\n\n"
                f"Evidence:\n"
                f"  clampOffsets p99 = {investigation['latency']['p99_ms']}ms "
                f"for cluster with {trace.get('phases', [{}])[0].get('groups_returned', 0):,} groups\n"
                f"  91% of time in listGroups + describeGroups fan-out\n"
                f"  Only 4 of 50,312 groups matched (0.008% hit rate)\n\n"
                "Proposed contract: Input: topic str, partition int → Output: [group_id]\n"
                "Latency target: <50ms"
            )
        else:
            context = (
                "Protocol change needed: ListGroupsRequest v5 with topic_partitions filter.\n"
                "Consumer Team will implement; OSS Kafka team must KIP + vote."
            )
        return ImpactRequest.new(
            from_agent=self.AGENT_NAME,
            to_agent=peer_id,
            ticket_id=ticket.ticket_id,
            request_type="impact_analysis",
            context=context,
            codepaths_of_interest=[codepath],
            proposed_change=(
                "Add inverted index to GroupCoordinator + ListGroups v5 filter"
            ),
        )

    def _assemble_finding(
        self,
        ticket: Ticket,
        investigation: dict[str, Any],
        responses: list[ImpactResponse],
    ) -> Finding:
        approvals = []
        open_qs = []
        follow_ups = []

        for resp in responses:
            open_qs.extend(resp.open_questions)
            follow_ups.extend(resp.follow_up_consultations)
            if resp.verdict in ("needs_changes", "approved"):
                approvals.append(f"{resp.from_agent} lead")

        return Finding(
            ticket_id=ticket.ticket_id,
            owning_agent=self.AGENT_NAME,
            summary=(
                "clampOffsets bottleneck confirmed: O(n_groups) scan. "
                "Fix requires GroupCoordinator inverted index (consumer-team) "
                "and ListGroups v5 API (oss-kafka KIP)."
            ),
            root_cause=investigation["root_cause"],
            confidence=0.95,
            execution_order=investigation["execution_order"],
            approvals_needed=approvals or ["consumer-team lead", "oss-kafka committer"],
            requires_human=True,
            cited_codepaths=investigation["cited_codepaths"],
            open_questions=open_qs,
            consultations=[r.to_dict() for r in responses],
        )
