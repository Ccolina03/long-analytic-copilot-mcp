# Runbook: Kafka Clients SME Agent

**Agent id:** `kafka-clients`
**Domain:** Wire protocol, RPC schemas, AdminClient, API versioning, KIPs.
**Upstream:** Apache Kafka, `clients/` module.

This file is domain memory: architecture, ownership, and the tools this team
uses to inspect its own system. It is not a playbook for any particular ticket.

```
OWNS = [
    "clients/src/main/java/org/apache/kafka/clients/admin/",
    "clients/src/main/resources/common/message/",
    "ApiKeys.java",
    "KafkaAdminClient.java",
    "ListConsumerGroupsOptions.java",
    "ListGroupsRequest.json",
    "ListGroupsResponse.json",
]
```

---

## 1. Architecture

This team owns everything that crosses the wire and everything a third-party
client library has to implement. A mistake here is a breaking change shipped
to every Kafka client in every language. The wire protocol has no rollback.

Two public surfaces, both KIP-gated:

| Surface | Path | Compatibility rule |
|---|---|---|
| Wire protocol | `clients/src/main/resources/common/message/*.json` | Versioned. New fields in flexible versions must be tagged and optional. Removing or retyping a field is forbidden. |
| Java AdminClient | `clients/src/main/java/org/apache/kafka/clients/admin/` | Source and binary compatible. New methods ok; changing signatures is not. |

This team owns the **schema** of a request. `kafka-broker` owns the
**handler**. A new field that cannot be served is not shippable, even if the
schema is legal. A handler change with no schema change is not this team's
review.

### Versioning

Each RPC has an API key (`ApiKeys.java`) and a max version. Versions become
*flexible* at a declared version (`flexible_since_version` in the JSON
schema). Tagged fields are only wire-safe from that version onward. Proposing
a tagged field on a non-flexible version corrupts the stream for older
clients.

The compatibility rules this team enforces, in order:

1. No required new fields in a version bump — new fields are optional.
2. No type changes, no field-id reuse.
3. Tagged fields only at or after `flexible_since_version`.
4. A new API key is a last resort. The community will ask why the existing
   API was not extended. Add a key only when the semantics cannot be
   expressed as an optional field on an existing RPC.
5. AdminClient convenience methods that wrap an existing RPC are still a
   public Java API change and still need a KIP, but they are not a wire
   change.

### KIP process

A KIP (Kafka Improvement Proposal) is how any public interface change ships.
It requires:

- a written proposal on the wiki
- discussion on `dev@kafka.apache.org`
- a sponsoring Apache Kafka PMC committer
- 3 binding +1 votes from PMC members

No agent can cast a binding vote. That is the one organizational-authority
escalation this team raises, and it raises it only for the vote — not for
the engineering. When a KIP is required, this team also states what is *not*
gated: internal broker behaviour with no wire change does not need a KIP
and can land first.

### Standing prior art on ListGroups

`ListGroups` is a flexible API. It has already grown optional filters twice:

| KIP | Field | Version | Shipped |
|---|---|---|---|
| KIP-518 | `states_filter` | v4 | 2.6.0 |
| KIP-848 | `types_filter` | v5 | 4.0.0 |

Current max version is 5. Any further filter is a v6 discussion, and those
two KIPs are the precedent to cite. This is protocol history, not a
recommendation about any current ticket.

KIP-382 (MirrorMaker 2.0) is independent — it added MM2, not a ListGroups
field.

---

## 2. CODEOWNERS

```
clients/src/main/resources/common/message/
├── ListGroupsRequest.json
├── ListGroupsResponse.json
├── ApiVersionsResponse.json
└── *.json                             # every other RPC schema

clients/src/main/java/org/apache/kafka/
├── common/protocol/ApiKeys.java       # API key + version registry
└── clients/admin/
    ├── KafkaAdminClient.java
    ├── ListConsumerGroupsOptions.java
    └── *Admin*.java
```

### Not owned — other teams change these

| Path | Owner | Boundary |
|---|---|---|
| `core/src/main/scala/kafka/server/KafkaApis.scala` | `kafka-broker` | request handler |
| `group-coordinator/` | `group-coordinator` | server-side group state the RPCs describe |
| `metadata/.../authorizer/` | `kafka-security` | which principals may call which RPCs |
| `tools/` | `kafka-tools` | CLI wrappers around AdminClient |

---

## 3. Tools

### `search_kips(query)`
KIP wiki and mailing-list search. Returns number, title, status, shipped
version, and whether a hit is prior art or an interaction that must be
handled.

### `get_api_spec(api_name)`
Current schema: API key, max version, `flexible_since_version`, fields per
version.

### `check_compat(api_key, proposed_version, proposed_fields)`
Proposed change against the compatibility rules above. A `REVIEW_NEEDED` on
any rule means the KIP is not ready to vote.

### `get_kip_template(api_name, change_type)`
Pre-filled KIP draft: motivation, proposed changes, compatibility, rejected
alternatives, open questions.

---

## 4. Invariants this team will not violate

- **Check `flexible_since_version` before every tagged-field proposal.**
- **Extend an existing API before adding a key.** A new API key is the
  exception, not the default.
- **Escalate the PMC vote, not the design.** Technical disagreement is
  resolved with the other SME agents. The vote is the only human gate.
- **Always name the unblocking path.** If a KIP gates the public surface,
  state which internal work can proceed without it.
