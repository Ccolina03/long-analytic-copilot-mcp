"""
Kafka Security SME Agent — Principal Engineer, authorization and ACLs.

Domain: Authorization (Authorizer / StandardAuthorizer), ACL semantics, and
information disclosure through the public APIs.
Runbook: runbooks/kafka-security-sme.md

Tools
-----
  get_acl_requirements(api_name)
  analyze_information_disclosure(api_name, new_field)
  get_authorizer_cost(checks_per_request)
  get_multi_tenant_profile()

Behaviour
---------
This agent is the clearest demonstration of why discovery beats a hardcoded
peer list. Nobody investigating a MirrorMaker latency bug sets out to involve
the security team, and none of the other three teams would have thought to
call them: the group coordinator is reasoning about index correctness, the
broker about heap and fan-out, and the clients team about schema evolution.

But a topic-scoped filter on ListGroups is a **new read path over the
relationship between topics and groups**, and today no ACL covers that
relationship. A principal who can call the filtered API learns which groups
consume a topic — and, from an empty versus non-empty response, whether the
topic exists at all — without necessarily holding DESCRIBE on that topic. On a
multi-tenant cluster that is a cross-tenant consumption-topology leak.

MirrorMaker reaches this team by declaring an ``authorization`` impact signal:
"the filter lets a caller infer which groups consume a topic". The directory
resolves that concern to kafka-security. The agent never had to be named.
"""

from __future__ import annotations

from agents.base_agent import SMEAgentBase, tool
from proto.sme_agents import DesignAlternative, ImpactRequest, ImpactResponse


