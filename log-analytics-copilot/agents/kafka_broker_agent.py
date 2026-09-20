"""
Kafka Broker SME Agent — Principal Engineer, broker request handling and KRaft.

Domain: Broker request routing (KafkaApis), KRaft controller and metadata,
broker heap and GC budget.
Runbook: runbooks/kafka-broker-sme.md

Tools
-----
  get_request_routing(api_name)
  get_coordinator_distribution(topic)
  get_broker_heap_profile(broker_id)
  get_kraft_metadata_budget()

Behaviour
---------
This agent exists because the other three teams can agree on a perfect index
and still ship something that does not work. Groups hash to coordinator shards
by group id, so the groups consuming a single topic are spread across *every*
`__consumer_offsets` shard. A filtered ListGroups therefore still has to fan
out to all of them — the index changes what each shard does, not how many
shards are involved. Nobody else in the deliberation owns that fact, which is
exactly why the broker team has to be in the room.
"""

from __future__ import annotations

from agents.base_agent import SMEAgentBase, tool
from proto.sme_agents import DesignAlternative, ImpactRequest, ImpactResponse


class KafkaBrokerAgent(SMEAgentBase):
    AGENT_NAME = "kafka-broker"
    DOMAIN = (
        "Broker request routing (KafkaApis), KRaft controller and metadata, "
        "broker heap and GC budget"
    )
    OWNS = [
        "core/src/main/scala/kafka/server/",
        "KafkaApis.scala",
        "BrokerServer.scala",
        "KafkaConfig.scala",
        "metadata/src/main/java/org/apache/kafka/controller/",
        "QuorumController.java",
        "MetadataImage.java",
    ]

    # Request-routing and heap-budget reasoning is quantitative work over tool
    # output; the design review escalates to LLM_DESIGN_TIER on its own.
    LLM_TIER = "small"

    # A filtered ListGroups must stay well inside the broker request timeout
    # even when it fans out to every coordinator shard.
    TARGET_FANOUT_P99_MS = 50

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    @tool("get_request_routing")
    def get_request_routing(self, api_name: str) -> dict:
        """Describe how ``api_name`` is routed and fanned out inside the broker."""
        if not api_name:
            raise ValueError("api_name is required")
        if api_name.upper() == "LISTGROUPS":
            return {
                "api": "ListGroups",
                "handler": "KafkaApis.handleListGroupsRequest",
                "routing": "fan_out_all_coordinator_shards",
                "coordinator_shard_count": 50,
                "reason": (
                    "A group's coordinator shard is abs(group_id.hashCode()) % 50, which is "
                    "independent of which topics that group consumes. Groups consuming one "
                    "topic are therefore distributed across all 50 shards, so any "
                    "ListGroups call — filtered or not — must query every shard."
                ),
                "current_per_shard_cost": "O(groups_on_shard) metadata walk",
                "aggregation": "union of per-shard results, errors surfaced per shard",
            }
        return {
            "api": api_name,
            "handler": "unknown",
            "routing": "unknown",
            "note": f"routing not modelled for {api_name}",
        }

    @tool("get_coordinator_distribution")
    def get_coordinator_distribution(self, topic: str) -> dict:
        """Show how groups consuming ``topic`` spread across coordinator shards."""
        if not topic:
            raise ValueError("topic is required")
        return {
            "topic": topic,
            "matched_groups": 4,
            "shards_hosting_matched_groups": 4,
            "total_shards": 50,
            "shards_that_must_be_queried": 50,
            "note": (
                "Only 4 shards actually hold a matching group, but the broker cannot know "
                "which 4 without asking, because group→shard placement is by group id "
                "hash, not by subscribed topic. The fan-out is unavoidable; what the index "
                "changes is the per-shard cost, from a full metadata walk to a hash lookup."
            ),
        }

    @tool("get_broker_heap_profile")
    def get_broker_heap_profile(self, broker_id: int) -> dict:
        """Return heap usage and headroom for ``broker_id``."""
        if broker_id < 0:
            raise ValueError("broker_id must be >= 0")
        heap_total_mb = 6144
        heap_used_mb = 3890
        return {
            "broker_id": broker_id,
            "heap_total_mb": heap_total_mb,
            "heap_used_mb": heap_used_mb,
            "heap_headroom_mb": heap_total_mb - heap_used_mb,
            "gc_collector": "G1",
            "gc_pause_p99_ms": 18,
            "largest_consumers": [
                {"component": "log index / page cache metadata", "mb": 1650},
                {"component": "replica fetcher buffers", "mb": 890},
                {"component": "group metadata", "mb": 740},
                {"component": "metadata image", "mb": 610},
            ],
        }

    @tool("get_kraft_metadata_budget")
    def get_kraft_metadata_budget(self) -> dict:
        """Return the churn budget for the `__cluster_metadata` log."""
        return {
            "metadata_log_size_mb": 240,
            "records_per_second_steady_state": 12,
            "snapshot_interval_records": 20_000,
            "design_intent": (
                "The metadata log carries cluster topology — topics, partitions, broker "
                "registrations, ACLs — which change rarely. It is replicated to every "
                "broker and replayed on startup."
            ),
            "consumer_group_churn_per_second": 380,
            "verdict": (
                "Consumer group membership churn is ~32x the steady-state metadata record "
                "rate. Putting group subscriptions in the metadata log would dominate it "
                "and slow broker startup for every broker in the cluster."
            ),
        }

    # ------------------------------------------------------------------
    # Routing alternatives
    # ------------------------------------------------------------------

    def _routing_alternatives(
        self, routing: dict, distribution: dict, heap: dict, metadata: dict
    ) -> list[DesignAlternative]:
        return [
            DesignAlternative(
                label="A",
                name="Scatter-gather across all coordinator shards, indexed per shard",
                proposed_by=self.AGENT_NAME,
                approach=(
                    "Keep the existing ListGroups fan-out in "
                    "`KafkaApis.handleListGroupsRequest` exactly as it is: query all "
                    f"{routing['coordinator_shard_count']} coordinator shards in parallel "
                    "and union the results. The only change is what each shard does with "
                    "the request — when the topic_partitions filter is present it performs "
                    "a hash lookup in its local TopicPartitionGroupIndex instead of walking "
                    "every group it hosts. Each shard returns only its matching groups, so "
                    "the response stays small even though the fan-out is wide. Per-shard "
                    "errors are surfaced individually so a single slow shard degrades the "
                    "result rather than failing the whole request."
                ),
                pros=[
                    "No change to request routing at all, which is the part of the broker "
                    "with the widest blast radius — we are only changing the per-shard "
                    "lookup, not the dispatch.",
                    f"The fan-out is already how ListGroups works today across "
                    f"{routing['coordinator_shard_count']} shards, so there is no new "
                    "failure mode to reason about and no new timeout behaviour.",
                    "Per-shard cost drops from a full metadata walk to a hash lookup, which "
                    f"is where the {distribution['matched_groups']}-of-50k win actually "
                    "comes from.",
                    "Shards are queried in parallel, so wall-clock latency is one shard's "
                    "lookup plus aggregation, not the sum.",
                    "Degrades safely: a shard without the index built yet falls back to its "
                    "own scan, and the union is still correct.",
                ],
                cons=[
                    f"Still contacts all {routing['coordinator_shard_count']} shards even "
                    f"though only {distribution['shards_hosting_matched_groups']} hold a "
                    "matching group, so there is a fixed floor on request count that the "
                    "index cannot remove.",
                    "Tail latency is governed by the slowest shard, so one GC pause on one "
                    "broker sets the p99 for the whole call.",
                    "Does not reduce request *count* on the cluster, only per-request work; "
                    "operators watching RPC rate will not see it drop.",
                ],
                effort="S",
                risk="low",
                blast_radius=["kafka-broker", "group-coordinator"],
            ),
            DesignAlternative(
                label="B",
                name="Global topic→group index materialized in KRaft metadata",
                proposed_by=self.AGENT_NAME,
                approach=(
                    "Publish group subscriptions into the `__cluster_metadata` log so the "
                    "QuorumController maintains one authoritative topic→group mapping in "
                    "MetadataImage. Any broker could then answer a filtered ListGroups from "
                    "its local metadata image with a single lookup and no fan-out at all."
                ),
                pros=[
                    "Eliminates the fan-out entirely — one local lookup on whichever broker "
                    "receives the request, which is the theoretically cleanest answer.",
                    "The mapping becomes strongly consistent and survives broker restart "
                    "with no rebuild, since every broker replays the metadata log anyway.",
                ],
                cons=[
                    f"Fundamentally misuses the metadata log. Measured consumer group churn "
                    f"is {metadata['consumer_group_churn_per_second']} changes/sec against a "
                    f"steady-state metadata rate of "
                    f"{metadata['records_per_second_steady_state']} records/sec — roughly "
                    f"32x. Group membership would dominate the log it was put in.",
                    "Every metadata record is replicated to every broker and replayed on "
                    "startup, so this directly slows broker startup cluster-wide and grows "
                    "snapshot size without bound.",
                    "Couples consumer group membership to controller availability. Today a "
                    "rebalance does not need the controller; this would make it so, which "
                    "is a serious availability regression.",
                    "Contradicts the stated design intent of the metadata log, so it would "
                    "rightly be rejected in KIP review on architectural grounds.",
                ],
                effort="XL",
                risk="high",
                blast_radius=[
                    "kafka-broker", "group-coordinator", "KRaft controller",
                    "every broker in the cluster",
                ],
            ),
            DesignAlternative(
                label="C",
                name="Co-locate groups on shards by subscribed topic",
                proposed_by=self.AGENT_NAME,
                approach=(
                    "Change how a group is assigned to a coordinator shard: instead of "
                    "`abs(group_id.hashCode()) % 50`, derive the shard from the group's "
                    "primary subscribed topic, so all groups consuming a topic land on the "
                    "same shard and a filtered ListGroups can be routed to exactly one "
                    "coordinator."
                ),
                pros=[
                    "Turns the fan-out into a single targeted request, which is the best "
                    "possible request-count outcome.",
                    "Would also speed up unrelated per-topic group operations.",
                ],
                cons=[
                    "The group→shard hash is effectively a public contract. Every client "
                    "library computes it to find the coordinator via FindCoordinator, and "
                    "`__consumer_offsets` records are already partitioned by it. Changing it "
                    "is not migratable without a flag day across every client.",
                    "Groups routinely subscribe to multiple topics, so 'primary topic' is "
                    "not well defined and would change as subscriptions change — meaning a "
                    "group's coordinator could move, which the protocol does not support.",
                    "Destroys load balance: a popular topic would concentrate all its groups "
                    "on one shard, creating a hotspot far worse than the problem being "
                    "solved.",
                    "No credible migration path for existing `__consumer_offsets` data.",
                ],
                effort="XL",
                risk="high",
                blast_radius=[
                    "kafka-broker", "group-coordinator", "kafka-clients",
                    "all client libraries", "operators",
                ],
            ),
        ]

    # ------------------------------------------------------------------
    # Consultation handler — round-aware
    # ------------------------------------------------------------------

    def _handle_consultation(
        self, request: ImpactRequest, depth: int
    ) -> ImpactResponse:
        """Answer the routing and heap questions with real numbers."""
        routing = self.get_request_routing("LISTGROUPS")
        distribution = self.get_coordinator_distribution("orders")
        heap = self.get_broker_heap_profile(12)
        metadata = self.get_kraft_metadata_budget()
        alternatives = self._routing_alternatives(routing, distribution, heap, metadata)

        alternatives[0].recommended = True
        alternatives[1].rejected_reason = (
            f"Consumer group churn is ~32x the metadata log's steady-state record rate, so "
            f"this would dominate `__cluster_metadata`, slow startup for every broker, and "
            f"couple rebalancing to controller availability."
        )
        alternatives[2].rejected_reason = (
            "The group→shard hash is a de facto public contract used by every client "
            "library and by the existing `__consumer_offsets` layout. Not migratable, and "
            "it would create per-topic hotspots."
        )

        index_mb = 14
        heap_pct = index_mb / heap["heap_total_mb"] * 100
        round_num = request.round_number

        new_concerns: list[str] = []
        if round_num == 1:
            new_concerns = [
                f"Important correction to the design as proposed: a filtered ListGroups "
                f"cannot be a single targeted lookup. Group→coordinator placement is "
                f"`abs(group_id.hashCode()) % {routing['coordinator_shard_count']}`, which "
                f"has nothing to do with subscribed topics, so the groups consuming one "
                f"topic are spread across all "
                f"{routing['coordinator_shard_count']} shards. The request must still fan "
                f"out to every shard. The index makes each shard's work O(1) instead of "
                f"O(groups_on_shard) — that is the real win, and it is still worth it, but "
                f"the fan-out does not go away and the design doc should say so.",
                f"Tail latency on the fan-out is set by the slowest shard, so a single G1 "
                f"pause (currently p99 {heap['gc_pause_p99_ms']}ms) shows up in the "
                f"caller's p99. Budget for that rather than assuming the index lookup cost.",
                f"Heap is acceptable but not free: {index_mb}MB against "
                f"{heap['heap_headroom_mb']}MB headroom is {heap_pct:.2f}% of total heap. "
                f"We want the config-capped size with a documented fallback to scan when "
                f"the cap is hit, not an unbounded map.",
                "Per-shard errors must be surfaced individually. If one coordinator is "
                "unavailable the caller needs to know the result is partial rather than "
                "receiving a silently short list — MM2 checkpointing against a partial "
                "group set is the same correctness bug we are trying to avoid.",
            ]
            verdict = "needs_changes"
            summary = (
                f"Routing is fine but the design's framing is wrong in one important way: "
                f"the filtered call still fans out to all "
                f"{routing['coordinator_shard_count']} coordinator shards, because group "
                f"placement is by group-id hash and is unrelated to subscribed topics. "
                f"Only {distribution['shards_hosting_matched_groups']} shards actually hold "
                f"a match, but the broker cannot know which ones without asking. The index "
                f"is still the right fix — it changes per-shard cost from a full metadata "
                f"walk to a hash lookup, and shards are queried in parallel so wall-clock "
                f"latency is one lookup plus aggregation. Heap at {index_mb}MB is "
                f"{heap_pct:.2f}% of total and acceptable with a config cap. We reject "
                f"putting this in KRaft metadata (churn is ~32x the log's steady-state "
                f"rate) and reject re-hashing group placement (breaks every client)."
            )
        elif round_num == 2:
            verdict = "needs_changes"
            new_concerns = [
                "Confirming one thing before we sign off: the partial-result semantics need "
                "to be in the KIP, not just the implementation, because clients have to "
                "know how to interpret a response where some shards errored."
            ]
            summary = (
                f"Agreed on alternative A: scatter-gather unchanged, per-shard indexed "
                f"lookup, results unioned. Expected p99 is comfortably inside the "
                f"{self.TARGET_FANOUT_P99_MS}ms target since the shards are queried in "
                f"parallel and each does a hash lookup. Heap budget signed off at "
                f"{index_mb}MB with the config cap and scan fallback. Asking that "
                f"partial-result semantics be specified in the KIP rather than left to the "
                f"implementation."
            )
        else:
            verdict = "agreed"
            summary = (
                f"Agreed and settled from our side. Routing unchanged, per-shard indexed "
                f"lookup, parallel fan-out, union of results with per-shard errors "
                f"surfaced. Heap capped by config with scan fallback. Our test requirements "
                f"for the fan-out and partial-failure paths are captured below."
            )

        return ImpactResponse(
            request_id=request.request_id,
            from_agent=self.AGENT_NAME,
            to_agent=request.from_agent,
            verdict=verdict,
            confidence=0.93,
            summary=summary,
            principal_review=(
                "I want to correct the mental model in the proposal, because it matters for "
                "what the team actually builds. The framing so far has been 'replace an "
                "O(n_groups) scan with an O(1) index lookup', which is right about the work "
                "but wrong about the shape of the request. A group's coordinator shard is "
                "chosen by hashing its group id, and that has no relationship to which "
                "topics the group consumes. So the groups consuming a single topic are "
                "scattered across all 50 `__consumer_offsets` shards, and the broker has no "
                "way to know which shards hold a match without asking all of them. The "
                "fan-out is structural and the index does not remove it.\n\n"
                "That is not an argument against the index — it is an argument for being "
                "precise about where the win comes from. Today each of the 50 shards walks "
                "every group it hosts, and the caller then issues describeConsumerGroups "
                "across 50k groups. With the index each shard does a hash lookup and "
                "returns only its matches, and the describe fan-out disappears entirely. "
                "The shards are already queried in parallel, so wall-clock latency is one "
                "lookup plus aggregation. That comfortably meets the target; it just is not "
                "a single-RPC story, and a design doc that claims otherwise will mislead "
                "whoever implements it.\n\n"
                "On the two alternatives I am ruling out: putting group subscriptions in the "
                "KRaft metadata log is the kind of idea that looks elegant and is "
                "architecturally wrong. The metadata log exists for cluster topology that "
                "changes rarely, is replicated to every broker, and is replayed at startup. "
                "Group membership churns at roughly 32x the log's steady-state record rate; "
                "it would dominate the log, grow snapshots without bound, slow every "
                "broker's startup, and — worst of all — make rebalancing depend on "
                "controller availability, which it does not today. Re-hashing group "
                "placement to co-locate by topic is worse still: that hash is a de facto "
                "public contract baked into every client library and into the existing "
                "`__consumer_offsets` layout, and it would create per-topic hotspots.\n\n"
                "Finally, partial results. With a 50-way fan-out, some shard will sometimes "
                "be unavailable. The response must distinguish 'these are all the matching "
                "groups' from 'these are the matching groups we could reach', and that "
                "distinction belongs in the protocol, not in an implementation note. MM2 "
                "checkpointing against a silently truncated group set is precisely the "
                "correctness failure this whole ticket is trying to prevent."
            ),
            design_alternatives=alternatives,
            recommendation=(
                "Alternative A: keep the existing scatter-gather and make each coordinator "
                "shard serve the filter from its local index. It requires no routing change, "
                "introduces no new failure mode, and the parallel fan-out means wall-clock "
                f"latency stays inside the {self.TARGET_FANOUT_P99_MS}ms target."
            ),
            cited_codepaths=[
                "KafkaApis.scala",
                "BrokerServer.scala",
                "KafkaConfig.scala",
            ],
            new_concerns=new_concerns,
            open_questions=(
                [
                    "Partial-result semantics for a fan-out where one or more coordinator "
                    "shards error must be specified in the KIP, not left to the "
                    "implementation."
                ] if round_num == 1 else []
            ),
            follow_up_consultations=[],
            test_requirements=[
                "Unit: handleListGroupsRequest fans out to every coordinator shard when the "
                "topic_partitions filter is present, and unions the per-shard results.",
                "Unit: a shard whose index is not yet built falls back to its own scan and "
                "the unioned result is still complete.",
                "Integration: a filtered ListGroups against a 50-shard cluster returns the "
                f"same group set as the unfiltered scan, with p99 under "
                f"{self.TARGET_FANOUT_P99_MS}ms.",
                "Integration: when one coordinator shard is unavailable the response marks "
                "the result partial rather than returning a silently short list.",
                "Load: broker heap growth from the index stays within the configured cap, "
                "and G1 pause p99 does not regress.",
                "Metrics: per-shard fan-out latency is exported so the slow-shard tail is "
                "observable.",
            ],
        )
