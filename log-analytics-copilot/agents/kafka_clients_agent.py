"""
Kafka Clients SME Agent — Principal Engineer, wire protocol and AdminClient.

Domain: Apache Kafka wire protocol, RPC schemas, API versioning, AdminClient
public surface, and the KIP process.
Runbook: runbooks/kafka-clients-sme.md

Tools
-----
  search_kips(query)
  get_api_spec(api_name)
  check_compat(api_key, proposed_version, proposed_fields)
  get_kip_template(api_name, change_type)

Behaviour
---------
Acts as the gate for protocol changes. Proposes three alternatives for the
public API surface and argues for the versioned tagged field. This is the one
agent that legitimately raises ``needs_org_authority``: an Apache PMC vote is
an external process no contributor controls. Crucially it also finds the
unblocking path — the server-side index needs no KIP, so the operator-facing
fix is not gated on the vote.
"""

from __future__ import annotations

from agents.base_agent import SMEAgentBase, tool
from proto.sme_agents import DesignAlternative, ImpactRequest, ImpactResponse


class KafkaClientsAgent(SMEAgentBase):
    AGENT_NAME = "kafka-clients"
    DOMAIN = (
        "Apache Kafka wire protocol, RPC schemas, API versioning, AdminClient "
        "public surface, KIP process"
    )
    OWNS = [
        "clients/src/main/resources/common/message/",
        "ListGroupsRequest.json",
        "ListGroupsResponse.json",
        "ApiVersionsResponse.json",
        "ApiKeys.java",
        "clients/src/main/java/org/apache/kafka/clients/admin/",
        "KafkaAdminClient.java",
        "ListConsumerGroupsOptions.java",
    ]

    # Wire-protocol compatibility reasoning is the highest-stakes judgment in
    # this network: get it wrong and you ship a breaking change to every Kafka
    # client. Worth a strong model on every call, not just design review.
    LLM_TIER = "deep"

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    @tool("search_kips")
    def search_kips(self, query: str) -> list:
        """Search KIP wiki and mailing list archives for proposals matching ``query``."""
        if not query:
            raise ValueError("query is required")
        q = query.lower()
        results = []

        if any(k in q for k in ("listgroups", "list_groups", "topic", "partition")):
            results.append({
                "kip": "KIP-518",
                "title": "Allow listing consumer groups per state",
                "status": "DONE",
                "shipped_version": "2.6.0",
                "relevance": (
                    "Added states_filter to ListGroups v4. Does NOT add a topic-partition "
                    "filter. This is the direct precedent that a filter field on "
                    "ListGroups is an acceptable shape."
                ),
            })
            results.append({
                "kip": "KIP-848",
                "title": "The Next Generation of the Consumer Rebalance Protocol",
                "status": "DONE",
                "shipped_version": "4.0.0",
                "relevance": (
                    "Added types_filter to ListGroups v5 and introduced the 'consumer' "
                    "group type. New-protocol groups are described via "
                    "ConsumerGroupDescribeRequest (API 69), not DescribeGroups, so the "
                    "proposed filter must define its behaviour for both group types."
                ),
            })
            results.append({
                "kip": "KIP-382",
                "title": "MirrorMaker 2.0",
                "status": "DONE",
                "shipped_version": "2.4.0",
                "relevance": (
                    "Established MM2's checkpoint and offset-translation design, which is "
                    "the caller that needs this filter. Useful motivation material."
                ),
            })

        if not results:
            results.append({
                "kip": None,
                "title": "No existing KIP found",
                "status": "NEW_REQUIRED",
                "shipped_version": None,
                "relevance": f"No existing KIP covers: {query}",
            })

        return results

    @tool("get_api_spec")
    def get_api_spec(self, api_name: str) -> dict:
        """Return the current protocol schema for a Kafka API."""
        if not api_name:
            raise ValueError("api_name is required")
        if api_name.upper() == "LISTGROUPS":
            return {
                "api": "ListGroups",
                "api_key": 16,
                "current_max_version": 5,
                "flexible_since_version": 3,
                "versions": [
                    {"version": 0, "fields": []},
                    {"version": 1, "fields": ["throttle_time_ms"]},
                    {"version": 2, "fields": ["throttle_time_ms"]},
                    {
                        "version": 3,
                        "fields": ["throttle_time_ms"],
                        "notes": "flexible version — tagged fields become safe here",
                    },
                    {
                        "version": 4,
                        "fields": ["throttle_time_ms", "states_filter"],
                        "kip": "KIP-518",
                    },
                    {
                        "version": 5,
                        "fields": ["throttle_time_ms", "states_filter", "types_filter"],
                        "kip": "KIP-848",
                    },
                ],
                "proposed_v6_field": (
                    "topic_partitions: [{topic: string, partition: int32}]"
                ),
            }
        return {
            "api": api_name,
            "api_key": -1,
            "current_max_version": 0,
            "versions": [],
            "note": f"spec not found for {api_name}",
        }

    @tool("check_compat")
    def check_compat(
        self, api_key: int, proposed_version: int, proposed_fields: list
    ) -> dict:
        """Validate a proposed protocol change against Kafka compatibility rules."""
        checks = [
            {
                "rule": "new fields must be optional (tagged fields in flexible versions)",
                "status": "PASS",
                "note": (
                    "ListGroups has been flexible since v3, so tagged fields are safe"
                    if api_key == 16 else
                    "verify this API uses flexible version encoding"
                ),
            },
            {
                "rule": "old clients sending v5 must get unchanged v5 behavior",
                "status": "PASS",
                "note": "empty topic_partitions = no filter = return all groups",
            },
            {
                "rule": "old brokers must return UNSUPPORTED_VERSION for newer versions",
                "status": "PASS",
                "note": "standard ApiVersions negotiation handles this",
            },
            {
                "rule": "KIP-848 group type compatibility",
                "status": "REVIEW_NEEDED",
                "note": (
                    "KIP-848 consumer groups are described via "
                    "ConsumerGroupDescribeRequest (API 69), not DescribeGroups. Must "
                    "specify filter behaviour for mixed classic/consumer group types "
                    "before the KIP can pass review."
                ),
            },
        ]

        overall = (
            "PASS_WITH_NOTE"
            if any(c["status"] == "REVIEW_NEEDED" for c in checks)
            else "PASS"
        )

        return {
            "api_key": api_key,
            "proposed_version": proposed_version,
            "proposed_fields": proposed_fields,
            "checks": checks,
            "kip_848_note": (
                "Must specify behavior when the topic_partitions filter is used against "
                "KIP-848 consumer groups as well as classic groups"
            ),
            "overall": overall,
        }

    @tool("get_kip_template")
    def get_kip_template(self, api_name: str, change_type: str) -> str:
        """Return a pre-filled KIP draft template."""
        return f"""# KIP-XXX: {api_name} filter by topic-partition

**Author:** [group-coordinator team]
**Status:** Under Discussion
**Discussion thread:** [link to dev@kafka.apache.org thread]

## Motivation
MirrorMaker 2's `MirrorCheckpointConnector.findConsumerGroups()` currently calls
`Admin.listConsumerGroups()` and then `describeConsumerGroups()` across all 50k
groups to find the 4 groups consuming a given topic. This takes 8-12 seconds and
runs on every checkpoint interval. The bottleneck is the absence of a reverse
index in the group coordinator and the lack of a topic-partition filter in the
API. Any operator tool asking "which groups consume this topic" has the same
problem, as does any self-managed DR deployment.

## Proposed Changes

### Protocol ({change_type})
ListGroupsRequest v6 adds an optional tagged field:
  `topic_partitions: ARRAY(STRUCT(topic STRING, partition INT32))`

### Server-side (group coordinator)
- Add in-memory `topicPartitionToGroups: Map<TopicPartition, Set<String>>`
- Maintain the index on subscription change, leave, and session timeout
- Serve filtered ListGroups from the index when the field is present
- Scatter-gather across all `__consumer_offsets` coordinator shards

### Client-side (AdminClient)
- Add `ListConsumerGroupsOptions.inTopicPartitions(Collection<TopicPartition>)`

## Compatibility
- Old clients (v5) send no filter, get the full list — unchanged behavior
- New clients (v6) against old brokers get UNSUPPORTED_VERSION, degrade to v5
- Applies to both classic and KIP-848 consumer groups (see Open Questions)

## Rejected Alternatives
1. New API key: higher cost, no clear benefit over a versioned extension
2. Client-side aggregation only: does not fix the O(n_groups) cost
3. Scan with early exit: still O(n) worst case, does not solve the root cause

## Open Questions
- Exact semantics for KIP-848 consumer groups versus classic groups
"""

    # ------------------------------------------------------------------
    # Protocol-surface alternatives
    # ------------------------------------------------------------------

    def _protocol_alternatives(
        self, api_spec: dict, compat: dict
    ) -> list[DesignAlternative]:
        return [
            DesignAlternative(
                label="A",
                name="ListGroupsRequest v6 with an optional tagged topic_partitions field",
                proposed_by=self.AGENT_NAME,
                approach=(
                    "Bump LIST_GROUPS (API key 16) max version from "
                    f"{api_spec['current_max_version']} to 6 in ApiKeys.java and add an "
                    "optional tagged field `topic_partitions: ARRAY(STRUCT(topic STRING, "
                    "partition INT32))` to ListGroupsRequest.json. Because ListGroups has "
                    f"been a flexible version since v{api_spec['flexible_since_version']}, "
                    "tagged fields are wire-safe by construction. An empty or absent field "
                    "means no filter, which makes v6 behaviourally identical to v5 for any "
                    "client that does not opt in. AdminClient gains "
                    "`ListConsumerGroupsOptions.inTopicPartitions(...)`."
                ),
                pros=[
                    "Follows the precedent set by KIP-518 and KIP-848, which added exactly "
                    "this shape of filter to this exact API twice already — the community "
                    "has accepted the pattern, which materially de-risks the vote.",
                    "Backward and forward compatible by construction: flexible-version "
                    "tagged fields are the mechanism Kafka designed for this.",
                    "Smallest possible protocol surface — one optional field, no new API "
                    "key, no new response shape.",
                    "Benefits every Kafka user: MM2, self-managed DR, and operator tooling "
                    "all have this problem today.",
                    "The server-side index needs no protocol change at all, so the "
                    "performance fix can land and be validated while the KIP is in flight.",
                ],
                cons=[
                    "Requires a KIP: roughly 2 weeks discussion, 1 week vote, 2 weeks "
                    "implementation review, 1 week to merge — about 6 weeks to trunk, and "
                    "the vote is not under our control.",
                    "Needs 3 binding +1 votes from PMC members; any reasoned -1 blocks "
                    "until addressed.",
                    f"KIP-848 interaction must be specified before review will pass: "
                    f"{compat['kip_848_note']}.",
                    "Overloading ListGroups further means its semantics keep accreting "
                    "filters, which some reviewers will object to on design-cleanliness "
                    "grounds.",
                ],
                effort="L",
                risk="medium",
                blast_radius=[
                    "kafka-clients", "group-coordinator", "kafka-broker",
                    "Apache Kafka community",
                ],
            ),
            DesignAlternative(
                label="B",
                name="New dedicated API key: ListGroupsForTopicPartition",
                proposed_by=self.AGENT_NAME,
                approach=(
                    "Introduce a brand-new API key with a purpose-built request/response "
                    "shape that takes a list of topic-partitions and returns group ids. "
                    "Designed from the start to cover both classic and KIP-848 consumer "
                    "groups, so the semantics are unambiguous rather than retrofitted onto "
                    "an API that predates the new protocol."
                ),
                pros=[
                    "Clean semantics — no version negotiation subtleties, no behavioural "
                    "overloading of an existing API.",
                    "Can be specified correctly for both group types from day one instead "
                    "of carrying a compatibility caveat.",
                    "Easier to evolve independently of ListGroups' existing filter fields.",
                ],
                cons=[
                    "A new API key is a substantially bigger KIP ask than a version bump, "
                    "and the community's consistent instinct is to ask 'why not extend the "
                    "existing API?' — expect that pushback and a longer discussion phase.",
                    "More permanent surface area to maintain, document, and support across "
                    "every client library in every language.",
                    "Clients need new capability-detection logic rather than reusing "
                    "standard version negotiation.",
                    "Strictly slower to land than alternative A while solving the same "
                    "problem, which is hard to justify to reviewers.",
                ],
                effort="XL",
                risk="high",
                blast_radius=[
                    "kafka-clients", "group-coordinator", "Apache Kafka community",
                    "all client libraries",
                ],
            ),
            DesignAlternative(
                label="C",
                name="AdminClient convenience method only, no wire change",
                proposed_by=self.AGENT_NAME,
                approach=(
                    "Add a public `Admin.listConsumerGroupsForTopic(...)` helper that does "
                    "the list-then-describe aggregation on the client side, so callers stop "
                    "hand-rolling it. No protocol change, no broker change — this is a "
                    "usability wrapper around the existing APIs. Still needs a small KIP "
                    "because AdminClient is a public interface, but no wire format is "
                    "touched and no vote on protocol semantics is required."
                ),
                pros=[
                    "Smallest possible KIP — a Java API addition with no wire format "
                    "implications, which typically passes review quickly.",
                    "Removes the duplicated list-then-describe logic that MM2 and several "
                    "operator tools have each reimplemented slightly differently.",
                    "Zero compatibility risk of any kind.",
                ],
                cons=[
                    "Does not fix the actual problem. The cost is still O(n_groups) because "
                    "the work simply moves behind a nicer method signature — MM2's p99 "
                    "would be unchanged.",
                    "Arguably worse than doing nothing: it blesses the inefficient pattern "
                    "as the official API, making it harder to argue later that the server "
                    "should do this properly.",
                    "Still consumes a KIP cycle without delivering the latency win, so it "
                    "costs most of the process overhead for none of the benefit.",
                ],
                effort="S",
                risk="medium",
                blast_radius=["kafka-clients", "mirrormaker"],
            ),
        ]

    # ------------------------------------------------------------------
    # Consultation handler
    # ------------------------------------------------------------------

    def _handle_consultation(
        self, request: ImpactRequest, depth: int
    ) -> ImpactResponse:
        """Respond to a protocol-review request."""
        kips = self.search_kips("ListGroups topic partition filter")
        api_spec = self.get_api_spec("LISTGROUPS")
        compat = self.check_compat(16, 6, ["topic_partitions"])
        alternatives = self._protocol_alternatives(api_spec, compat)

        alternatives[0].recommended = True
        alternatives[1].rejected_reason = (
            "A new API key is a larger ask for the same outcome; the community will "
            "reasonably push back and it lands strictly later than the v6 field."
        )
        alternatives[2].rejected_reason = (
            "Does not fix the O(n_groups) cost — it only renames it. Blessing the "
            "inefficient pattern as public API makes the real fix harder to argue later."
        )

        kip_518 = next((k for k in kips if k.get("kip") == "KIP-518"), None)
        round_num = request.round_number

        new_concerns: list[str] = []
        if round_num == 1:
            new_concerns = [
                f"{compat['kip_848_note']}. This must be written into the KIP before it "
                f"goes to a vote — reviewers will catch it otherwise and it costs a cycle.",
                "The Apache vote itself is outside any contributor's control: 3 binding +1 "
                "votes are required and any reasoned -1 blocks. Do not build an operator "
                "commitment around the KIP date.",
            ]
            verdict = "needs_changes"
        else:
            verdict = "agreed"

        summary = (
            f"New KIP required — no existing KIP covers topic-partition scoped ListGroups. "
            f"Closest precedent is {kip_518['kip'] if kip_518 else 'KIP-518'} "
            f"({kip_518['title'] if kip_518 else 'listing consumer groups per state'}), "
            f"which added states_filter in v4; KIP-848 then added types_filter in v5. Two "
            f"accepted filter fields on this exact API is strong prior art — cite both. "
            f"Recommended surface: ListGroupsRequest v6 with an optional tagged "
            f"topic_partitions field. Compatibility check: {compat['overall']}. "
            f"Timeline ~6 weeks to trunk. "
            f"Critically: the broker-side index needs no protocol change, so the "
            f"coordinator work can land behind a broker config immediately and MM2 can "
            f"migrate to the public v6 API when the KIP merges — the performance fix is "
            f"not gated on the vote."
        )

        return ImpactResponse(
            request_id=request.request_id,
            from_agent=self.AGENT_NAME,
            to_agent=request.from_agent,
            verdict=verdict,
            confidence=0.95,
            summary=summary,
            principal_review=(
                "The right way to think about this is to separate the performance fix from "
                "the API surface, because they have completely different gating. The "
                "reverse index inside the group coordinator is an internal implementation "
                "detail — no wire format changes, so no KIP, so no external dependency. "
                "The only part that touches the protocol is letting a client *ask* for a "
                "filtered list, and that is one optional tagged field on an API that has "
                "been flexible since v3 and has already accepted two filter fields by the "
                "same mechanism. Splitting it that way means the operator-visible latency "
                "problem gets fixed on the implementation team's schedule while the public "
                "API follows the Apache process at its own pace. I want to be direct about "
                "the one thing we genuinely cannot decide ourselves: the KIP vote needs 3 "
                "binding +1s from PMC members, and no amount of engineering agreement "
                "substitutes for that. That is the single item on this ticket that needs a "
                "human with organizational standing, and it is specifically about securing "
                "a sponsoring committer, not about whether the design is right."
            ),
            design_alternatives=alternatives,
            recommendation=(
                "Alternative A: ListGroupsRequest v6 with an optional tagged "
                "topic_partitions field. It follows the KIP-518 and KIP-848 precedent, is "
                "wire-safe because ListGroups is flexible since v3, and lets the "
                "server-side index land ahead of the vote."
            ),
            cited_codepaths=[
                "ListGroupsRequest.json",
                "ApiKeys.java",
                "ListGroupsResponse.json",
                "ListConsumerGroupsOptions.java",
            ],
            new_concerns=new_concerns,
            open_questions=(
                [
                    "KIP-848 interaction: specify whether topic-partition filtering applies "
                    "to consumer-protocol groups, classic groups, or both, and document the "
                    "behaviour explicitly in the first version."
                ] if round_num == 1 else []
            ),
            follow_up_consultations=[],
            test_requirements=[
                "Protocol: ListGroupsRequest v6 round-trips through the generated serde "
                "with the tagged field both present and absent.",
                "Compatibility: a v5 client against a v6-capable broker receives "
                "byte-identical responses to the pre-change behavior.",
                "Compatibility: a v6 client against a v5-only broker receives "
                "UNSUPPORTED_VERSION and degrades to v5 without error.",
                "Interop: a cluster with both classic and KIP-848 consumer groups returns "
                "the documented behavior for the filter.",
                "Generated-code: ApiKeys.java max version for LIST_GROUPS reports 6 and "
                "ApiVersionsResponse advertises it correctly.",
                "AdminClient: inTopicPartitions() degrades to the unfiltered path when the "
                "broker does not advertise v6.",
            ],
            # The one legitimate org-authority gate on this ticket.
            needs_org_authority=True,
            org_authority_reason=(
                "The ListGroups v6 KIP requires a sponsoring Apache Kafka PMC committer and "
                "3 binding +1 votes on dev@kafka.apache.org. Securing a sponsor and "
                "shepherding that vote is an external organizational process that cannot be "
                "delegated to an agent. Note this gates the *public API* only — the "
                "broker-side index and MM2's internal fast path can land without it."
            ),
        )
