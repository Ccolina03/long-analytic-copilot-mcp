"""
OSS Kafka SME Agent.

Domain: Apache Kafka protocol, KIPs, API versioning, open-source contribution.
Runbook: runbooks/oss-kafka-sme.md

Tools
-----
  search_kips(query)
  get_api_spec(api_name)
  check_compat(api_key, proposed_version, proposed_fields)
  get_kip_template(api_name, change_type)

This agent acts as the upstream gate for protocol changes.  It never
owns a ticket directly in the consumer-groups-per-topic example — it is
always consulted (by consumer-team, or occasionally directly by kora-global).
"""

from __future__ import annotations

from typing import Any

from agents.base_agent import SMEAgentBase, tool
from proto.sme_agents import ImpactRequest, ImpactResponse, Ticket


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

        if "listgroups" in q or "list_groups" in q or "topic" in q or "partition" in q:
            results.append({
                "kip": "KIP-518",
                "title": "ListGroups API to filter by State",
                "status": "DONE",
                "shipped_version": "2.6.0",
                "relevance": (
                    "Adds state/type filter to ListGroups v4. "
                    "Does NOT add topic-partition filter. "
                    "Direct predecessor — reference as prior art."
                ),
            })
            results.append({
                "kip": "KIP-848",
                "title": "The Next Generation of the Consumer Rebalance Protocol",
                "status": "IN_PROGRESS",
                "shipped_version": None,
                "relevance": (
                    "New group protocol — proposed change must be compatible "
                    "with KIP-848 group model."
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
                "versions": [
                    {"version": 0, "fields": []},
                    {"version": 1, "fields": ["throttle_time_ms"]},
                    {"version": 2, "fields": ["throttle_time_ms"]},
                    {
                        "version": 3,
                        "fields": ["throttle_time_ms"],
                        "notes": "flexible version",
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
        checks = []

        # Rule 1: new fields must be optional in flexible versions
        checks.append({
            "rule": "new fields must be optional (tagged fields in flexible versions)",
            "status": "PASS",
            "note": (
                "ListGroups v3+ uses flexible encoding — tagged fields are safe"
                if api_key == 16 else
                "verify this API uses flexible version encoding"
            ),
        })

        # Rule 2: old clients with lower version must get unchanged behavior
        checks.append({
            "rule": "old clients sending v4 must get v4 behavior",
            "status": "PASS",
            "note": "empty topic_partitions = no filter = return all groups",
        })

        # Rule 3: old brokers receiving new version must return UNSUPPORTED_VERSION
        checks.append({
            "rule": "old brokers must return UNSUPPORTED_VERSION for newer versions",
            "status": "PASS",
            "note": "standard version negotiation handles this",
        })

        # Rule 4: KIP-848 compatibility
        checks.append({
            "rule": "KIP-848 new protocol compatibility",
            "status": "REVIEW_NEEDED",
            "note": (
                "KIP-848 new consumer protocol uses ConsumerGroupDescribeRequest (API 69) "
                "not ListGroups. Must specify behavior for mixed classic/consumer groups."
            ),
        })

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
                "Must specify behavior when topic_partitions filter is used "
                "against KIP-848 style groups"
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
takes 8–12 seconds. The bottleneck is the absence of a reverse index on
GroupCoordinator and the lack of a topic-partition filter in the API.

## Proposed Changes

### Protocol ({change_type})
ListGroupsRequest v5 adds an optional tagged field:
  `topic_partitions: ARRAY(STRUCT(topic STRING, partition INT32))`

### Server-side (GroupCoordinator)
- Add in-memory `topicPartitionToGroups: Map<TopicPartition, Set<GroupId>>`
- Maintain index on JoinGroup / LeaveGroup / heartbeat timeout
- `handleListGroups()`: if topic_partitions filter present → use index

### Client-side (AdminClient)
- Add `ListGroupsOptions.withTopicPartitions(List<TopicPartition>)`

## Compatibility
- Old clients (v4) → no filter → full list (unchanged behavior)
- New clients (v5) on old brokers → UNSUPPORTED_VERSION → degrade to v4
- KIP-848 groups: behavior TBD (see open questions)

## Rejected Alternatives
1. New API key: higher cost, no clear benefit over versioned extension
2. Scan with early exit: still O(n) worst case, doesn't solve root cause

## Open Questions
- Behavior for KIP-848 style (new protocol) consumer groups
"""

    # ------------------------------------------------------------------
    # Consultation handler
    # ------------------------------------------------------------------

    def _handle_consultation(
        self, request: ImpactRequest, depth: int
    ) -> ImpactResponse:
        """Respond to a protocol-review request from consumer-team."""
        kips = self.search_kips("ListGroups topic partition filter")
        api_spec = self.get_api_spec("LISTGROUPS")
        compat = self.check_compat(
            api_key=16,
            proposed_version=5,
            proposed_fields=["topic_partitions"],
        )
        template = self.get_kip_template("ListGroups", "new_field")

        kip_518_found = any(k.get("kip") == "KIP-518" for k in kips)
        kip_848_concern = any(
            c["status"] == "REVIEW_NEEDED" for c in compat["checks"]
        )

        open_qs = []
        if kip_848_concern:
            open_qs.append(
                "KIP-848 compatibility: must specify behavior for new-protocol "
                "consumer groups when topic_partitions filter is used"
            )

        return ImpactResponse(
            request_id=request.request_id,
            from_agent=self.AGENT_NAME,
            to_agent=request.from_agent,
            verdict="needs_changes",
            confidence=0.95,
            summary=(
                f"New KIP required (no existing KIP covers topic-partition scoped ListGroups). "
                f"Closest precedent: KIP-518 (reference as prior art). "
                f"Proposed: ListGroupsRequest v5 with optional topic_partitions tagged field. "
                f"Compat check: {compat['overall']}. "
                f"Timeline: ~6 weeks to trunk. "
                f"Fast-path: Kora can ship server-side index immediately; "
                f"v5 API filter must wait for KIP."
            ),
            cited_codepaths=[
                "ListGroupsRequest.json",
                "ApiKeys.java",
            ],
            open_questions=open_qs,
            follow_up_consultations=[],
        )
