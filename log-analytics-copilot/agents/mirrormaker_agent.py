"""
MirrorMaker SME Agent — Principal Engineer, cross-cluster replication.

Domain: MirrorMaker 2 — asynchronous replication between Kafka clusters,
consumer group offset translation, checkpointing, failover.
Runbook: runbooks/mirrormaker-sme.md

This is the entry-point agent. Tickets about cross-cluster replication land
here, and this agent drives the investigation before consulting anyone. It is
the natural owner because MM2 sits at the boundary between two clusters and
therefore sees problems that no single-cluster team would notice.

Tools
-----
  get_checkpoint_latency(topic, last_hours=24)
  get_group_discovery_trace(topic)
  list_replication_flows()
  get_group_discovery_cost(flow_id)

Behaviour
---------
Owns the group-discovery ticket. Proposes three genuinely different design
alternatives spanning the real solution space (correct-but-needs-a-KIP,
fast-but-owns-duplicate-state, cheap-but-partial), then deliberates with
group-coordinator, kafka-clients, and kafka-broker across up to 3 rounds
before recommending one.
"""

from __future__ import annotations

from typing import Any

from agents.base_agent import SMEAgentBase, tool
from proto.sme_agents import (
    DesignAlternative,
    ImpactRequest,
    ImpactResponse,
    TeamInvolvement,
    Ticket,
)


