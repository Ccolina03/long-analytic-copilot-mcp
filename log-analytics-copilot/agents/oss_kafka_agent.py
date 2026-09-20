"""
OSS Kafka SME Agent — Principal Engineer, Apache Kafka protocol.

Domain: Apache Kafka protocol, KIPs, API versioning, open-source contribution.
Runbook: runbooks/oss-kafka-sme.md

Tools
-----
  search_kips(query)
  get_api_spec(api_name)
  check_compat(api_key, proposed_version, proposed_fields)
  get_kip_template(api_name, change_type)

Behaviour
---------
Acts as the upstream gate for protocol changes.  Proposes three alternatives
for the public API surface and argues for the versioned tagged field.  This is
the one agent that legitimately raises ``needs_org_authority``: an Apache PMC
vote is an external process no agent and no single company controls.  Crucially
it also finds the unblocking path — the server-side index needs no KIP, so the
customer-facing fix is not gated on the vote.
"""

from __future__ import annotations

from typing import Any

from agents.base_agent import SMEAgentBase, tool
from proto.sme_agents import DesignAlternative, ImpactRequest, ImpactResponse, Ticket


class OssKafkaAgent(SMEAgentBase):
    AGENT_NAME = "oss-kafka"
    DOMAIN = "Apache Kafka protocol, KIPs, API versioning, open-source contribution"
    OWNS = [
        "apache/kafka/clients/src/main/resources/common/message/",
        "ListGroupsRequest.json",
        "ListGroupsResponse.json",
        "ApiVersionsResponse.json",
        "ApiKeys.java",
        "apache/kafka/core/src/main/scala/kafka/server/KafkaApis.scala",
    ]

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
                "title": "ListGroups API to filter by State",
                "status": "DONE",
                "shipped_version": "2.6.0",
                "relevance": (
                    "Adds states_filter and types_filter to ListGroups v4. Does NOT add a "
                    "topic-partition filter. This is the direct predecessor and the "
                    "precedent that a filter field on ListGroups is an acceptable shape."
                ),
            })
            results.append({
                "kip": "KIP-848",
                "title": "The Next Generation of the Consumer Rebalance Protocol",
                "status": "IN_PROGRESS",
                "shipped_version": None,
                "relevance": (
                    "New consumer group protocol. New-protocol groups are described via "
                    "ConsumerGroupDescribeRequest (API 69), not ListGroups, so the "
                    "proposed filter must define its behavior for mixed clusters."
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
                "current_max_version": 4,
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
                        "fields": ["throttle_time_ms", "states_filter", "types_filter"],
                        "kip": "KIP-518",
                    },
                ],
                "proposed_v5_field": (
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
                "rule": "old clients sending v4 must get unchanged v4 behavior",
                "status": "PASS",
                "note": "empty topic_partitions = no filter = return all groups",
            },
            {
                "rule": "old brokers must return UNSUPPORTED_VERSION for newer versions",
                "status": "PASS",
                "note": "standard ApiVersions negotiation handles this",
            },
            {
                "rule": "KIP-848 new protocol compatibility",
                "status": "REVIEW_NEEDED",
                "note": (
                    "KIP-848 new consumer protocol uses ConsumerGroupDescribeRequest "
                    "(API 69) not ListGroups. Must specify behavior for mixed "
                    "classic/consumer groups before the KIP can pass review."
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
            "overall": overall,
            "kip_848_note": (
                "Must specify behavior when topic_partitions filter is used against "
                "KIP-848 style groups"
            ),
        }

    @tool("get_kip_template")
    def get_kip_template(self, api_name: str, change_type: str) -> str:
        """Return a pre-filled KIP draft template."""
        return f"""# KIP-XXX: {api_name} filter by topic-partition

**Author:** [Consumer Team]
**Status:** Under Discussion
**Discussion thread:** [link to kafka-dev@apache.org thread]

## Motivation
Cluster Linking `clampOffsets()` currently calls `ListGroups()` and scans all
50k groups to find the 4 groups subscribed to a given topic-partition. This
takes 8-12 seconds. The bottleneck is the absence of a reverse index on
GroupCoordinator and the lack of a topic-partition filter in the API.
The same problem affects MirrorMaker 2 and any self-managed DR deployment.

## Proposed Changes

### Protocol ({change_type})
ListGroupsRequest v5 adds an optional tagged field:
  `topic_partitions: ARRAY(STRUCT(topic STRING, partition INT32))`

### Server-side (GroupCoordinator)
- Add in-memory `topicPartitionToGroups: Map<TopicPartition, Set<GroupId>>`
- Maintain index on JoinGroup / LeaveGroup / heartbeat timeout
- `handleListGroups()`: if topic_partitions filter present, use the index

### Client-side (AdminClient)
- Add `ListGroupsOptions.withTopicPartitions(List<TopicPartition>)`

## Compatibility
- Old clients (v4) send no filter, get the full list — unchanged behavior
- New clients (v5) against old brokers get UNSUPPORTED_VERSION, degrade to v4
- KIP-848 groups: behavior must be specified (see Open Questions)

## Rejected Alternatives
1. New API key: higher cost, no clear benefit over a versioned extension
2. Scan with early exit: still O(n) worst case, does not solve the root cause

## Open Questions
- Behavior for KIP-848 style (new protocol) consumer groups
"""

    # ------------------------------------------------------------------
    # Protocol-surface alternatives
    # ------------------------------------------------------------------

    def _protocol_alternatives(self, api_spec: dict, compat: dict) -> list[DesignAlternative]:
        return [
            DesignAlternative(
                label="A",
                name="ListGroupsRequest v5 with an optional tagged topic_partitions field",
                proposed_by=self.AGENT_NAME,
                approach=(
                    "Bump LIST_GROUPS (API key 16) max version from "
                    f"{api_spec['current_max_version']} to 5 in ApiKeys.java and add an "
                    "optional tagged field `topic_partitions: ARRAY(STRUCT(topic STRING, "
                    "partition INT32))` to ListGroupsRequest.json. Because ListGroups has "
                    f"been a flexible version since v{api_spec['flexible_since_version']}, "
                    "tagged fields are wire-safe by construction. Empty or absent field "
                    "means no filter, which makes v5 behaviourally identical to v4 for any "
                    "client that does not opt in. AdminClient gets a "
                    "`ListGroupsOptions.withTopicPartitions(...)` overload."
                ),
                pros=[
                    "Follows the precedent set by KIP-518, which added exactly this shape of "
                    "filter to this exact API — the community has already accepted the "
                    "pattern, which materially de-risks the vote.",
                    "Backward and forward compatible by construction: flexible-version "
                    "tagged fields are the mechanism Kafka designed for this.",
                    "Smallest possible protocol surface — one optional field, no new API "
                    "key, no new response shape.",
                    "Open-source users get the fix, so MirrorMaker 2 and self-managed DR "
                    "benefit and we avoid a protocol fork.",
                    "The server-side index needs no protocol change at all, so Confluent "
                    "can ship the performance fix internally while the KIP is in flight.",
                ],
                cons=[
                    "Requires a KIP: roughly 2 weeks discussion, 1 week vote, 2 weeks "
                    "implementation review, 1 week to merge — about 6 weeks to trunk, and "
                    "the vote is not under our control.",
                    "Needs 3 binding +1 votes from PMC members; any -1 with a reason blocks "
                    "until addressed.",
                    f"KIP-848 interaction must be specified before review will pass: "
                    f"{compat['kip_848_note']}.",
                    "Overloading ListGroups further means its semantics keep accreting "
                    "filters, which some reviewers will object to on design-cleanliness "
                    "grounds.",
                ],
                effort="L",
                risk="medium",
                blast_radius=["oss-kafka", "consumer-team", "Apache Kafka community"],
            ),
            DesignAlternative(
                label="B",
                name="New dedicated API key: ListGroupsForTopicPartition",
                proposed_by=self.AGENT_NAME,
                approach=(
                    "Introduce a brand-new API key with a purpose-built request/response "
                    "shape that takes a list of topic-partitions and returns group ids. "
                    "Designed from the start to work for both classic and KIP-848 "
                    "new-protocol groups, so the semantics are unambiguous rather than "
                    "retrofitted onto an API that predates the new protocol."
                ),
                pros=[
                    "Clean semantics — no version negotiation subtleties, no behavioural "
                    "overloading of an existing API.",
                    "Can be specified correctly for KIP-848 groups from day one instead of "
                    "carrying a compatibility caveat.",
                    "Easier to evolve independently of ListGroups' existing filter fields.",
                ],
                cons=[
                    "A new API key is a substantially bigger KIP ask than a version bump, "
                    "and the community's consistent instinct is to ask 'why not extend the "
                    "existing API?' — expect that pushback and a longer discussion phase.",
                    "More permanent surface area to maintain, document, and support across "
                    "every client library.",
                    "Clients need new capability detection logic rather than reusing "
                    "standard version negotiation.",
                    "Strictly slower to land than alternative A while solving the same "
                    "problem, which is hard to justify to reviewers.",
                ],
                effort="XL",
                risk="high",
                blast_radius=[
                    "oss-kafka", "consumer-team", "Apache Kafka community", "all client libraries"
                ],
            ),
            DesignAlternative(
                label="C",
                name="Confluent-internal extension only, no upstream change",
                proposed_by=self.AGENT_NAME,
                approach=(
                    "Skip Apache entirely. Expose the filtered lookup through a "
                    "Confluent-only internal call between Cluster Linking and the Kora "
                    "GroupCoordinator, gated behind a Confluent extension flag. No KIP, no "
                    "mailing list, no vote."
                ),
                pros=[
                    "Ships immediately with zero external dependency and zero community "
                    "coordination cost.",
                    "Full control over semantics and timeline.",
                ],
                cons=[
                    "Creates a protocol fork between Confluent and Apache Kafka, which is "
                    "precisely the outcome this team exists to prevent.",
                    "Open-source users with the identical problem — anyone running "
                    "MirrorMaker 2 or self-managed DR — get nothing.",
                    "When Apache eventually adds its own topic-partition filter (and it "
                    "will, because the need is general), we inherit a migration and a "
                    "period of maintaining two divergent paths.",
                    "Contradicts Confluent's stated position on upstream-first protocol "
                    "work, which has reputational cost in the community we depend on.",
                ],
                effort="S",
                risk="high",
                blast_radius=["kora-global", "consumer-team"],
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
        compat = self.check_compat(16, 5, ["topic_partitions"])
        alternatives = self._protocol_alternatives(api_spec, compat)

        alternatives[0].recommended = True
        alternatives[1].rejected_reason = (
            "A new API key is a larger ask for the same outcome; the community will "
            "reasonably push back and it lands strictly later than the v5 field."
        )
        alternatives[2].rejected_reason = (
            "Creates a protocol fork and leaves open-source users with the same bug. "
            "Acceptable only as a temporary internal fast path, not as the end state."
        )

        kip_518 = next((k for k in kips if k.get("kip") == "KIP-518"), None)
        round_num = request.round_number

        new_concerns: list[str] = []
        if round_num == 1:
            new_concerns = [
                f"{compat['kip_848_note']}. This must be written into the KIP before it goes "
                f"to a vote — reviewers will catch it otherwise and it costs us a cycle.",
                "The Apache vote itself is outside Confluent's control: 3 binding +1 votes "
                "are required and any reasoned -1 blocks. Do not build a customer "
                "commitment around the KIP date.",
            ]
            verdict = "needs_changes"
        else:
            verdict = "agreed"

        summary = (
            f"New KIP required — no existing KIP covers topic-partition scoped ListGroups. "
            f"Closest precedent is {kip_518['kip'] if kip_518 else 'KIP-518'} "
            f"({kip_518['title'] if kip_518 else 'ListGroups state/type filter'}), which "
            f"added the states_filter/types_filter to v4 and establishes that a filter "
            f"field on this API is an accepted shape — cite it as prior art. "
            f"Recommended surface: ListGroupsRequest v5 with an optional tagged "
            f"topic_partitions field. Compatibility check: {compat['overall']}. "
            f"Timeline ~6 weeks to trunk. "
            f"Critically: the broker-side index needs no protocol change, so Kora can ship "
            f"the performance fix behind a Confluent flag immediately and migrate to the "
            f"public v5 API when the KIP merges — the customer fix is not gated on the vote."
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
                "the API surface, because they have completely different gating. The reverse "
                "index inside GroupCoordinator is an internal implementation detail — no "
                "wire format changes, so no KIP, so no external dependency. The only part "
                "that touches the protocol is letting a client *ask* for a filtered list, "
                "and that is one optional tagged field on an API that has been flexible "
                "since v3. Splitting it that way means the customer-visible latency problem "
                "gets fixed on Confluent's own schedule while the public API follows the "
                "Apache process at its own pace. I want to be direct about the one thing we "
                "genuinely cannot decide ourselves: the KIP vote needs 3 binding +1s from "
                "PMC members, and no amount of engineering agreement substitutes for that. "
                "That is the single item on this ticket that needs a human with "
                "organizational standing, and it is specifically about securing a sponsoring "
                "committer, not about whether the design is right."
            ),
            design_alternatives=alternatives,
            recommendation=(
                "Alternative A: ListGroupsRequest v5 with an optional tagged "
                "topic_partitions field. It follows KIP-518's accepted precedent, is "
                "wire-safe because ListGroups is flexible since v3, and lets the "
                "server-side index ship ahead of the vote."
            ),
            cited_codepaths=[
                "ListGroupsRequest.json",
                "ApiKeys.java",
                "ListGroupsResponse.json",
            ],
            new_concerns=new_concerns,
            open_questions=(
                [
                    "KIP-848 interaction: specify whether topic-partition filtering applies "
                    "to new-protocol consumer groups, or document it as unsupported for "
                    "them in the first version."
                ] if round_num == 1 else []
            ),
            follow_up_consultations=[],
            test_requirements=[
                "Protocol: ListGroupsRequest v5 round-trips through the generated "
                "serde with the tagged field both present and absent.",
                "Compatibility: a v4 client against a v5-capable broker receives "
                "byte-identical responses to the pre-change behavior.",
                "Compatibility: a v5 client against a v4-only broker receives "
                "UNSUPPORTED_VERSION and degrades to v4 without error.",
                "Interop: mixed cluster with both classic and KIP-848 groups returns the "
                "documented behavior for the filter.",
                "Generated-code: ApiKeys.java max version for LIST_GROUPS reports 5 and "
                "ApiVersionsResponse advertises it correctly.",
            ],
            # The one legitimate org-authority gate on this ticket.
            needs_org_authority=True,
            org_authority_reason=(
                "The ListGroups v5 KIP requires a sponsoring Apache Kafka PMC committer and "
                "3 binding +1 votes on kafka-dev@apache.org. Securing a sponsor and "
                "representing Confluent in that vote is an external organizational process "
                "that cannot be delegated to an agent. Note this gates the *public API* "
                "only — the broker-side index and Kora's internal fast path can ship "
                "without it."
            ),
        )
