# Runbook: Kafka Security SME Agent

**Agent id:** `kafka-security`
**Domain:** Authorization, ACLs, and information disclosed by public APIs.
**Upstream:** Apache Kafka, `StandardAuthorizer` and the `common/acl` types.

This file is domain memory: architecture, ownership, and the tools this team
uses to inspect its own system. It is not a playbook for any particular ticket.

```
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
```

---

## 1. Architecture

Kafka authorization is a yes/no on *(principal, operation, resource)*. The
Authorizer is invoked from `KafkaApis` on the request path; this team owns
the Authorizer and the ACL model, not the dispatch.

### Resource types and operations

The model is resource-centric. The resources that exist today:

| Resource type | Typical operations | What a grant actually means |
|---|---|---|
| `CLUSTER` | `DESCRIBE`, `ALTER`, `IDEMPOTENT_WRITE` | cluster-scoped admin |
| `TOPIC` | `READ`, `WRITE`, `DESCRIBE`, `CREATE`, `DELETE` | that topic |
| `GROUP` | `READ`, `DESCRIBE`, `DELETE` | that consumer group |
| `TRANSACTIONAL_ID` | `WRITE`, `DESCRIBE` | that transactional id |
| `DELEGATION_TOKEN` | `DESCRIBE`, `OWNER` | that token |

There is no resource type for a *relationship between* two resources. An
ACL on a group does not grant knowledge of which topics that group
consumes; an ACL on a topic does not grant knowledge of which groups
consume it. APIs that expose a new relationship are a new authorization
surface, even when they return only records the caller could already
fetch one-by-one.

### How a check is evaluated

`Authorizer.authorize(requestContext, List<Action>)` takes a batch.
`StandardAuthorizer` resolves the whole batch against one immutable ACL
snapshot (stored in KRaft metadata, owned as *data* by `kafka-broker`,
*interpreted* here). Cost is one snapshot read plus one ordered-set
lookup per Action, not one RPC per Action.

Default-deny: if no matching ACE allows the action, it is denied. Prefix
and wildcard resource patterns (`Literal`, `Prefixed`, `Literal *`) are
part of the lookup.

### Filtered-read convention

When an API accepts a filter over resources, Kafka's established behaviour
(Metadata, DescribeClientQuotas / KIP-546, ListGroups' existing
group-level filter) is:

- **omit** unauthorized entries
- **do not error** on them

Erroring (`TOPIC_AUTHORIZATION_FAILED` vs `UNKNOWN_TOPIC_OR_PARTITION`)
turns the API into an existence oracle: the caller learns whether a name
it is not allowed to see refers to something real. Omission makes
"unauthorized" and "empty" indistinguishable in the response. That is
deliberate. It is hostile to operators debugging missing ACLs, which is
why dropped entries should be named in a DEBUG authorization log line.

### Tenancy

A large fraction of production clusters use topic-level ACLs; a smaller
but important fraction are multi-tenant. Principals in those clusters are
scoped to the topics (and groups) they own, not to `DESCRIBE` on
`CLUSTER`. Features that require cluster-wide `DESCRIBE` to be useful
force operators to broaden credentials, which is a security regression
regardless of the feature's intent.

`ListGroups` today: `DESCRIBE` on `CLUSTER` authorizes the full list;
without it, the broker filters to groups the principal holds `DESCRIBE`
on `GROUP` for. There is no topic-ACL involvement, because the RPC does
not take a topic.

---

## 2. CODEOWNERS

```
server/src/main/java/org/apache/kafka/server/authorizer/
├── Authorizer.java                 # interface KafkaApis calls
└── Action.java                     # (principal, operation, resource) tuple

metadata/src/main/java/org/apache/kafka/metadata/authorizer/
├── StandardAuthorizer.java         # default implementation
└── StandardAuthorizerData.java     # immutable ACL snapshot

clients/src/main/java/org/apache/kafka/common/acl/
├── AclOperation.java
├── AclBinding.java
├── AclBindingFilter.java
└── ResourcePattern.java
```

### Not owned — other teams change these

| Path | Owner | Boundary |
|---|---|---|
| `core/src/main/scala/kafka/server/KafkaApis.scala` | `kafka-broker` | *where* authorize() is called; this team owns *what* it means |
| `metadata/src/main/java/org/apache/kafka/controller/` | `kafka-broker` | ACL records in KRaft; this team consumes the image |
| `clients/src/main/resources/common/message/` | `kafka-clients` | request schemas that may need an authorization section in the KIP |
| `group-coordinator/` | `group-coordinator` | group state being authorized |

---

## 3. Tools

### `get_acl_requirements(api_name)`
Which ACLs `api_name` requires today, fallback behaviour for unprivileged
principals, and any relationship the API exposes that no ACL currently
covers.

### `analyze_information_disclosure(api_name, new_field)`
What adding `new_field` lets a caller infer: leak vectors, severity,
precedent, whether it is exploitable with the current ACL set.

### `get_authorizer_cost(checks_per_request)`
Hot-path cost of `checks_per_request` Actions: per-check microseconds,
total, budget, whether a batch of that size fits.

### `get_multi_tenant_profile()`
How clusters are actually configured: share with topic-level ACLs, share
that are multi-tenant, typical principal scope for connector workloads.

---

## 4. Invariants this team will not violate

- **New read paths over a relationship need authorization for that
  relationship.** Existing ACLs on the *nodes* do not automatically cover
  the *edge*.
- **Filtered reads omit, they do not error.** Erroring is an existence
  oracle.
- **Do not "fix" a narrow leak by requiring `DESCRIBE` on `CLUSTER`.**
  That broadens privilege for every caller that wants the feature.
- **Authorization semantics of a public API belong in the KIP.** Tightening
  them after the API ships is a breaking change.
- **Silent omission needs a DEBUG log line** naming what was dropped, or
  operators cannot tell a missing ACL from an empty result.
