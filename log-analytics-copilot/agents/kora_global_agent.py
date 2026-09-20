"""
Kora Global SME Agent — Principal Engineer, Cluster Linking.

Domain: Cluster Linking — failover, offset clamping, mirror operations.
Runbook: runbooks/kora-global-sme.md

Tools
-----
  get_failover_latency(topic, last_hours=24)
  get_offset_clamp_trace(topic, partition)
  list_active_links()
  get_consumer_groups_for_link(link_id)

Behaviour
---------
Owns the clampOffsets ticket.  Proposes three genuinely different design
alternatives spanning the real solution space (correct-but-slow to ship,
fast-but-owns-duplicate-state, cheap-but-partial), then deliberates with
consumer-team and oss-kafka across up to 3 rounds before recommending one.
"""

from __future__ import annotations

from typing import Any

from agents.base_agent import SMEAgentBase, tool
from proto.sme_agents import (
    DesignAlternative,
    Finding,
    ImpactRequest,
    ImpactResponse,
    TeamInvolvement,
    Ticket,
)


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

    # As the owning agent this one authors the 1-pager and synthesizes three
    # teams' input, so it carries a standard tier rather than the cheapest.
    LLM_TIER = "standard"

    # Latency target that any acceptable alternative must hit
    TARGET_CLAMP_P99_MS = 50

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
            {"name": "listGroups",       "ms": 840,  "groups_returned": 50312},
            {"name": "describeGroups",   "ms": 8100, "groups_scanned": 50312},
            {"name": "filterMatch",      "ms": 180,  "groups_matched": 4},
            {"name": "applyTranslation", "ms": 80,   "offsets_clamped": 4},
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
            },
            {
                "link_id": "us-west-2-to-ap-south-1",
                "state": "ACTIVE",
                "mirrored_topics": 88,
                "replication_lag_ms": 410,
                "consumer_group_sync": True,
            },
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
    # Investigation
    # ------------------------------------------------------------------

    def _investigate(self, ticket: Ticket) -> dict[str, Any]:
        trace = self.get_offset_clamp_trace("orders", 3)
        latency = self.get_failover_latency("orders")
        links = self.list_active_links()
        groups_scanned = trace["phases"][0]["groups_returned"]
        matched = trace["phases"][2]["groups_matched"]

        return {
            "agent": self.AGENT_NAME,
            "ticket_id": ticket.ticket_id,
            "trace": trace,
            "latency": latency,
            "affected_links": len(links),
            "root_cause": (
                f"clampOffsets() has no reverse index from (topic, partition) to group. "
                f"It calls AdminClient.listGroups() to fetch all {groups_scanned:,} groups, "
                f"then fan-outs describeGroups() per group to read subscriptions, and only "
                f"{matched} of them actually match the failover topics "
                f"({matched / groups_scanned:.3%} hit rate). "
                f"{latency['bottleneck_pct']}% of the {trace['total_ms']}ms is spent in those "
                f"two calls, and the fan-out lands on GroupCoordinator at exactly the moment "
                f"it is most stressed."
            ),
            "summary": (
                f"clampOffsets p99 is {latency['p99_ms']}ms against a "
                f"{self.TARGET_CLAMP_P99_MS}ms target, caused by an O(n_groups) scan "
                f"on a path that needs O(matched_groups)."
            ),
            "cited_codepaths": [
                "OffsetClampingService.java",
                "confluent/kora-cluster-linking/",
                "LinkAdminClient.java",
            ],
            "goals": [
                f"Reduce clampOffsets p99 from {latency['p99_ms']}ms to "
                f"under {self.TARGET_CLAMP_P99_MS}ms on a 50k-group cluster.",
                "Eliminate the describeGroups fan-out that thundering-herds "
                "GroupCoordinator during failover.",
                "Keep offset-translation correctness unchanged — no consumer may "
                "resume at an invalid destination offset.",
                "Make the lookup cost scale with the number of *matched* groups, "
                "not the total number of groups in the cluster.",
            ],
            "non_goals": [
                "Changing the offset translation algorithm itself "
                "(OffsetTranslationTable is correct and is not in scope).",
                "Improving general-purpose listGroups performance for non-failover "
                "callers.",
                "Redesigning consumer group sync (GroupSyncService) — separate concern.",
                "Reducing replication lag on the mirror path — unrelated to this hot path.",
                "Supporting KIP-848-style consumer groups in the first iteration "
                "(tracked separately once oss-kafka rules on semantics).",
            ],
            "execution_order": [
                "Consumer Team adds the reverse index to GroupCoordinator behind a "
                "feature flag, with metrics on index size and hit rate.",
                "Kora Global switches OffsetClampingService to the indexed lookup path, "
                "keeping the old scan behind the same flag as a fallback.",
                "OSS Kafka team drafts and posts the ListGroups v5 KIP for the public "
                "API surface, referencing KIP-518 as prior art.",
                "Kora ships the internal fast path to production ahead of the KIP vote; "
                "validate p99 on a 50k-group staging cluster.",
                "Once the KIP merges, migrate Kora from the Confluent-internal call to "
                "the public v5 API and delete the fallback scan.",
            ],
            "risks_and_mitigations": [
                "**Index drift** — reverse index disagrees with groupMetadataCache. "
                "Mitigation: build both paths, run the scan in shadow mode for one week "
                "and alert on any mismatch before trusting the index.",
                "**Coordinator failover rebuild** — index must be rebuilt from "
                "`__consumer_offsets` replay (~800ms for 50k groups). "
                "Mitigation: serve the old scan path while a rebuild is in progress.",
                "**Broker heap growth** — ~14MB per 50k-group cluster. "
                "Mitigation: config-capped index size with a documented fallback to "
                "full scan when the cap is hit.",
                "**KIP timeline slip** — the Apache vote is outside Confluent's control. "
                "Mitigation: the internal fast path ships independently, so customer "
                "impact is fixed regardless of the KIP schedule.",
            ],
            "rollout_and_rollback": [
                "Feature flag `cluster.link.clamp.use.tp.index` defaults off; enable per "
                "cluster starting with the smallest group counts.",
                "Shadow mode first: run indexed lookup and full scan in parallel, compare "
                "results, emit a mismatch metric, do not act on the index.",
                "Rollback is flipping the flag off — the scan path stays in the binary "
                "until the KIP-backed v5 API is in production everywhere.",
                "Bake for one full failover drill per region before enabling by default.",
            ],
            "success_metrics": [
                f"clampOffsets p99 < {self.TARGET_CLAMP_P99_MS}ms on a 50k-group cluster "
                f"(from {latency['p99_ms']}ms).",
                "Zero index/scan mismatches over a 7-day shadow-mode window.",
                "describeGroups call volume during failover drops by >99%.",
                "No increase in consumer lag spikes attributable to failover.",
                "GroupCoordinator p99 request latency unchanged or improved during failover.",
            ],
            "testing_strategy": [
                "[kora-global] Unit: `clampOffsets` returns identical group sets from the "
                "indexed path and the scan path for 1k randomized subscription fixtures.",
                "[kora-global] Unit: fallback to scan when the index reports unavailable.",
                "[kora-global] Integration: failover drill on a 50k-group staging cluster, "
                f"asserting p99 < {self.TARGET_CLAMP_P99_MS}ms.",
                "[kora-global] Correctness: no consumer resumes at an offset outside the "
                "valid destination range, verified across 100 simulated failovers.",
                "[kora-global] Load: describeGroups call count during failover must be zero "
                "on the indexed path.",
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
                name="Broker-side reverse index + ListGroups v5 topic-partition filter",
                proposed_by=self.AGENT_NAME,
                approach=(
                    "Consumer Team adds a `topicPartitionToGroups: Map<TopicPartition, "
                    "Set<GroupId>>` to GroupCoordinator, maintained incrementally on "
                    "JoinGroup / LeaveGroup / heartbeat-timeout, and rebuilt from "
                    "`__consumer_offsets` replay on coordinator failover. OSS Kafka adds an "
                    "optional `topic_partitions` tagged field to ListGroupsRequest v5. "
                    "clampOffsets then issues a single filtered ListGroups call and gets "
                    "back only the groups that actually subscribe to the failover "
                    "partitions, making the call O(matched) instead of O(n_groups). "
                    "Kora can enable the broker-side index ahead of the KIP and switch to "
                    "the public v5 API once it merges."
                ),
                pros=[
                    "Fixes the root cause rather than the symptom — the lookup becomes "
                    "O(matched_groups), which is what the algorithm actually needs.",
                    "Single source of truth: the index lives next to the data it indexes, "
                    "so there is no cross-process state to drift.",
                    "Eliminates the describeGroups fan-out entirely, which removes the "
                    "thundering herd on GroupCoordinator during failover.",
                    "Benefits open-source users too — MirrorMaker 2 and any self-managed "
                    "DR setup has the identical problem.",
                    "The server-side index can ship independently of the KIP, so customer "
                    "pain is relieved without waiting on the Apache process.",
                ],
                cons=[
                    "Requires a KIP with a ~6 week discussion-and-vote timeline for the "
                    "public API surface, which Confluent does not control.",
                    "Cross-team dependency on two teams (consumer-team to implement, "
                    "oss-kafka to shepherd the protocol change).",
                    "Adds ~14MB of broker heap per 50k-group cluster, which needs "
                    "broker-team sign-off.",
                    "Index rebuild on coordinator failover costs ~800ms, during which the "
                    "fallback path must be used.",
                    "KIP-848 interaction is genuinely unresolved — new-protocol groups use "
                    "a different describe API.",
                ],
                effort="L",
                risk="medium",
                blast_radius=["consumer-team", "oss-kafka", "broker-team", "kora-global"],
            ),
            DesignAlternative(
                label="B",
                name="Kora-side materialized view built by tailing `__consumer_offsets`",
                proposed_by=self.AGENT_NAME,
                approach=(
                    "Cluster Linking builds and owns its own reverse index inside the "
                    "Kora Cluster Linking service by consuming the compacted "
                    "`__consumer_offsets` topic and maintaining "
                    "`Map<TopicPartition, Set<GroupId>>` in the link process. No Kafka "
                    "protocol change and no broker change at all — clampOffsets reads its "
                    "own local view instead of calling out to GroupCoordinator. On link "
                    "startup the view is populated by replaying the compacted topic from "
                    "the beginning; afterwards it is updated incrementally from the tail."
                ),
                pros=[
                    "Zero cross-team dependency — Kora Global can ship this alone, with no "
                    "KIP, no broker change, and no coordination cost.",
                    "Fastest path to relieving customer pain; weeks of calendar time saved "
                    "versus the KIP route.",
                    "No broker heap impact at all; the memory cost lands in the Cluster "
                    "Linking service where this team controls the JVM sizing.",
                    "Fully reversible — it is an internal implementation detail of one "
                    "service with no public API surface.",
                ],
                cons=[
                    "Creates a second copy of state that GroupCoordinator already owns, "
                    "which is a correctness liability: any bug or lag in the tailer means "
                    "clamping against a stale subscription set.",
                    "Committed offsets in `__consumer_offsets` are not the same thing as "
                    "live subscriptions — a group that joined but has not committed yet is "
                    "invisible to this view, which is a real correctness gap during "
                    "rebalances.",
                    "Cold-start replay of the compacted topic on every link process restart "
                    "adds startup latency and read bandwidth.",
                    "Does nothing for open-source users, so the same problem gets solved "
                    "again upstream later.",
                    "Kora now owns a permanent piece of consumer-group logic that properly "
                    "belongs to consumer-team — an ownership boundary violation that will "
                    "cost us every time the group protocol evolves.",
                ],
                effort="M",
                risk="high",
                blast_radius=["kora-global"],
            ),
            DesignAlternative(
                label="C",
                name="Batch describeGroups + parallel scan with early termination",
                proposed_by=self.AGENT_NAME,
                approach=(
                    "Keep the existing listGroups-then-filter structure but fix the "
                    "pathological parts of it: batch the describeGroups calls (currently "
                    "one RPC per group) into batches of 500, issue those batches in "
                    "parallel across coordinator partitions, and early-terminate the scan "
                    "once every failover partition has been matched. Purely a change "
                    "inside OffsetClampingService and LinkAdminClient — no broker change, "
                    "no protocol change, no new state anywhere."
                ),
                pros=[
                    "Entirely within Kora Global's ownership boundary; no other team needs "
                    "to be involved and no approvals are required.",
                    "Days of work rather than weeks, and very low risk — the semantics of "
                    "the operation are unchanged, only the call pattern.",
                    "No new state, so no drift, no rebuild, no staleness window.",
                    "Useful regardless of which other alternative wins, since it makes the "
                    "fallback path meaningfully faster.",
                ],
                cons=[
                    f"Still O(n_groups) asymptotically — realistic improvement is "
                    f"{investigation['latency']['p99_ms']}ms down to roughly 1–2s, which "
                    f"misses the {self.TARGET_CLAMP_P99_MS}ms target by ~20x.",
                    "Does not remove the load on GroupCoordinator; batching reduces the RPC "
                    "count but the coordinator still reads every group's metadata.",
                    "Degrades again as clusters grow — at 500k groups we are back where we "
                    "started, so this buys time rather than solving the problem.",
                    "Parallel fan-out could make the thundering-herd worse if batch "
                    "concurrency is tuned badly under failover conditions.",
                ],
                effort="S",
                risk="low",
                blast_radius=["kora-global"],
            ),
        ]

    # ------------------------------------------------------------------
    # Peer selection + request construction
    # ------------------------------------------------------------------

    def _select_peers(
        self, ticket: Ticket, investigation: dict[str, Any]
    ) -> list[tuple[str, str]]:
        """Look up owning teams for codepaths this change would touch."""
        return [
            ("consumer-team", "GroupCoordinator.scala"),
            ("oss-kafka", "ListGroupsRequest.json"),
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
            f"clampOffsets p99 = {latency['p99_ms']}ms on a cluster with "
            f"{trace['phases'][0]['groups_returned']:,} groups. "
            f"{latency['bottleneck_pct']}% of the {trace['total_ms']}ms is listGroups + "
            f"describeGroups fan-out. Only {trace['phases'][2]['groups_matched']} of "
            f"{trace['phases'][0]['groups_returned']:,} groups matched "
            f"(0.008% hit rate). Target is p99 < {self.TARGET_CLAMP_P99_MS}ms."
        )

        if peer_id == "consumer-team":
            if round_number == 1:
                question = (
                    "I have three alternatives on the table (A: broker-side reverse index "
                    "plus ListGroups v5 filter, B: Kora-side materialized view off "
                    "`__consumer_offsets`, C: batch and parallelize the existing scan). "
                    "A needs you to own the index inside GroupCoordinator. Is that "
                    "implementable, what does it cost in heap and rebuild time, and do you "
                    "see an implementation approach I have not considered? I am "
                    "specifically worried that B has a correctness gap because committed "
                    "offsets are not the same as live subscriptions — confirm or correct "
                    "that."
                )
            elif round_number == 2:
                question = (
                    "Given your round-1 answer, I want to converge on A. Which of your "
                    "implementation sub-options do you recommend, and what does oss-kafka "
                    "say about the protocol surface? Also confirm the rebuild-on-failover "
                    "cost is acceptable with the scan path as fallback."
                )
            else:
                question = (
                    "Final round: confirm you are agreed on A with the feature-flag "
                    "rollout and shadow-mode validation, and that your test requirements "
                    "are captured."
                )
        else:  # oss-kafka
            if round_number == 1:
                question = (
                    "Does a topic-partition filter on ListGroups need a KIP, and is there "
                    "existing prior art? I want to know whether the public API surface is a "
                    "version bump on ListGroups, a brand-new API key, or something we keep "
                    "Confluent-internal. Give me the alternatives and the timeline for "
                    "each, and flag anything about KIP-848 that would make this change "
                    "wrong."
                )
            elif round_number == 2:
                question = (
                    "Confirm: can Kora ship the broker-side index and an internal call "
                    "ahead of the KIP vote, then migrate to the public v5 API once it "
                    "merges, without creating a compatibility problem we cannot undo?"
                )
            else:
                question = (
                    "Final round: confirm the v5 tagged-field approach is agreed, and state "
                    "exactly what needs Apache PMC authority versus what we can decide "
                    "ourselves."
                )

        return ImpactRequest.new(
            from_agent=self.AGENT_NAME,
            to_agent=peer_id,
            ticket_id=ticket.ticket_id,
            request_type="design_review",
            context=f"{investigation['root_cause']}\n\nEvidence:\n{evidence}",
            question=question,
            codepaths_of_interest=[codepath],
            proposed_change=(
                "Make clampOffsets' group lookup O(matched_groups) instead of "
                "O(n_groups), via a reverse (topic, partition) → group index."
            ),
            alternatives_on_table=alternatives,
            round_number=round_number,
            prior_concerns=prior_concerns or [],
        )

    # ------------------------------------------------------------------
    # 1-pager section builders
    # ------------------------------------------------------------------

    def _build_background(self, ticket: Ticket, investigation: dict[str, Any]) -> str:
        latency = investigation["latency"]
        trace = investigation["trace"]
        return (
            "Cluster Linking mirrors topics between Kafka clusters. When a consumer fails "
            "over from the source to the destination cluster, its committed offsets must be "
            "clamped to the closest valid destination offset, which requires knowing every "
            "consumer group subscribed to the topics being failed over.\n\n"
            f"`OffsetClampingService.clampOffsets()` discovers those groups by calling "
            f"`AdminClient.listGroups()` — which returns every group in the cluster — and "
            f"then calling `describeGroups()` per group to read its subscription. On a "
            f"cluster with {trace['phases'][0]['groups_returned']:,} consumer groups this "
            f"takes {latency['p99_ms']}ms at p99, and only "
            f"{trace['phases'][2]['groups_matched']} of those groups actually matter.\n\n"
            f"The cost lands at the worst possible moment: during an active failover, when "
            f"GroupCoordinator is already under maximum stress, and the delay shows up "
            f"directly as consumer lag spikes. "
            f"{investigation['affected_links']} active links would benefit from a fix."
        )

    def _build_tldr(
        self,
        ticket: Ticket,
        investigation: dict[str, Any],
        recommended: DesignAlternative | None,
    ) -> str:
        latency = investigation["latency"]
        base = (
            f"`clampOffsets()` is {latency['p99_ms']}ms at p99 against a "
            f"{self.TARGET_CLAMP_P99_MS}ms target because it scans all "
            f"{investigation['trace']['phases'][0]['groups_returned']:,} consumer groups "
            f"to find the {investigation['trace']['phases'][2]['groups_matched']} that "
            f"matter. There is no reverse index from (topic, partition) to group."
        )
        if recommended:
            return (
                f"{base} We evaluated three alternatives and recommend "
                f"**{recommended.name}** (effort {recommended.effort}, risk "
                f"{recommended.risk}): it is the only option that reaches the latency "
                f"target without Cluster Linking taking ownership of consumer-group state "
                f"it should not own. The broker-side index ships behind a flag immediately; "
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
            team="kora-global",
            role="owner",
            owns="Cluster Linking: failover, offset clamping, OffsetClampingService.java",
            sign_off_required=False,
            contribution=(
                "Diagnosed the O(n_groups) scan, quantified the impact, proposed the three "
                "alternatives, and drove the deliberation to a recommendation."
            ),
        )]

        role_by_agent = {
            "consumer-team": (
                "implementer",
                "GroupCoordinator.scala, GroupMetadata.scala, consumer group state",
            ),
            "oss-kafka": (
                "approver",
                "Kafka wire protocol, ListGroupsRequest.json, KIP process",
            ),
            "broker-team": (
                "notified",
                "Broker JVM heap and GC configuration",
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

        # broker-team is affected by the heap increase even though it was not
        # consulted directly in this round.
        if any(r.from_agent == "consumer-team" for r in responses) and \
                not any(t.team == "broker-team" for t in teams):
            teams.append(TeamInvolvement(
                team="broker-team",
                role="notified",
                owns="Broker JVM heap and GC configuration",
                sign_off_required=False,
                contribution=(
                    "Needs to be looped in on the ~14MB per-cluster heap increase before "
                    "the index is enabled by default."
                ),
            ))

        return teams
