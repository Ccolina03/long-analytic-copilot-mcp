# SME Agent Network — Architecture

> **One-line pitch:** Give us a bug ticket, and a network of specialized AI
> engineers — each with persistent domain ownership, each armed with the
> right tools — will figure out who needs to be involved, why, what the
> dependencies are, and what needs to change. No human plays coordinator.

---

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [Competitive Landscape — Why This Is Different](#2-competitive-landscape--why-this-is-different)
3. [Core Thesis](#3-core-thesis)
4. [The Three-Layer Agent Model](#4-the-three-layer-agent-model)
5. [The Engineering Knowledge Graph](#5-the-engineering-knowledge-graph)
6. [Agent-to-Agent Protocol](#6-agent-to-agent-protocol)
7. [Ownership Boundaries](#7-ownership-boundaries)
8. [The Ticket as Orchestration Layer](#8-the-ticket-as-orchestration-layer)
9. [Layer-by-Layer System Architecture](#9-layer-by-layer-system-architecture)
10. [Worked Example — Consumer Groups Per Topic](#10-worked-example--consumer-groups-per-topic)
11. [The Moat — Compounding Dependency Graph](#11-the-moat--compounding-dependency-graph)
12. [Phased Rollout](#12-phased-rollout)
13. [The Killer Demo](#13-the-killer-demo)
14. [Repository Layout](#14-repository-layout)
15. [Data Schema](#15-data-schema)
16. [Adding a New SME Agent](#16-adding-a-new-sme-agent)
17. [Build Roadmap](#17-build-roadmap)
18. [Design Decisions](#18-design-decisions)

---

## 1. Problem Statement

When a bug is opened, an engineer has to:

- Figure out **which team owns** the affected code
- Understand **unfamiliar codepaths** across service boundaries
- Find the **right SME** for each domain
- Determine **what other systems** could be affected by a fix
- Coordinate **approvals** from multiple team leads
- Keep all of this in their head while also debugging

This process takes hours to days. Most of the delay is **coordination
overhead**, not actual engineering work.

The root cause: every engineering organization has a dependency graph —
which services call which, which teams own what, which changes require
whose approval. **That graph lives in people's heads, not in any system.**

---

## 2. Competitive Landscape — Why This Is Different

The multi-agent-orchestration space is already crowded. Before building,
it's worth being precise about what's already been claimed and where the
gap is.

| Company | Core abstraction | What they own |
|---|---|---|
| **Superset** | Many coding agents → parallel execution | Running hundreds of generic coding agents in parallel on independent tasks |
| **Agent Relay** | Infrastructure layer | Shared messaging, session history, tools, GitHub/Linear/Slack plumbing between agents |
| **Glen** | Organizational memory | Aggregates agent sessions, Slack, PRs, tickets, docs — retrieval for future agents |
| **Linzumi** | Human → many agents | Team chat directs dozens of coding agents; turns org decisions into a source of truth |

**None of them own: persistent domain expertise + structured cross-domain
reasoning + explicit ownership boundaries.**

```
Superset:      many agents  →  parallel execution
Agent Relay:   many agents  →  shared plumbing (messaging/tools/history)
Glen:          many agents  →  shared memory (retrieval)
Linzumi:       human        →  many agents (command & control)

THIS PROJECT:  ticket → SME agents → SME agents (typed consultation)
                      → coordinated action → human approval only when needed
```

The distinction that matters:

> Those products answer **"how do agents run / talk / remember."**
> This product answers **"who should be involved, why, and what's the
> technical impact — grounded in actual code ownership."**

Agent Relay is closer to an infrastructure layer this product could sit on
top of — **not a competitor to build a fork of**. Glen is the most adjacent
risk (organizational memory), but memory is retrieval; this system is
**reasoning + ownership + action**, and every resolved ticket makes the
underlying dependency graph structurally better, not just semantically
searchable.

---

## 3. Core Thesis

Before scaling this into a platform, the fundamental hypothesis to prove is:

> **Does specialized agent collaboration — agents with persistent domain
> ownership, typed communication, and explicit boundaries — actually
> outperform one powerful coding agent with a giant context window?**

If yes, the multi-agent architecture below is justified.
If no, none of this complexity is worth building.

This is why the MVP (§12) intentionally starts with **investigation and
impact analysis only** — no autonomous code changes — until that
hypothesis is validated against a single-agent baseline.

---

## 4. The Three-Layer Agent Model

Every SME agent is not just "an LLM with a system prompt." It has three
distinct layers of context that separate *what it permanently knows* from
*what's happening right now* from *what it's allowed to do*.

```
                    ┌─────────────────────┐
                    │   Team SME Agent     │
                    │  "Cluster Linking    │
                    │       expert"        │
                    └──────────┬──────────┘
                               │
             ┌─────────────────┼─────────────────┐
             ▼                 ▼                 ▼
       DOMAIN MEMORY      LIVE CONTEXT       CAPABILITIES
     (persistent, slow-    (per-ticket,      (what it's allowed
      changing)             fast-changing)     to touch/call)
             │                 │                 │
       ┌─────┴─────┐      ┌────┴─────┐      ┌────┴─────┐
       │ Code      │      │ Ticket   │      │ GitHub   │
       │ ownership │      │ text     │      │ Jira     │
       │ Design    │      │ Recent   │      │ Slack    │
       │ decisions │      │ incidents│      │ CI/CD    │
       │ Historical│      │ Current  │      │ MCP      │
       │ PRs       │      │ changes  │      │ tools    │
       │ Invariants│      │ Related  │      │ (scoped) │
       │ Known bugs│      │ traces   │      │          │
       │ Tests     │      │          │      │          │
       └───────────┘      └──────────┘      └──────────┘
```

### Domain Memory (persistent, curated — not a vector dump)

This is what makes the agent an SME rather than a generic assistant.
Per §7's ownership boundary, each agent maintains:

- Repositories and specific packages/files it owns
- Architecture docs and design rationale ("why" behind non-obvious code)
- Historical PRs and the decisions embedded in them
- Known invariants (e.g. "PID epoch must remain monotonic")
- Past incidents and their root causes
- Test coverage for its owned codepaths
- Slack/discussion threads that explain tribal knowledge

**Not** a single vector database with everything dumped in. See §5 —
this is a structured knowledge graph, because "who owns what and what
depends on what" is a graph problem, not a similarity-search problem.

### Live Context (per-ticket, ephemeral)

The current ticket, related recent incidents, in-flight changes to the
same codepaths, and any trace/log evidence pulled at investigation time.
This is what changes every time the agent is invoked — it does not persist
between tickets (though the *outcome* feeds back into Domain Memory, see §11).

### Capabilities (explicit, scoped)

What the agent is actually allowed to call: GitHub (read PRs, open PRs),
Jira (read/update tickets), Slack (post updates), CI/CD (trigger test runs),
and MCP tools (query logs, search code, run domain-specific analyses).
Capabilities are scoped per agent — the Billing agent cannot open a PR
against the Broker team's repository.

---

## 5. The Engineering Knowledge Graph

Domain Memory is not a vector database with everything dumped in. It's a
structured graph, because ownership, dependency, and causality are graph
relationships — semantic similarity search cannot answer "what does this
PR block" or "which team must approve this."

**Storage: Postgres** with a graph-shaped schema (edges as rows), not a
dedicated graph DB — keeps the MVP boring and fast to ship. Move to a real
graph DB only if query patterns demand it later.

```
Service
  ├── owns          → Repository
  ├── owns          → API
  ├── depends_on    → Service
  ├── modifies      → Database
  ├── emits         → Event
  ├── consumes      → Event
  └── has_invariant → Rule

CodePath
  ├── calls         → CodePath
  ├── modifies      → State
  └── protected_by  → Test

Decision
  ├── applies_to    → CodePath
  ├── introduced_by → PR
  └── rationale     → Explanation

Team
  ├── owns          → Service
  ├── owns          → Repository
  └── must_approve  → ChangeType
```

### Minimal Postgres schema for the MVP

```sql
CREATE TABLE entities (
  id          UUID PRIMARY KEY,
  type        TEXT NOT NULL,   -- 'service' | 'repository' | 'codepath' | 'decision' | 'team' | 'rule'
  name        TEXT NOT NULL,
  metadata    JSONB            -- repo path, line ranges, description, etc.
);

CREATE TABLE edges (
  id          UUID PRIMARY KEY,
  from_id     UUID REFERENCES entities(id),
  to_id       UUID REFERENCES entities(id),
  relation    TEXT NOT NULL,   -- 'owns' | 'depends_on' | 'calls' | 'protected_by' | 'must_approve' | ...
  evidence    JSONB,           -- trace_ids, PR links, ticket ids that established this edge
  confidence  FLOAT DEFAULT 1.0,
  created_at  TIMESTAMPTZ DEFAULT now(),
  last_seen_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_edges_from ON edges(from_id, relation);
CREATE INDEX idx_edges_to   ON edges(to_id, relation);
```

**Where edges come from:**

| Source | Produces |
|---|---|
| Log traces (`trace_id` co-occurrence across services) | `Service --depends_on--> Service`, weighted by call volume |
| Git history / CODEOWNERS | `Team --owns--> Repository`, `Team --owns--> CodePath` |
| PR descriptions + review comments | `Decision --applies_to--> CodePath`, `Decision --introduced_by--> PR` |
| Resolved ticket outcomes (this system's own output) | `ChangeType --must_approve--> Team` (learned from who actually approved past changes) |
| Static analysis (call graph extraction) | `CodePath --calls--> CodePath` |

This is the compounding asset — see §11.

---

## 6. Agent-to-Agent Protocol

Agents do not "chat" with each other in free-form prose. Free-form chat
between LLMs produces free-form prose that the *next* LLM has to
re-interpret — errors compound with every hop. Instead, agents exchange a
**typed, structured protocol**.

### Request

```json
{
  "from": "cluster-linking-agent",
  "to": "producer-agent",
  "request_type": "impact_analysis",
  "change": "producer endpoint switches from cluster A to B",
  "question": [
    "What client state is affected?",
    "What invariants must hold?",
    "What codepaths handle rebootstrap?"
  ]
}
```

### Response

```json
{
  "impact": "HIGH",
  "affected_components": [
    "KafkaProducer",
    "MetadataUpdater",
    "Sender"
  ],
  "invariants": [
    "PID epoch must remain monotonic",
    "metadata must be refreshed after endpoint change"
  ],
  "codepaths": [
    "Producer#maybeWaitForProducerId",
    "Metadata#requestUpdate"
  ],
  "tests": [
    "ProducerRebootstrapTest"
  ],
  "confidence": 0.87
}
```

**Why this matters architecturally:** the requesting agent (or the
Orchestrator) reasons over *actual technical claims* — a list of
components, a list of invariants, a confidence score — instead of another
LLM's prose summary of prose. This is auditable, diffable, and can be
validated against the knowledge graph (§5) before being trusted.

### Full request/response schema

```proto
message ImpactRequest {
  string from_agent          = 1;
  string to_agent            = 2;
  string request_type        = 3;  // "impact_analysis" | "approval" | "evidence_lookup"
  string change_description  = 4;
  repeated string questions  = 5;
  string ticket_id           = 6;
}

message ImpactResponse {
  string   impact_level          = 1;  // "LOW" | "MEDIUM" | "HIGH"
  repeated string affected_components = 2;
  repeated string invariants     = 3;
  repeated string codepaths      = 4;
  repeated string tests          = 5;
  float    confidence            = 6;
  repeated string open_questions = 7;  // things the agent could NOT determine
}
```

`open_questions` is deliberate — an agent admitting "I don't know" is a
first-class, structured output, not a hallucinated guess. This is what lets
the Orchestrator decide when to escalate to a human (§8).

---

## 7. Ownership Boundaries

Every agent has an explicit, non-overlapping ownership boundary — this is
what turns "five LLMs with different prompts" into something that mirrors
a real engineering org.

```
Cluster Linking Agent
  owns:
    cluster-link/
    mirror-topic/
    switchover/

KRaft Agent
  owns:
    metadata/
    quorum/
    controller/

Producer Agent
  owns:
    clients/producer/
    clients/transactions/

Consumer Team Agent
  owns:
    core/coordinator/group/
    clients/consumer/

OSS Kafka Agent
  owns:
    clients/.../message/*.json     (protocol schema)
    KIP process / mailing list

Billing Agent
  owns:
    metering/
    pricing/
    subscriptions/
```

**The rule:** when an agent's analysis touches a codepath it does not own,
it must not guess. It issues a typed `ImpactRequest` (§6) to the owning
agent. This is enforced at the orchestration layer, not just convention —
the Orchestrator validates that any `codepaths` cited in a `Finding` fall
within the issuing agent's declared ownership, or flags the finding as
`needs_verification`.

See `runbooks/*.md` in this repo for the concrete ownership boundaries of
the three agents currently scaffolded (`kora-global`, `consumer-team`,
`oss-kafka`) — each runbook lists exact repository paths and line ranges.

---

## 8. The Ticket as Orchestration Layer

The ticket is not something an agent merely *answers*. It is the **unit of
work** that drives the entire pipeline — investigation, cross-team impact
analysis, root cause, fix planning, and (later phases) code + tests + PR.

```
                    Jira / GitHub Ticket
                            │
                            ▼
                    ┌───────────────┐
                    │  Triage Agent │
                    └───────┬───────┘
                            │
                    identify domains
              (via knowledge graph §5 +
               keyword/embedding match)
                            │
          ┌─────────────────┼─────────────────┐
          ▼                 ▼                 ▼
      CL SME Agent     KRaft SME Agent   Client SME Agent
          │                 │                 │
          │◄── ImpactRequest / ImpactResponse ─┤
          │        (typed protocol, §6)        │
          └─────────────────┼─────────────────┘
                            ▼
                     Impact Analysis
                            │
                            ▼
                       Root Cause
                            │
                            ▼
                      Fix Planner
                            │
                  ┌─────────┴─────────┐
                  ▼                   ▼
              Code Agent          Test Agent
                  │                   │
                  └─────────┬─────────┘
                            ▼
                        Draft PR
                            │
                            ▼
                    Human approval gate
              (only if requires_human=true)
```

At every stage, the ticket accumulates structured artifacts — findings,
impact responses, evidence, confidence scores — rather than a growing wall
of chat transcript. This is what makes the final output auditable:
"here is the root cause, here is the evidence, here is what's still
uncertain, here is the proposed fix."

---

## 9. Layer-by-Layer System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  INPUTS                                                              │
│  GitHub Issues · Jira Webhooks · Slack Alerts · Manual HTTP POST    │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ HTTPS
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  API GATEWAY  (FastAPI)                                              │
│  • Single external entry point, auth + rate limiting                 │
│  • Normalizes ticket format (Jira/GitHub/raw → Ticket proto)        │
│  • Translates HTTP → gRPC, streams findings back as they arrive      │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ gRPC: TriageTicket(Ticket)
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  ORCHESTRATOR AGENT                                                  │
│  • Agent registry (SME agents self-register on boot)                │
│  • Domain classifier — queries the Knowledge Graph (§5), not just    │
│    keyword match, to find which agents own the affected entities    │
│  • Runs Investigate() on all relevant agents in parallel            │
│  • Routes typed ImpactRequest/Response between agents (§6)          │
│  • Validates cited codepaths against declared ownership (§7)        │
│  • Builds TriageResult; escalates to human only if needs_human=true │
└──────┬────────────┬────────────┬────────────┬────────────┬──────────┘
       │ gRPC       │ gRPC       │ gRPC       │ gRPC       │ gRPC
       ▼            ▼            ▼            ▼            ▼
┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
│  SME     │ │  SME     │ │  SME     │ │  SME     │ │  SME     │
│  Agent   │ │  Agent   │ │  Agent   │ │  Agent   │ │  Agent   │
│  Kora    │ │ Consumer │ │  OSS     │ │  Broker  │ │ Billing  │
│  Global  │ │  Team    │ │  Kafka   │ │  Team    │ │  Team    │
│          │ │          │ │          │ │          │ │          │
│ 3-layer  │ │ 3-layer  │ │ 3-layer  │ │ 3-layer  │ │ 3-layer  │
│ context  │ │ context  │ │ context  │ │ context  │ │ context  │
│ (§4)     │ │ (§4)     │ │ (§4)     │ │ (§4)     │ │ (§4)     │
└────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘
     │            │            │             │            │
     └────────────┴────────────┴─────────────┴────────────┘
                               │ MCP tool calls
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  MCP TOOL SERVER  (FastAPI)                                          │
│  Shared observability tools, callable by any agent's Capabilities   │
│  layer: query_logs · top_errors · search_keyword · blast_radius     │
│  Plus domain-specific tools declared per-agent (see runbooks/)       │
└──────────────────────────────┬──────────────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  DATA LAYER                                                          │
│                                                                      │
│  Knowledge Graph (Postgres)  ← entities + edges, the org's memory   │
│  Log pipeline (Kafka → Delta)← Bronze/Silver/Gold, trace_id edges    │
│  GitHub/Jira/Slack ingestion ← PRs, decisions, tribal knowledge      │
└─────────────────────────────────────────────────────────────────────┘
```

The log/Kafka/Delta pipeline scaffolded earlier in this repo becomes one
**input source** to the Knowledge Graph (it's where `Service --depends_on-->
Service` edges are mined from `trace_id` co-occurrence) — not the center of
the architecture. The center is the Knowledge Graph plus the typed
agent protocol.

---

## 10. Worked Example — Consumer Groups Per Topic

**Ticket:**
> "Cluster Linking offset clamping during failover is too slow. Currently
> calls `ListGroups()` on entire cluster then filters. Need topic-partition
> scoped group lookup."

**Current bottleneck:**
```
clampOffsets(topic, partition):
  all_groups = ListGroups()             # O(n_groups) — 8s for 50k groups
  for group in all_groups:
    if topic in DescribeGroup(group):   # N extra RPC calls
      clamp(group, topic, partition)
```

**Desired API:**
```
clampOffsets(topic, partition):
  groups = ListGroupsForTopicPartition(topic, partition)  # O(1) — <50ms
  for group in groups:
    clamp(group, topic, partition)
```

### Consultation flow, in the typed protocol from §6

```
13:42:01  Ticket received by Orchestrator

13:42:08  Orchestrator queries Knowledge Graph:
          "ListGroups" codepath → owned_by → consumer-team
          "clampOffsets" codepath → owned_by → kora-global
          → routes to: kora-global, consumer-team, oss-kafka

13:42:12  kora-global-agent.Investigate(ticket)
          tool: get_failover_latency() → p99=11,400ms, 91% in listGroups
          tool: get_offset_clamp_trace() → bottleneck confirmed
          Finding: needs_from=[consumer-team, oss-kafka], confidence=0.95

13:42:21  kora-global → consumer-team: ImpactRequest
          {
            "request_type": "impact_analysis",
            "change": "need topic-partition scoped group lookup",
            "questions": ["Can GroupCoordinator support an indexed lookup?",
                          "What's the memory cost?"]
          }

13:42:27  consumer-team-agent responds: ImpactResponse
          {
            "impact": "MEDIUM",
            "affected_components": ["GroupCoordinator", "GroupMetadata"],
            "invariants": ["group state must stay consistent across rebalance"],
            "codepaths": ["GroupCoordinator#handleListGroups"],
            "confidence": 0.9,
            "open_questions": ["needs new Kafka API version — ask oss-kafka"]
          }

13:42:31  consumer-team → oss-kafka: ImpactRequest
          "Does adding a topic_partitions filter to ListGroups v5 need a KIP?"

13:42:44  oss-kafka-agent responds: ImpactResponse
          tool: search_kips("ListGroups topic partition filter")
               → "KIP-518 is closest precedent, does NOT cover this. New KIP required."
          { "impact": "HIGH", "confidence": 0.92,
            "open_questions": ["KIP-848 compatibility needs explicit review"] }

13:43:12  Orchestrator validates all cited codepaths against ownership (§7)
          → all valid, no unverified claims

13:43:20  Orchestrator builds TriageResult:
          execution_order: [kora-global confirm scope → consumer-team draft KIP
                            → oss-kafka vote (~4wk) → consumer-team implement
                            → kora-global integrate]
          approvals_needed: [consumer-team lead, oss-kafka committer]
          requires_human: true
          escalation_reason: "Two team lead approvals required before implementation"
```

Full domain runbooks for these three agents — including exact repository
paths, line ranges, and tool specifications — live in:

- [`runbooks/kora-global-sme.md`](./runbooks/kora-global-sme.md)
- [`runbooks/consumer-team-sme.md`](./runbooks/consumer-team-sme.md)
- [`runbooks/oss-kafka-sme.md`](./runbooks/oss-kafka-sme.md)

---

## 11. The Moat — Compounding Dependency Graph

This is the core defensibility argument.

After processing enough tickets, the system learns chains that no single
document describes anywhere:

```
Switchover
   ↓
Mirror state
   ↓
KRaft record
   ↓
Metadata propagation
   ↓
Producer rebootstrap
   ↓
PID/epoch
   ↓
EOS transaction
```

So when a new ticket arrives mentioning "switchover," the Orchestrator
already knows — before calling a single agent — that this apparently local
change potentially crosses five technical domains. **That's difficult for
a generic coding agent with a big context window to reconstruct from
scratch on every single ticket.**

Every resolved ticket writes back into the Knowledge Graph (§5):

- New `depends_on` edges discovered during impact analysis
- New `must_approve` edges learned from who actually signed off
- Updated `confidence` scores on existing edges that were confirmed or
  contradicted by this ticket's outcome
- New `Decision` nodes linking the resulting PR back to the rationale

This means the system's answer to "who needs to be involved" gets
**structurally better with every ticket**, not just "more data for
retrieval." A competitor starting today has to rebuild this graph from
zero; a customer running this for a year has a graph a competitor cannot
buy or scrape.

---

## 12. Phased Rollout

Deliberately **not** starting with autonomous code changes. The first
question that must be answered is the Core Thesis (§3) — until specialized
agent collaboration is proven to beat a single strong agent, building
Phase 2/3 is premature.

### Phase 1 — Investigation & Impact Analysis (MVP)

```
Ticket → investigation → cross-team impact analysis → root cause →
recommended fix (written up, NOT auto-applied)
```

Success metric: triage quality and speed vs. a human on-call engineer,
and vs. a single large-context-window coding agent given the same ticket
and full repo access.

### Phase 2 — Draft Changes

```
Phase 1 output → branch + tests + draft PR (human merges)
```

Only after Phase 1 demonstrates that cross-agent impact analysis catches
things a single agent misses (the actual differentiator to prove).

### Phase 3 — Autonomous Remediation

```
Phase 2 output → auto-merge for low-risk, high-confidence changes →
human review only for the escalated subset
```

Gated behind sustained Phase 2 accuracy in production.

---

## 13. The Killer Demo

The demo is not "we have an AI that knows your docs." It's watching a
ticket get triaged by a live network of specialists, with a visible
timeline of who talked to whom and why.

```
13:42:01  Ticket received

13:42:08  Cluster Linking Agent claims ticket

13:42:21  → asks KRaft Agent about metadata transition

13:42:27  → asks Producer Agent about rebootstrap

13:42:31  → asks Consumer Agent about offset behavior

13:42:44  Agents identify shared codepath

13:43:12  Root cause identified

13:44:05  Regression test generated

13:45:19  PR opened

13:45:22  Ticket updated with:
          - root cause
          - affected components
          - evidence
          - PR
          - unresolved questions
```

Every line in that timeline corresponds to a real, typed message in the
protocol from §6 — not a scripted narration. The demo should be able to
show the raw `ImpactRequest`/`ImpactResponse` JSON behind any line on
request, because that auditability is the product.

---

## 14. Repository Layout

```
log-analytics-copilot/
│
├── proto/
│   ├── logs.proto               # LogEvent + LogIngestionService (existing)
│   └── sme_agents.proto         # Ticket, Finding, ImpactRequest/Response,
│                                 # Orchestrator + SMEAgent RPCs (§6)
│
├── knowledge-graph/
│   ├── schema.sql                # entities + edges tables (§5)
│   ├── ingest_github.py          # PRs, CODEOWNERS → ownership edges
│   ├── ingest_traces.py          # trace_id co-occurrence → depends_on edges
│   └── ingest_ticket_outcomes.py # resolved tickets → must_approve edges
│
├── gateway/
│   ├── main.py                   # FastAPI — external entry point
│   └── Dockerfile
│
├── orchestrator/
│   ├── main.py                   # gRPC server + agent registry
│   ├── classifier.py             # Knowledge-graph-driven domain classifier
│   ├── consultation.py           # Investigate → ImpactRequest/Response loop
│   ├── ownership_validator.py    # checks cited codepaths against §7 boundaries
│   └── Dockerfile
│
├── agents/
│   ├── base_agent.py             # SMEAgentBase + @tool decorator + 3-layer context (§4)
│   ├── kora_global_agent.py
│   ├── consumer_team_agent.py
│   ├── oss_kafka_agent.py
│   ├── broker_team_agent.py      # future
│   ├── billing_team_agent.py     # future
│   └── Dockerfile
│
├── runbooks/                     # persistent Domain Memory per agent (§4)
│   ├── kora-global-sme.md
│   ├── consumer-team-sme.md
│   └── oss-kafka-sme.md
│
├── mcp-server/
│   ├── main.py                   # FastAPI MCP tool server (existing)
│   ├── tools.py                  # Tool implementations (existing)
│   └── requirements.txt
│
├── log-pipeline/                 # ONE input source to the Knowledge Graph,
│   ├── kafka_to_delta.py         # not the center of the architecture
│   ├── build_service_graph.py    # trace_id → depends_on edges (feeds §5)
│   └── ...
│
├── scripts/
│   ├── kafka-smoke.sh
│   ├── spark-smoke.sh
│   └── produce-fake-logs.py
│
├── docker-compose.yml
├── README.md
└── ARCHITECTURE.md               ← this file
```

---

## 15. Data Schema

### LogEvent (proto/logs.proto)

| Field | Type | Notes |
|---|---|---|
| `timestamp` | string | ISO-8601 UTC |
| `service` | string | Partition key in Silver table |
| `level` | string | DEBUG / INFO / WARN / ERROR |
| `message` | string | Free-form, tokenized for keyword index |
| `trace_id` | string | Groups events across services — feeds `depends_on` edges into the Knowledge Graph |
| `event_id` | string | Unique per log line, used for dedup |
| `host` | string | Originating host |

### Knowledge Graph tables (Postgres — see §5)

| Table | Description |
|---|---|
| `entities` | Services, repositories, codepaths, decisions, teams, rules |
| `edges` | Typed relationships between entities, with evidence + confidence |

### Delta tables (log pipeline — one input source, see §9)

| Table | Layer | Description |
|---|---|---|
| `bronze_logs` | Bronze | Raw JSON from Kafka, append-only |
| `silver_logs` | Silver | Parsed, deduped on `(trace_id, event_id)`, partitioned by service |
| `service_graph` | Gold | `(from_service, to_service, co_occurrences)` — feeds Knowledge Graph `depends_on` edges |

### Finding / ImpactResponse (proto/sme_agents.proto)

Every SME agent returns these same shapes (§6). Fields are additive —
agents only fill what they know, and `open_questions` is a first-class
field for admitted uncertainty.

---

## 16. Adding a New SME Agent

1. Write a runbook in `runbooks/my-team-sme.md`: what the team owns, exact
   repo paths/line ranges, what tools it needs (see existing runbooks for
   the format).

2. Create `agents/my_team_agent.py`:

```python
from agents.base_agent import SMEAgentBase, tool

class MyTeamAgent(SMEAgentBase):
    AGENT_NAME = "my-team"
    DOMAIN     = "plain English description of what this team owns"
    OWNS       = ["path/to/repo/", "another/owned/path/"]  # enforced by §7

    @tool("my_tool")
    async def my_tool(self, param: str) -> dict:
        """One-line description shown to Orchestrator's classifier."""
        return await self.mcp.query_logs(f"SELECT ... WHERE service='my-service'")
```

3. Add to `docker-compose.yml` and register with the Orchestrator on boot.

Zero changes to proto, Orchestrator, or existing agents. The Knowledge Graph
picks up the new `Team --owns--> Repository` edges from the runbook/manifest
automatically.

---

## 17. Build Roadmap

### Phase 0 — Observability backbone (done)
- [x] Kafka KRaft + Spark streaming, Bronze/Silver Delta tables
- [x] MCP server with 5 tools (`query_logs`, `top_errors`, `search_keyword`,
      `pipeline_status`, `optimize_table`)
- [x] `proto/logs.proto` + `LogIngestionService`
- [x] Three SME runbooks (`kora-global`, `consumer-team`, `oss-kafka`)

### Phase 1 — Knowledge Graph + typed protocol (Weeks 1–2)
- [ ] `knowledge-graph/schema.sql` — entities + edges (§5)
- [ ] `knowledge-graph/ingest_github.py` — CODEOWNERS + PR history → ownership edges
- [ ] `proto/sme_agents.proto` — `Ticket`, `Finding`, `ImpactRequest/Response` (§6)
- [ ] `orchestrator/ownership_validator.py` — enforce §7 boundaries

### Phase 2 — First 3 SME Agents (Weeks 3–4)
- [ ] `agents/consumer_team_agent.py`, `kora_global_agent.py`, `oss_kafka_agent.py`
- [ ] Orchestrator consultation loop using the typed protocol
- [ ] Knowledge-graph-driven classifier (replace keyword-only routing)

### Phase 3 — Prove the Core Thesis (Week 5)
- [ ] Run the same 10–20 real tickets through: (a) this multi-agent system,
      (b) a single large-context-window agent with full repo access
- [ ] Compare: root cause accuracy, cross-team impact catches, time to answer
- [ ] **Decision point** — proceed to Phase 2 rollout (§12) only if the
      multi-agent system wins on impact-catch rate

### Phase 4 — Draft PRs (Phase 2 rollout, §12)
- [ ] Fix Planner + Code Agent + Test Agent
- [ ] Consumer-groups-per-topic example produces an actual draft PR

---

## 18. Design Decisions

### Why typed agent-to-agent protocol, not free-form chat?

Free-form LLM-to-LLM chat compounds interpretation errors with every hop —
by the third agent in a chain, claims have drifted from evidence into
paraphrase. A typed `ImpactRequest`/`ImpactResponse` (§6) forces every
agent to commit to structured, falsifiable claims (`affected_components`,
`confidence`, `open_questions`) that the Orchestrator can validate against
the Knowledge Graph before trusting them.

### Why a Postgres knowledge graph, not a vector database?

"Who owns this," "what does this block," "what must approve this change"
are graph traversal questions, not similarity-search questions. A vector
DB answers "what text looks similar to this text" — it cannot answer "what
is the shortest dependency chain between switchover and EOS transactions."
Postgres with an entities/edges schema is boring, cheap, and answers the
actual questions this product needs to answer. Move to a dedicated graph
DB only if query complexity outgrows SQL joins.

### Why explicit ownership boundaries, not "all agents know everything"?

Without boundaries, every agent is tempted to guess about code it doesn't
own — which is exactly the failure mode of a single big-context-window
agent (confidently wrong about unfamiliar code). Enforcing that an agent
must issue a typed request to the owning agent, rather than answer from a
guess, is what makes the network's aggregate answer more reliable than
any one agent's guess — but only if the boundary is enforced by the
Orchestrator, not left as a prompt suggestion.

### Why gRPC between agents, MCP for tools?

Agents need typed contracts and streaming (consultation chains can be
3–4 hops deep) — gRPC handles both. Tool calls are request-response and
already built on MCP (HTTP-native, discoverable via the manifest endpoint).
These are different layers and should not be conflated.

### Why is the log pipeline a peripheral input, not the center?

Earlier versions of this project treated Kafka/Spark/Delta as the core
architecture. It is useful — `trace_id` co-occurrence is one legitimate
source of `depends_on` edges — but it is one ingestion pipeline among
several (GitHub, Jira, Slack, static analysis). The Knowledge Graph (§5)
and the typed agent protocol (§6) are the actual product; log ingestion is
plumbing that feeds evidence into it.

### Why not start with autonomous code changes?

Because the fundamental hypothesis (§3) — that specialized agents with
ownership boundaries and typed communication beat one strong generalist
agent — has not been proven yet. Building auto-merge PR generation before
proving that hypothesis risks building an elaborate architecture that a
simpler system would have matched. Phase 1 exists specifically to falsify
or confirm this before further investment.