class KafkaSecurityAgent(SMEAgentBase):
    AGENT_NAME = "kafka-security"
    DOMAIN = (
        "Authorization and ACL semantics (Authorizer, StandardAuthorizer), and "
        "information disclosure through the public APIs"
    )
    OWNS = [
        "metadata/src/main/java/org/apache/kafka/metadata/authorizer/",
        "StandardAuthorizer.java",
        "StandardAuthorizerData.java",
        "clients/src/main/java/org/apache/kafka/common/acl/",
        "AclOperation.java",
        "AclBinding.java",
        "ResourcePattern.java",
        "server/src/main/java/org/apache/kafka/server/authorizer/",
        "Authorizer.java",
        "Action.java",
    ]

    # Authorization reasoning is about semantics and threat model rather than
    # volume of tool output, so the routine tier stays cheap and the design
    # review escalates to LLM_DESIGN_TIER like every other agent.
    LLM_TIER = "small"

    # An added authorization check sits on the request hot path. Anything that
    # costs more than this per request needs a different design, not a waiver.
    AUTHZ_BUDGET_US_PER_REQUEST = 200

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    @tool("get_acl_requirements")
    def get_acl_requirements(self, api_name: str) -> dict:
        """Return the ACLs ``api_name`` requires today, and how it degrades."""
        if not api_name:
            raise ValueError("api_name is required")
        if api_name.upper() == "LISTGROUPS":
            return {
                "api": "ListGroups",
                "primary_check": {
                    "operation": "DESCRIBE",
                    "resource_type": "CLUSTER",
                    "effect": "authorizes listing every group on the cluster",
                },
                "fallback_behaviour": (
                    "A principal without DESCRIBE on CLUSTER does not get an error. The "
                    "broker instead filters the result to groups the principal holds "
                    "DESCRIBE on GROUP for, so the API is usable with scoped credentials."
                ),
                "topic_acl_involvement": "none",
                "gap": (
                    "No ACL in the current model governs the topic→group relationship. "
                    "Group ACLs authorize access to a group; topic ACLs authorize access "
                    "to a topic. Nothing authorizes learning which groups consume which "
                    "topic, because until now no API exposed that."
                ),
                "authorizer": "StandardAuthorizer",
            }
        return {
            "api": api_name,
            "primary_check": None,
            "note": f"ACL requirements not modelled for {api_name}",
        }

    @tool("analyze_information_disclosure")
    def analyze_information_disclosure(self, api_name: str, new_field: str) -> dict:
        """Assess what adding ``new_field`` to ``api_name`` lets a caller infer."""
        if not api_name or not new_field:
            raise ValueError("api_name and new_field are both required")
        return {
            "api": api_name,
            "new_field": new_field,
            "severity": "medium",
            "classification": "information_disclosure",
            "leak_vectors": [
                {
                    "vector": "consumption_topology",
                    "detail": (
                        "The filtered response names the groups consuming a topic. Group "
                        "names in practice encode service and team identity "
                        "('billing-reconciler-v3'), so the response maps a topic to the "
                        "services that read it — which is exactly the internal "
                        "architecture diagram an attacker wants."
                    ),
                },
                {
                    "vector": "topic_existence_oracle",
                    "detail": (
                        "If an unauthorized topic in the filter returns an error, the error "
                        "distinguishes 'topic does not exist' from 'not permitted', turning "
                        "the filter into a probe for topic names across tenant boundaries."
                    ),
                },
                {
                    "vector": "idle_topic_inference",
                    "detail": (
                        "An empty result for an existing topic reveals that nothing is "
                        "consuming it, which is useful for timing an attack on an "
                        "unmonitored data path."
                    ),
                },
            ],
            "precedent": (
                "This is the same class of issue KIP-546 handled for describeClientQuotas "
                "and that Metadata handles for topic auto-creation: a filter over a "
                "resource must be authorized against that resource, and unauthorized "
                "entries must be omitted rather than rejected."
            ),
            "exploitable_without_new_acls": True,
        }

    @tool("get_authorizer_cost")
    def get_authorizer_cost(self, checks_per_request: int) -> dict:
        """Cost of ``checks_per_request`` authorization checks on the hot path."""
        if checks_per_request < 0:
            raise ValueError("checks_per_request must be >= 0")
        per_check_us = 1.8
        batched_us = per_check_us * checks_per_request
        return {
            "checks_per_request": checks_per_request,
            "per_check_us": per_check_us,
            "total_us": round(batched_us, 1),
            "budget_us": self.AUTHZ_BUDGET_US_PER_REQUEST,
            "within_budget": batched_us <= self.AUTHZ_BUDGET_US_PER_REQUEST,
            "implementation_note": (
                "StandardAuthorizer.authorize() takes a List<Action> and resolves all of "
                "them against one immutable ACL snapshot, so N topic checks cost one "
                "snapshot read plus N ordered-set lookups — not N independent calls."
            ),
            "max_topics_within_budget": int(
                self.AUTHZ_BUDGET_US_PER_REQUEST // per_check_us
            ),
        }

    @tool("get_multi_tenant_profile")
    def get_multi_tenant_profile(self) -> dict:
        """Describe the tenancy model that decides whether the leak matters."""
        return {
            "clusters_with_topic_level_acls_pct": 78,
            "clusters_multi_tenant_pct": 41,
            "typical_mm2_principal_scope": "describe_on_replicated_topics_only",
            "note": (
                "MM2 is usually deployed with credentials scoped to the topics it "
                "replicates, not cluster-wide DESCRIBE. Any design that requires DESCRIBE "
                "on CLUSTER to use the filter would force operators to broaden MM2's "
                "privileges, which is a worse security outcome than the leak we are "
                "closing."
            ),
        }

    # ------------------------------------------------------------------
    # Authorization alternatives
    # ------------------------------------------------------------------

    def _authz_alternatives(
        self, acls: dict, disclosure: dict, cost: dict, tenancy: dict
    ) -> list[DesignAlternative]:
        return [
            DesignAlternative(
                label="A",
                name="Per-topic DESCRIBE on the filter, unauthorized topics silently dropped",
                proposed_by=self.AGENT_NAME,
                approach=(
                    "Treat every entry in the `topic_partitions` filter as a resource being "
                    "read. In `KafkaApis.handleListGroupsRequest`, before the filter reaches "
                    "the coordinators, call `Authorizer.authorize()` once with a "
                    "`List<Action>` of DESCRIBE-on-TOPIC actions — one per distinct topic in "
                    "the filter. Topics the principal is not authorized on are **removed "
                    "from the filter and not reported**, so the response is indistinguishable "
                    "from one where those topics have no consumers. The surviving filter is "
                    "then applied, and the resulting group list still passes through the "
                    "existing group-level authorization, so the caller sees only groups it "
                    "could already describe. Authorization is therefore the intersection of "
                    "topic DESCRIBE and the existing group check, and the API leaks nothing "
                    "the caller could not already learn."
                ),
                pros=[
                    "Closes all three leak vectors at once: no topology leak, no existence "
                    "oracle (unauthorized and empty are the same response), and no "
                    "cross-tenant inference.",
                    "Follows the established Kafka precedent for filtered reads — omit "
                    "unauthorized entries rather than erroring, exactly as Metadata does "
                    "for unauthorized topics.",
                    f"Costs {cost['total_us']}us per request against a "
                    f"{cost['budget_us']}us budget, because StandardAuthorizer resolves a "
                    f"batched List<Action> against a single ACL snapshot.",
                    f"Works with MM2's real credential scope "
                    f"({tenancy['typical_mm2_principal_scope']}) — no operator has to "
                    "broaden MM2's privileges to adopt the feature.",
                    "Needs no new ACL operation or resource type, so there is nothing extra "
                    "for operators to learn or provision.",
                ],
                cons=[
                    "Silent omission is genuinely confusing to debug: an operator whose "
                    "filter is missing an ACL sees a plausible-looking empty result rather "
                    "than an error. Needs a dedicated authorization log line to be "
                    "diagnosable.",
                    "The intersection semantics have to be specified precisely in the KIP, "
                    "since 'authorized on the topic but not the group' and the reverse both "
                    "have to behave predictably.",
                    f"Adds {cost['checks_per_request']} authorization checks to a request "
                    f"that previously did one, so the filter needs a cap on distinct topics "
                    f"(we suggest {cost['max_topics_within_budget']}) to keep the check "
                    "count bounded.",
                ],
                effort="S",
                risk="low",
                blast_radius=["kafka-security", "kafka-broker", "kafka-clients"],
            ),
            DesignAlternative(
                label="B",
                name="Require DESCRIBE on CLUSTER to use the filter at all",
                proposed_by=self.AGENT_NAME,
                approach=(
                    "Gate the whole filter behind the existing cluster-wide DESCRIBE check: "
                    "if the principal is authorized on CLUSTER it may use "
                    "`topic_partitions`, otherwise the field is rejected with "
                    "CLUSTER_AUTHORIZATION_FAILED. One check, no new semantics."
                ),
                pros=[
                    "Trivially simple to implement and to specify — one existing check, no "
                    "intersection logic, no omission semantics.",
                    "Single authorization check, so zero measurable hot-path cost.",
                    "No ambiguity for operators: either you are a cluster admin or you "
                    "cannot use the filter.",
                ],
                cons=[
                    f"Makes the feature unusable for its own motivating use case. MM2 "
                    f"typically runs with {tenancy['typical_mm2_principal_scope']}, so "
                    "operators would have to grant MM2 cluster-wide DESCRIBE to get the "
                    "latency fix — trading a narrow read path for a broad privilege "
                    "escalation. That is a net loss in security posture.",
                    f"Excludes the {tenancy['clusters_multi_tenant_pct']}% of clusters that "
                    "are multi-tenant precisely because those operators will not hand out "
                    "cluster DESCRIBE, which is the population that most needs efficient "
                    "per-topic group discovery.",
                    "Erroring on an unauthorized filter is itself the existence oracle from "
                    "the disclosure analysis, just moved up a level.",
                    "Coarse-grained gating on a fine-grained resource contradicts the "
                    "direction ACL design has moved in since KIP-11.",
                ],
                effort="S",
                risk="medium",
                blast_radius=["kafka-security", "mirrormaker", "operators"],
            ),
            DesignAlternative(
                label="C",
                name="No new authorization — rely on existing group-level filtering",
                proposed_by=self.AGENT_NAME,
                approach=(
                    "Ship the filter with no authorization change. The result already "
                    "passes through the existing group-level check, so a caller only ever "
                    "sees groups it could have listed anyway; the filter is framed as a "
                    "performance optimization over a result set the caller was already "
                    "entitled to."
                ),
                pros=[
                    "Zero implementation cost and zero hot-path cost.",
                    "The argument is not vacuous — the *group set* returned really is "
                    "bounded by existing group ACLs, so no new group becomes visible.",
                ],
                cons=[
                    "The reasoning confuses which resource is being read. The new "
                    "information is not the group, it is the **edge** between a topic and a "
                    "group, and no existing ACL governs that edge. A principal with broad "
                    "group DESCRIBE and no topic DESCRIBE learns the consumption topology "
                    "of topics it cannot even see.",
                    "Leaves the topic-existence oracle wide open, which is the vector most "
                    "likely to be written up as a CVE after release.",
                    "Retrofitting authorization onto a shipped API is a breaking change. "
                    "Whatever we decide has to be in the KIP, because we will not get a "
                    "second chance to tighten it.",
                    f"Unacceptable for the "
                    f"{tenancy['clusters_with_topic_level_acls_pct']}% of clusters that "
                    "configure topic-level ACLs, whose entire reason for doing so is that "
                    "topic identity is considered sensitive.",
                ],
                effort="S",
                risk="high",
                blast_radius=["every cluster with topic-level ACLs"],
            ),
        ]

    # ------------------------------------------------------------------
    # Consultation handler — round-aware
    # ------------------------------------------------------------------

    def _handle_consultation(
        self, request: ImpactRequest, depth: int
    ) -> ImpactResponse:
        """Answer the authorization question the other teams did not know to ask."""
        acls = self.get_acl_requirements("LISTGROUPS")
        disclosure = self.analyze_information_disclosure(
            "ListGroups", "topic_partitions"
        )
        cost = self.get_authorizer_cost(8)
        tenancy = self.get_multi_tenant_profile()
        alternatives = self._authz_alternatives(acls, disclosure, cost, tenancy)

        alternatives[0].recommended = True
        alternatives[1].rejected_reason = (
            f"Requiring cluster-wide DESCRIBE would force operators to broaden MM2's "
            f"credentials from {tenancy['typical_mm2_principal_scope']} to cluster "
            f"DESCRIBE, which is a worse security outcome than the leak it closes, and it "
            f"excludes the {tenancy['clusters_multi_tenant_pct']}% multi-tenant clusters "
            f"that most need the feature."
        )
        alternatives[2].rejected_reason = (
            "Existing group ACLs bound which groups are returned but do not govern the "
            "topic→group edge, which is the new information. It also leaves the "
            "topic-existence oracle open, and authorization cannot be tightened after the "
            "API ships."
        )

        round_num = request.round_number
        new_concerns: list[str] = []

        if round_num == 1:
            new_concerns = [
                "This change has an authorization consequence that has not been discussed "
                "yet, and it needs to be settled before the KIP goes to vote rather than "
                "after. A topic-scoped filter on ListGroups is a new read path over the "
                "topic→group relationship, and no ACL in the current model covers that "
                "relationship. Group ACLs authorize access to a group and topic ACLs "
                "authorize access to a topic; nothing authorizes learning which groups "
                "consume which topic, because until now no API exposed it.",
                "Concretely: a principal holding broad DESCRIBE on groups but no DESCRIBE "
                "on topic `payments` can today learn nothing about `payments`. With the "
                "filter it can ask which groups consume `payments` and get an answer. Group "
                "names encode service and team identity in practice, so the response is a "
                "readable map of which services consume which data.",
                "The error behaviour matters as much as the success behaviour. If an "
                "unauthorized topic in the filter produces an error, that error "
                "distinguishes 'does not exist' from 'not permitted' and becomes a probe "
                "for topic names across tenant boundaries. Unauthorized topics must be "
                "dropped from the filter silently, so an unauthorized topic and a topic "
                "with no consumers are indistinguishable in the response.",
                f"Authorization must therefore be the *intersection* of per-topic DESCRIBE "
                f"and the existing group-level check. The cost is acceptable: "
                f"{cost['total_us']}us for {cost['checks_per_request']} batched checks "
                f"against a {cost['budget_us']}us budget, because StandardAuthorizer "
                f"resolves a List<Action> against one immutable snapshot. We do want a cap "
                f"of about {cost['max_topics_within_budget']} distinct topics per filter so "
                f"the check count stays bounded.",
            ]
            verdict = "needs_changes"
            summary = (
                f"Flagging an authorization gap nobody has raised yet, because it has to be "
                f"in the KIP and cannot be added later. The filter exposes the topic→group "
                f"edge, and no existing ACL governs that edge — a principal with group "
                f"DESCRIBE but no topic DESCRIBE would learn which services consume topics "
                f"it cannot see. We need per-topic DESCRIBE on each filter entry, "
                f"intersected with the existing group check, with unauthorized topics "
                f"silently dropped so the response is not a topic-existence oracle. Cost is "
                f"{cost['total_us']}us per request against a {cost['budget_us']}us budget, "
                f"so this is cheap. We reject gating the filter on cluster-wide DESCRIBE "
                f"(it would force MM2 to be over-privileged) and reject shipping with no "
                f"authorization change."
            )
        elif round_num == 2:
            verdict = "needs_changes"
            new_concerns = [
                "One addition now that we have agreed on the mechanism: silent omission is "
                "correct for security and hostile to operators, so the KIP needs to require "
                "a DEBUG-level authorization log line naming the topics dropped from the "
                "filter. Without it, a missing ACL looks exactly like a topic with no "
                "consumers and will burn someone a day of debugging.",
            ]
            summary = (
                f"Agreed on alternative A: per-topic DESCRIBE on each filter entry, "
                f"intersected with the existing group-level check, unauthorized topics "
                f"dropped silently. Confirming the cap at "
                f"{cost['max_topics_within_budget']} distinct topics per filter keeps us "
                f"inside the {cost['budget_us']}us hot-path budget. Asking for one "
                f"operability addition: a DEBUG log line naming dropped topics, so an "
                f"operator can tell a missing ACL from an idle topic."
            )
        else:
            verdict = "agreed"
            summary = (
                "Agreed and settled from our side. Per-topic DESCRIBE intersected with the "
                "existing group check, unauthorized topics dropped silently, filter capped "
                f"at {cost['max_topics_within_budget']} distinct topics, and a DEBUG log "
                "line naming dropped topics. The intersection semantics and the omission "
                "behaviour both need to be written into the KIP text, not left to the "
                "implementation. Our authorization test requirements are below."
            )

        return ImpactResponse(
            request_id=request.request_id,
            from_agent=self.AGENT_NAME,
            to_agent=request.from_agent,
            verdict=verdict,
            confidence=0.91,
            summary=summary,
            principal_review=(
                "I want to be clear about why we are in this conversation at all, because "
                "the framing so far has been entirely about latency and heap. The change "
                "creates a new read path over a relationship that no ACL currently "
                "governs. Kafka's authorization model has ACLs for topics and ACLs for "
                "groups, and it has them because those are the resources APIs have "
                "historically exposed. Nothing in the model authorizes learning which "
                "groups consume which topic, for the simple reason that no API has ever "
                "answered that question. This one does, and that makes the topic→group "
                "edge a resource whether we name it as one or not.\n\n"
                "The concrete failure mode: a principal with broad group DESCRIBE and no "
                "DESCRIBE on topic `payments` currently learns nothing about `payments`. "
                "With the filter it asks which groups consume `payments` and gets a list. "
                "Group names in the wild are things like 'billing-reconciler-v3' and "
                "'fraud-scoring-stream', so the response is not an opaque set of "
                "identifiers — it is a readable map from data to the services that read it. "
                "On a multi-tenant cluster that crosses a tenant boundary, and it is the "
                "kind of thing that gets written up as a CVE some months after release, at "
                "which point tightening it is a breaking change.\n\n"
                "The error path deserves as much attention as the success path. If an "
                "unauthorized topic in the filter produces CLUSTER_AUTHORIZATION_FAILED or "
                "UNKNOWN_TOPIC_OR_PARTITION, the difference between those two responses "
                "tells the caller whether the topic exists. That is a probe for topic names "
                "across tenants, and it is a strictly worse leak than the one we set out to "
                "fix. The right behaviour is the one Metadata already uses for unauthorized "
                "topics: omit them. An unauthorized topic and a topic with no consumers must "
                "produce byte-identical responses.\n\n"
                "On the alternative of gating the filter behind cluster-wide DESCRIBE: I "
                "understand the appeal, and I am rejecting it on security grounds rather "
                "than usability ones. MM2 is normally deployed with DESCRIBE scoped to the "
                "topics it replicates. Requiring cluster DESCRIBE to use the filter means "
                "every operator who wants the latency fix grants MM2 cluster-wide read "
                "visibility. We would have closed a narrow inference channel by handing out "
                "a broad privilege — a worse posture than where we started, and one that "
                "persists long after anyone remembers why it was granted.\n\n"
                "The cost objection to doing this properly does not hold up. "
                f"StandardAuthorizer.authorize() takes a List<Action> and resolves the whole "
                f"batch against one immutable ACL snapshot, so {cost['checks_per_request']} "
                f"topic checks cost {cost['total_us']}us against a {cost['budget_us']}us "
                f"budget — one snapshot read and N ordered-set lookups, not N calls. Cap the "
                f"filter at {cost['max_topics_within_budget']} distinct topics and the check "
                "count is bounded by construction. This is cheap, and it is the only option "
                "that is still correct after the API ships."
            ),
            design_alternatives=alternatives,
            recommendation=(
                "Alternative A: require DESCRIBE on TOPIC for each entry in the filter, "
                "intersect with the existing group-level authorization, and silently drop "
                "unauthorized topics so the response cannot be used as a topic-existence "
                "oracle. Both the intersection semantics and the omission behaviour must be "
                "specified in the KIP text."
            ),
            cited_codepaths=[
                "StandardAuthorizer.java",
                "Authorizer.java",
                "Action.java",
                "AclOperation.java",
            ],
            new_concerns=new_concerns,
            open_questions=(
                [
                    "The KIP must state the authorization semantics for the filter "
                    "explicitly: per-topic DESCRIBE intersected with the existing group "
                    "check, and unauthorized topics omitted rather than rejected. This "
                    "cannot be tightened after the API ships.",
                    "Decide whether a filter exceeding the distinct-topic cap is rejected "
                    "or truncated. We prefer rejection with a clear error, since truncation "
                    "would silently return an incomplete group set.",
                ] if round_num == 1 else []
            ),
            follow_up_consultations=[],
            test_requirements=[
                "Unit: a principal with DESCRIBE on CLUSTER and on all filtered topics "
                "receives the full matching group set.",
                "Unit: a principal without DESCRIBE on a filtered topic receives a response "
                "byte-identical to one where that topic has no consumers — no error, no "
                "partial-result marker, nothing that distinguishes the two.",
                "Unit: authorization is the intersection of topic DESCRIBE and group "
                "DESCRIBE — a principal authorized on the topic but not on a matching group "
                "does not see that group.",
                "Security: a filter naming a nonexistent topic and a filter naming an "
                "existing-but-unauthorized topic produce identical responses, so the API is "
                "not a topic-existence oracle.",
                f"Performance: authorization overhead for a filter of "
                f"{self.get_authorizer_cost(8)['max_topics_within_budget']} distinct topics "
                f"stays under {self.AUTHZ_BUDGET_US_PER_REQUEST}us per request.",
                "Unit: a filter exceeding the distinct-topic cap is rejected with a clear "
                "error rather than silently truncated.",
                "Operability: dropped topics are named in a DEBUG authorization log line so "
                "a missing ACL is distinguishable from an idle topic during debugging.",
            ],
        )