class MirrorMakerAgent(SMEAgentBase):
    AGENT_NAME = "mirrormaker"
    DOMAIN = (
        "MirrorMaker 2 — async cross-cluster replication, consumer group "
        "offset translation, checkpointing, failover"
    )
    OWNS = [
        "connect/mirror/src/main/java/org/apache/kafka/connect/mirror/",
        "MirrorCheckpointConnector.java",
        "MirrorCheckpointTask.java",
        "MirrorSourceConnector.java",
        "MirrorHeartbeatConnector.java",
        "OffsetSyncStore.java",
        "MirrorClient.java",
        "Checkpoint.java",
        "MirrorMakerConfig.java",
    ]

    # As the owning agent this one authors the 1-pager and synthesizes three
    # teams' input, so it carries a standard tier rather than the cheapest.
    LLM_TIER = "standard"

    # Checkpoint interval any acceptable alternative must fit inside. MM2's
    # default emit.checkpoints.interval.seconds is 60s; group discovery
    # burning 12s of that budget is the problem.
    TARGET_DISCOVERY_P99_MS = 500

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    @tool("get_checkpoint_latency")
    def get_checkpoint_latency(self, topic: str, last_hours: int = 24) -> dict:
        """Return latency percentiles for checkpoint emission on ``topic``."""
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
            "bottleneck_phase": "listConsumerGroups",
            "bottleneck_pct": 91,
            "checkpoint_interval_ms": 60_000,
        }

    @tool("get_group_discovery_trace")
    def get_group_discovery_trace(self, topic: str) -> dict:
        """Replay MirrorCheckpointConnector's most recent group discovery."""
        if not topic:
            raise ValueError("topic is required")
        phases = [
            {"name": "listConsumerGroups", "ms": 840, "groups_returned": 50312},
            {"name": "describeConsumerGroups", "ms": 8100, "groups_scanned": 50312},
            {"name": "filterBySubscription", "ms": 180, "groups_matched": 4},
            {"name": "translateOffsets", "ms": 80, "offsets_checkpointed": 4},
        ]
        total_ms = sum(p["ms"] for p in phases)
        return {
            "topic": topic,
            "total_ms": total_ms,
            "phases": phases,
            "matched_groups": [
                "billing-consumer",
                "analytics-consumer",
                "fraud-detector",
                "audit-logger",
            ],
        }

    @tool("list_replication_flows")
    def list_replication_flows(self) -> list:
        """Return all active MM2 replication flows with lag and sync state."""
        return [
            {
                "flow_id": "us-east-1->eu-west-1",
                "state": "RUNNING",
                "replicated_topics": 142,
                "replication_lag_ms": 320,
                "sync_group_offsets_enabled": True,
            },
            {
                "flow_id": "us-west-2->ap-south-1",
                "state": "RUNNING",
                "replicated_topics": 88,
                "replication_lag_ms": 410,
                "sync_group_offsets_enabled": True,
            },
        ]

    @tool("get_group_discovery_cost")
    def get_group_discovery_cost(self, flow_id: str) -> dict:
        """Show the current slow-discovery stats for ``flow_id``."""
        if not flow_id:
            raise ValueError("flow_id is required")
        return {
            "flow_id": flow_id,
            "method": "listConsumerGroups_then_describe",
            "duration_ms": 9200,
            "total_groups_scanned": 50312,
            "matched_groups": 4,
            "efficiency_pct": 0.008,
        }

    # ------------------------------------------------------------------
    # Investigation
    # ------------------------------------------------------------------

    def _investigate(self, ticket: Ticket) -> dict[str, Any]:
        trace = self.get_group_discovery_trace("orders")
        latency = self.get_checkpoint_latency("orders")
        flows = self.list_replication_flows()
        groups_scanned = trace["phases"][0]["groups_returned"]
        matched = trace["phases"][2]["groups_matched"]

        return {
            "agent": self.AGENT_NAME,
            "ticket_id": ticket.ticket_id,
            "trace": trace,
            "latency": latency,
            "affected_flows": len(flows),
            "root_cause": (
                f"Kafka maintains no reverse index from (topic, partition) to consumer "
                f"group, so MirrorCheckpointConnector has no way to ask which groups "
                f"consume a given topic. findConsumerGroups() calls "
                f"Admin.listConsumerGroups() to fetch all {groups_scanned:,} groups, "
                f"then fans out "
                f"describeConsumerGroups() across every one of them to read subscriptions. "
                f"Only {matched} of them actually consume the replicated topics "
                f"({matched / groups_scanned:.3%} hit rate). "
                f"{latency['bottleneck_pct']}% of the {trace['total_ms']}ms is spent in "
                f"those two calls, and the fan-out lands on the group coordinators at "
                f"exactly the moment a failover makes them most stressed."
            ),
            "summary": (
                f"MM2 checkpoint group discovery p99 is {latency['p99_ms']}ms against a "
                f"{self.TARGET_DISCOVERY_P99_MS}ms target, caused by an O(n_groups) scan "
                f"on a path that needs O(matched_groups)."
            ),
            "cited_codepaths": [
                "MirrorCheckpointConnector.java",
                "MirrorCheckpointTask.java",
                "connect/mirror/src/main/java/org/apache/kafka/connect/mirror/",
            ],
            "goals": [
                f"Reduce checkpoint group discovery p99 from {latency['p99_ms']}ms to "
                f"under {self.TARGET_DISCOVERY_P99_MS}ms on a 50k-group cluster.",
                "Eliminate the describeConsumerGroups fan-out that thundering-herds the "
                "group coordinators during replication and failover.",
                "Keep offset translation correctness unchanged — no consumer may resume "
                "at an invalid offset on the target cluster.",
                "Make discovery cost scale with the number of *matched* groups, not the "
                "total number of groups in the source cluster.",
            ],
            "non_goals": [
                "Changing the offset translation algorithm itself (OffsetSyncStore is "
                "correct and is not in scope).",
                "Improving general-purpose listConsumerGroups performance for unrelated "
                "callers.",
                "Redesigning MirrorSourceConnector topic replication — separate concern.",
                "Reducing replication lag on the data path — unrelated to this hot path.",
                "Changing MM2's groups / groups.exclude configuration semantics.",
            ],
            "execution_order": [
                "group-coordinator adds the reverse index behind a feature flag, with "
                "metrics on index size and hit rate.",
                "kafka-broker confirms the scatter-gather routing across all 50 "
                "__consumer_offsets coordinator shards and signs off on the heap budget.",
                "kafka-clients drafts and posts the ListGroups v6 KIP for the public API "
                "surface, citing KIP-518 as prior art.",
                "Once the KIP merges, MirrorCheckpointConnector switches to the filtered "
                "Admin call, keeping the full scan behind a config as fallback.",
                "Validate discovery p99 on a 50k-group staging cluster across one full "
                "failover drill per region, then remove the fallback.",
            ],
            "risks_and_mitigations": [
                "**Index drift** — the reverse index disagrees with live group metadata. "
                "Mitigation: run the scan in shadow mode for one week and alert on any "
                "mismatch before trusting the index.",
                "**Coordinator failover rebuild** — the index must be rebuilt from "
                "`__consumer_offsets` replay (~800ms for 50k groups). "
                "Mitigation: serve the scan path while a rebuild is in progress.",
                "**Broker heap growth** — ~14MB per 50k-group cluster. "
                "Mitigation: config-capped index size with a documented fallback to full "
                "scan when the cap is hit.",
                "**KIP timeline** — the Apache vote is outside any single contributor's "
                "control. Mitigation: the server-side index is not a wire change, so it "
                "lands independently of the KIP schedule.",
            ],
            "rollout_and_rollback": [
                "Broker config `group.coordinator.topic.partition.index.enable` defaults "
                "off; enable per cluster starting with the smallest group counts.",
                "Shadow mode first: run the indexed lookup and the full scan in parallel, "
                "compare results, emit a mismatch metric, do not act on the index.",
                "MM2 side is gated by `checkpoints.group.discovery.use.filter`, which "
                "falls back to the scan when the broker does not advertise v6.",
                "Rollback is flipping either flag off — the scan path stays in the binary "
                "until the v6 API is in production everywhere.",
                "Bake for one full failover drill per region before enabling by default.",
            ],
            "success_metrics": [
                f"Checkpoint group discovery p99 < {self.TARGET_DISCOVERY_P99_MS}ms on a "
                f"50k-group cluster (from {latency['p99_ms']}ms).",
                "Zero index/scan mismatches over a 7-day shadow-mode window.",
                "describeConsumerGroups call volume during checkpointing drops by >99%.",
                "No increase in consumer lag spikes attributable to failover.",
                "Group coordinator p99 request latency unchanged or improved during "
                "checkpoint emission.",
            ],
            "testing_strategy": [
                "[mirrormaker] Unit: findConsumerGroups() returns identical group sets "
                "from the filtered path and the scan path for 1k randomized subscription "
                "fixtures.",
                "[mirrormaker] Unit: fallback to the scan when the broker does not "
                "advertise ListGroups v6.",
                "[mirrormaker] Integration: failover drill on a 50k-group staging "
                f"cluster, asserting discovery p99 < {self.TARGET_DISCOVERY_P99_MS}ms.",
                "[mirrormaker] Correctness: no consumer resumes at an offset outside the "
                "valid target range, verified across 100 simulated failovers.",
                "[mirrormaker] Load: describeConsumerGroups call count during checkpoint "
                "emission must be zero on the filtered path.",
            ],
        }

    # ------------------------------------------------------------------
    # Design alternatives — the real solution space
    # ------------------------------------------------------------------

    def _propose_alternatives(
        self, ticket: Ticket, investigation: dict[str, Any]
    ) -> list[DesignAlternative]:
        return [
            DesignAlternative(
                label="A",
                name="Broker-side reverse index + ListGroups v6 topic filter",
                proposed_by=self.AGENT_NAME,
                approach=(
                    "group-coordinator adds a `topicPartitionToGroups: Map<TopicPartition, "
                    "Set<String>>` to GroupMetadataManager, maintained incrementally as "
                    "members join, leave, and time out, and rebuilt from "
                    "`__consumer_offsets` replay when a coordinator shard loads. "
                    "kafka-clients adds an optional `topic_partitions` tagged field to "
                    "ListGroupsRequest v6 and a matching "
                    "`ListConsumerGroupsOptions.inTopicPartitions(...)`. "
                    "MirrorCheckpointConnector.findConsumerGroups() then issues a single "
                    "filtered Admin call and receives only the groups that actually "
                    "consume the replicated partitions, making discovery O(matched) "
                    "instead of O(n_groups). The broker-side index is not a wire change, "
                    "so it can land and be validated ahead of the KIP vote."
                ),
                pros=[
                    "Fixes the root cause rather than the symptom — discovery becomes "
                    "O(matched_groups), which is what the algorithm actually needs.",
                    "Single source of truth: the index lives next to the group metadata it "
                    "indexes, so there is no cross-process state to drift.",
                    "Eliminates the describeConsumerGroups fan-out entirely, removing the "
                    "thundering herd on the coordinators during checkpoint emission.",
                    "Every Kafka user benefits — MM2, any DR deployment, and any operator "
                    "tool that asks 'who is consuming this topic' has the same problem.",
                    "The server-side index ships independently of the KIP, so operator "
                    "pain is relieved without waiting on the Apache process.",
                ],
                cons=[
                    "Requires a KIP with a roughly 6-week discussion-and-vote timeline for "
                    "the public API surface, which no single contributor controls.",
                    "Cross-team dependency on three teams: group-coordinator to implement "
                    "the index, kafka-broker for routing and heap, kafka-clients to "
                    "shepherd the protocol change.",
                    "Adds ~14MB of broker heap per 50k-group cluster, which needs "
                    "kafka-broker sign-off.",
                    "Index rebuild on coordinator shard load costs ~800ms, during which "
                    "the fallback path must be used.",
                    "Behaviour for KIP-848 consumer groups versus classic groups must be "
                    "specified, since they are described through different APIs.",
                ],
                effort="L",
                risk="medium",
                blast_radius=[
                    "group-coordinator", "kafka-clients", "kafka-broker", "mirrormaker",
                ],
            ),
            DesignAlternative(
                label="B",
                name="MM2-local materialized view built by tailing `__consumer_offsets`",
                proposed_by=self.AGENT_NAME,
                approach=(
                    "MirrorMaker builds and owns its own reverse index inside the "
                    "MirrorCheckpointTask by consuming the compacted `__consumer_offsets` "
                    "topic on the source cluster and maintaining "
                    "`Map<TopicPartition, Set<String>>` in the Connect worker. No Kafka "
                    "protocol change and no broker change at all — findConsumerGroups() "
                    "reads its own local view instead of calling out to the coordinators. "
                    "On task startup the view is populated by replaying the compacted "
                    "topic from the beginning; afterwards it is updated from the tail."
                ),
                pros=[
                    "Zero cross-team dependency — MM2 can ship this alone, with no KIP, no "
                    "broker change, and no coordination cost.",
                    "Fastest path to relieving operator pain; weeks of calendar time saved "
                    "versus the KIP route.",
                    "No broker heap impact at all; the memory cost lands in the Connect "
                    "worker where this team controls the JVM sizing.",
                    "Fully reversible — an internal implementation detail of one connector "
                    "with no public API surface.",
                ],
                cons=[
                    "Creates a second copy of state the group coordinator already owns, "
                    "which is a correctness liability: any bug or lag in the tailer means "
                    "checkpointing against a stale subscription set.",
                    "Committed offsets in `__consumer_offsets` are not the same thing as "
                    "live subscriptions — a group that joined but has not committed yet is "
                    "invisible to this view, which is a real correctness gap during "
                    "rebalances.",
                    "Cold-start replay of the compacted topic on every task restart adds "
                    "startup latency and read bandwidth, and Connect restarts tasks often.",
                    "Requires read access to an internal topic, which many operators "
                    "restrict by ACL, so it will not work everywhere.",
                    "MM2 would own a permanent piece of consumer-group logic that belongs "
                    "to group-coordinator — an ownership boundary violation that costs us "
                    "every time the group protocol evolves.",
                ],
                effort="M",
                risk="high",
                blast_radius=["mirrormaker"],
            ),
            DesignAlternative(
                label="C",
                name="Batch describeConsumerGroups + parallel scan with early termination",
                proposed_by=self.AGENT_NAME,
                approach=(
                    "Keep the existing list-then-describe structure but fix the "
                    "pathological parts of it: batch the describeConsumerGroups calls into "
                    "batches of 500 instead of describing groups one coordinator round "
                    "trip at a time, issue those batches in parallel across coordinator "
                    "shards, and early-terminate once every replicated topic has been "
                    "matched. Purely a change inside MirrorCheckpointConnector — no broker "
                    "change, no protocol change, no new state anywhere."
                ),
                pros=[
                    "Entirely within MirrorMaker's ownership boundary; no other team needs "
                    "to be involved and no approvals are required.",
                    "Days of work rather than weeks, and very low risk — the semantics of "
                    "the operation are unchanged, only the call pattern.",
                    "No new state, so no drift, no rebuild, no staleness window.",
                    "Useful regardless of which other alternative wins, since it makes the "
                    "fallback path meaningfully faster.",
                ],
                cons=[
                    f"Still O(n_groups) asymptotically — realistic improvement is "
                    f"{investigation['latency']['p99_ms']}ms down to roughly 1-2s, which "
                    f"misses the {self.TARGET_DISCOVERY_P99_MS}ms target by ~4x.",
                    "Does not remove the load on the coordinators; batching reduces the "
                    "round-trip count but each coordinator still reads every group's "
                    "metadata.",
                    "Degrades again as clusters grow — at 500k groups we are back where we "
                    "started, so this buys time rather than solving the problem.",
                    "Parallel fan-out could make the thundering herd worse if batch "
                    "concurrency is tuned badly under failover conditions.",
                ],
                effort="S",
                risk="low",
                blast_radius=["mirrormaker"],
            ),
        ]

    # ------------------------------------------------------------------
    # Peer selection + request construction
    # ------------------------------------------------------------------

    def _select_peers(
        self, ticket: Ticket, investigation: dict[str, Any]
    ) -> list[tuple[str, str]]:
        """Return the teams whose code this change would touch."""
        return [
            ("group-coordinator", "GroupMetadataManager.java"),
            ("kafka-broker", "KafkaApis.scala"),
            ("kafka-clients", "ListGroupsRequest.json"),
        ]

    def _build_impact_request(
        self,
        peer_id: str,
        ticket: Ticket,
        investigation: dict[str, Any],
        codepath: str = "",
        alternatives=None,
        round_number: int = 1,
        prior_concerns=None,
    ) -> ImpactRequest:
        alternatives = alternatives or []
        latency = investigation["latency"]
        trace = investigation["trace"]

        evidence = (
            f"Checkpoint group discovery p99 = {latency['p99_ms']}ms on a cluster with "
            f"{trace['phases'][0]['groups_returned']:,} groups. "
            f"{latency['bottleneck_pct']}% of the {trace['total_ms']}ms is "
            f"listConsumerGroups + describeConsumerGroups fan-out. Only "
            f"{trace['phases'][2]['groups_matched']} of "
            f"{trace['phases'][0]['groups_returned']:,} groups matched "
            f"(0.008% hit rate). Target is p99 < {self.TARGET_DISCOVERY_P99_MS}ms."
        )

        question = self._question_for(peer_id, round_number, len(alternatives))

        return ImpactRequest.new(
            from_agent=self.AGENT_NAME,
            to_agent=peer_id,
            ticket_id=ticket.ticket_id,
            request_type="design_review",
            context=f"{investigation['root_cause']}\n\nEvidence:\n{evidence}",
            question=question,
            codepaths_of_interest=[codepath],
            proposed_change=(
                "Make MirrorCheckpointConnector's group discovery O(matched_groups) "
                "instead of O(n_groups), via a reverse (topic, partition) → group index."
            ),
            alternatives_on_table=alternatives,
            round_number=round_number,
            prior_concerns=prior_concerns or [],
        )

    def _question_for(self, peer_id: str, round_number: int, alt_count: int) -> str:
        """The specific question this peer gets in this round."""
        if peer_id == "group-coordinator":
            if round_number == 1:
                return (
                    f"I have {alt_count} alternatives on the table (A: broker-side reverse "
                    "index plus a ListGroups v6 filter, B: MM2-local materialized view off "
                    "`__consumer_offsets`, C: batch and parallelize the existing scan). "
                    "A needs you to own the index inside GroupMetadataManager. Is that "
                    "implementable, what does it cost in heap and rebuild time, and do you "
                    "see an implementation approach I have not considered? I am "
                    "specifically worried that B has a correctness gap because committed "
                    "offsets are not the same as live subscriptions — confirm or correct "
                    "that."
                )
            if round_number == 2:
                return (
                    "Given your round-1 answer, I want to converge on A. Which of your "
                    "implementation sub-options do you recommend, and what do kafka-broker "
                    "and kafka-clients say about routing and the protocol surface? Also "
                    "confirm the rebuild-on-load cost is acceptable with the scan path as "
                    "fallback."
                )
            return (
                "Final round: confirm you are agreed on A with the feature-flag rollout "
                "and shadow-mode validation, and that your test requirements are captured."
            )

        if peer_id == "kafka-broker":
            if round_number == 1:
                return (
                    "A filtered ListGroups has to be served by the brokers. Groups for one "
                    "topic can hash to any of the 50 `__consumer_offsets` partitions, so I "
                    "need to understand how a filtered request routes: does it fan out to "
                    "every coordinator shard, and if so does that undo the win? Also tell "
                    "me whether ~14MB of extra heap per 50k-group cluster is acceptable, "
                    "and whether any of this belongs in KRaft metadata instead."
                )
            if round_number == 2:
                return (
                    "Confirm the scatter-gather routing across all coordinator shards is "
                    "the right shape and that it still hits my latency target, and that "
                    "the heap budget is signed off with the config-capped fallback."
                )
            return (
                "Final round: confirm routing and heap are agreed, and that your test "
                "requirements for the fan-out path are captured."
            )

        # kafka-clients
        if round_number == 1:
            return (
                "Does a topic-partition filter on ListGroups need a KIP, and is there "
                "existing prior art? I want to know whether the public API surface is a "
                "version bump on ListGroups, a brand-new API key, or something else "
                "entirely. Give me the alternatives and the timeline for each, and flag "
                "anything about KIP-848 consumer groups that would make this change wrong."
            )
        if round_number == 2:
            return (
                "Confirm: can the broker-side index land and be validated ahead of the KIP "
                "vote, with MM2 migrating to the public v6 API once it merges, without "
                "creating a compatibility problem we cannot undo?"
            )
        return (
            "Final round: confirm the v6 tagged-field approach is agreed, and state "
            "exactly what needs Apache PMC authority versus what we can decide ourselves."
        )

    # ------------------------------------------------------------------
    # 1-pager section builders
    # ------------------------------------------------------------------

    def _build_background(self, ticket: Ticket, investigation: dict[str, Any]) -> str:
        latency = investigation["latency"]
        trace = investigation["trace"]
        return (
            "MirrorMaker 2 replicates topics asynchronously between Kafka clusters. To let "
            "a consumer fail over from the source cluster to the target, MM2 must "
            "translate that consumer group's committed offsets into equivalent target "
            "offsets and emit them as checkpoints. Doing that requires knowing every "
            "consumer group that consumes the topics being replicated.\n\n"
            f"`MirrorCheckpointConnector.findConsumerGroups()` discovers those groups by "
            f"calling `Admin.listConsumerGroups()` — which returns every group in the "
            f"cluster — and then `describeConsumerGroups()` on each one to read its "
            f"subscription. On a cluster with "
            f"{trace['phases'][0]['groups_returned']:,} consumer groups this takes "
            f"{latency['p99_ms']}ms at p99, and only "
            f"{trace['phases'][2]['groups_matched']} of those groups actually matter.\n\n"
            f"The cost lands at the worst possible moment. Checkpoint emission runs on a "
            f"{latency['checkpoint_interval_ms'] // 1000}s interval, so an "
            f"{latency['p99_ms']}ms discovery pass burns a fifth of the budget on every "
            f"cycle; during an active failover the coordinators are already under maximum "
            f"stress and the delay shows up directly as consumer lag. "
            f"{investigation['affected_flows']} active replication flows would benefit "
            f"from a fix, and the same problem affects any Kafka operator asking which "
            f"groups consume a topic."
        )

    def _build_tldr(
        self,
        ticket: Ticket,
        investigation: dict[str, Any],
        recommended: DesignAlternative | None,
    ) -> str:
        latency = investigation["latency"]
        base = (
            f"MM2 checkpoint group discovery is {latency['p99_ms']}ms at p99 against a "
            f"{self.TARGET_DISCOVERY_P99_MS}ms target because it scans all "
            f"{investigation['trace']['phases'][0]['groups_returned']:,} consumer groups "
            f"to find the {investigation['trace']['phases'][2]['groups_matched']} that "
            f"matter. Kafka has no reverse index from (topic, partition) to group."
        )
        if recommended:
            return (
                f"{base} We evaluated three alternatives and recommend "
                f"**{recommended.name}** (effort {recommended.effort}, risk "
                f"{recommended.risk}): it is the only option that reaches the latency "
                f"target without MirrorMaker taking ownership of consumer-group state it "
                f"should not own. The broker-side index lands behind a flag immediately; "
                f"the public API surface follows via KIP."
            )
        return base

    def _build_goals(self, ticket: Ticket, investigation: dict[str, Any]) -> list[str]:
        return investigation["goals"]

    def _build_non_goals(self, ticket: Ticket, investigation: dict[str, Any]) -> list[str]:
        return investigation["non_goals"]

    def _build_teams_involved(
        self, responses: list[ImpactResponse]
    ) -> list[TeamInvolvement]:
        teams = [TeamInvolvement(
            team="mirrormaker",
            role="owner",
            owns=(
                "MirrorMaker 2: cross-cluster replication, checkpointing, "
                "MirrorCheckpointConnector.java"
            ),
            sign_off_required=False,
            contribution=(
                "Diagnosed the O(n_groups) scan, quantified the impact, proposed the three "
                "alternatives, and drove the deliberation to a recommendation."
            ),
        )]

        role_by_agent = {
            "group-coordinator": (
                "implementer",
                "GroupMetadataManager.java, GroupCoordinatorShard.java, group state",
            ),
            "kafka-broker": (
                "implementer",
                "KafkaApis.scala request routing, broker heap budget, KRaft metadata",
            ),
            "kafka-clients": (
                "approver",
                "Kafka wire protocol, ListGroupsRequest.json, AdminClient, KIP process",
            ),
        }

        for resp in responses:
            if resp.timed_out or any(t.team == resp.from_agent for t in teams):
                continue
            role, owns = role_by_agent.get(resp.from_agent, ("implementer", ""))
            teams.append(TeamInvolvement(
                team=resp.from_agent,
                role="approver" if resp.needs_org_authority else role,
                owns=owns,
                sign_off_required=resp.needs_org_authority,
                contribution=resp.recommendation or resp.summary,
            ))

        return teams
