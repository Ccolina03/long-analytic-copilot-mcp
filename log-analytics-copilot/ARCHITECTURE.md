# SME Agent Network — Architecture

> **One-line pitch:** Give us a bug ticket, and a network of specialized AI
> engineers — each owning a domain, each armed with the right tools — will
> figure out who needs to be involved, why, what the dependencies are, and
> what needs to change. No human plays coordinator.

---

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [Vision](#2-vision)
3. [How It Works — End to End](#3-how-it-works--end-to-end)
4. [Layer-by-Layer Architecture](#4-layer-by-layer-architecture)
   - 4.1 [Input Layer — API Gateway](#41-input-layer--api-gateway)
   - 4.2 [Orchestrator Agent](#42-orchestrator-agent)
   - 4.3 [SME Agents](#43-sme-agents)
   - 4.4 [Tool Layer — MCP Server](#44-tool-layer--mcp-server)
   - 4.5 [Observability Layer — Data Pipeline](#45-observability-layer--data-pipeline)
5. [Communication Protocols](#5-communication-protocols)
6. [Generic Proto Design](#6-generic-proto-design)
7. [Worked Example — Consumer Groups Per Topic](#7-worked-example--consumer-groups-per-topic)
8. [Repository Layout](#8-repository-layout)
9. [Data Schema](#9-data-schema)
10. [Adding a New SME Agent](#10-adding-a-new-sme-agent)
11. [Docker Compose Services](#11-docker-compose-services)
12. [Build Roadmap](#12-build-roadmap)
13. [Design Decisions](#13-design-decisions)

---

## 1. Problem Statement

When a bug is opened, an engineer has to:

- Figure out **which team owns** the affected code
- Understand **unfamiliar codepaths** across service boundaries
- Find the **right SME** for each domain
- Determine **what other systems** could be affected by a fix
- Coordinate **approvals** from multiple team leads
- Keep all of this in their head while also debugging

This process takes hours to days. Most of the delay is **coordination overhead**,
not actual engineering work.

The root cause: every engineering organization has a dependency graph — which
services call which, which teams own what, which changes require whose approval.
**That graph lives in people's heads, not in any system.**

---

## 2. Vision

Build an **AI engineering organization** — a network of specialized SME
(Subject Matter Expert) agents where:

- **Each agent owns a domain**: its code, architecture, past incidents, historical
  fixes, and approval authority
- **Each agent has its own tools**: grounded in real production data from that domain
- **Agents consult each other**: when a ticket crosses domain boundaries, agents
  share findings, surface dependencies, and negotiate approvals — autonomously
- **The network produces a live dependency graph**: built from actual call traces,
  log correlations, and cross-service evidence — not documentation

The first product is simple:

> **Give us a bug. We'll figure out who needs to be involved, why, and what
> needs to change.**

---

## 3. How It Works — End to End

```
1.  A ticket lands (GitHub issue, Jira, Slack alert)
         │
         ▼
2.  API Gateway authenticates and forwards to Orchestrator (gRPC)
         │
         ▼
3.  Orchestrator classifies which domains are touched
    (using ticket text + keyword → service mapping from log history)
         │
         ▼
4.  Orchestrator calls Investigate() on each relevant SME Agent in parallel
         │
         ├── SME Agent A: runs its domain tools, produces a Finding
         ├── SME Agent B: runs its domain tools, produces a Finding
         └── SME Agent C: runs its domain tools, produces a Finding
         │
         ▼
5.  Orchestrator identifies cross-domain dependencies from findings
    ("Agent B says it's blocked by Agent A's approval")
         │
         ▼
6.  Orchestrator calls ConsultAbout() — agents respond to each other's findings
    Agent A reads Agent B's finding, updates its own with new context
         │
         ▼
7.  Orchestrator builds the final TriageResult:
    - Ordered execution plan
    - Who owns what
    - What approvals are required
    - What evidence was found in logs/traces
    - PR outline
    - Escalation to human only if a decision genuinely requires one
```

---

## 4. Layer-by-Layer Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  INPUTS                                                              │
│  GitHub Issues · Jira Webhooks · Slack Alerts · Manual HTTP POST    │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ HTTPS
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  API GATEWAY  (FastAPI)                                              │
│  • Single external entry point                                       │
│  • Authentication + rate limiting                                    │
│  • Normalizes ticket format (Jira/GitHub/raw → Ticket proto)        │
│  • Translates HTTP → gRPC for internal routing                      │
│  • Streams findings back to caller as they arrive                    │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ gRPC: TriageTicket(Ticket)
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  ORCHESTRATOR AGENT                                                  │
│  • Agent registry (SME agents self-register on boot)                │
│  • Domain classifier (LLM + keyword → which agents to call)        │
│  • Runs Investigate() on all relevant agents in parallel            │
│  • Reads findings, maps blockers/dependencies                       │
│  • Runs ConsultAbout() in dependency order                          │
│  • Builds TriageResult (execution order, approvals, PR outline)     │
│  • Decides: escalate to human OR open PR autonomously               │
└──────┬────────────┬────────────┬────────────┬────────────┬──────────┘
       │ gRPC       │ gRPC       │ gRPC       │ gRPC       │ gRPC
       ▼            ▼            ▼            ▼            ▼
┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
│  SME     │ │  SME     │ │  SME     │ │  SME     │ │  SME     │
│  Agent   │ │  Agent   │ │  Agent   │ │  Agent   │ │  Agent   │
│  Kora    │ │ Consumer │ │  OSS     │ │  Broker  │ │ Billing  │
│  Global  │ │  Team    │ │  Kafka   │ │  Team    │ │  Team    │
│          │ │          │ │          │ │          │ │          │
│ [tools]  │ │ [tools]  │ │ [tools]  │ │ [tools]  │ │ [tools]  │
└────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘
     │            │            │             │            │
     └────────────┴────────────┴─────────────┴────────────┘
                               │ MCP tool calls
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  MCP TOOL SERVER  (FastAPI)                                          │
│  query_logs · top_errors · search_keyword · pipeline_status         │
│  blast_radius · dependency_graph · optimize_table                    │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ Spark SQL / Delta reads
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  DATA LAYER                                                          │
│                                                                      │
│  Kafka (topic: logs.raw)          ← live log stream                 │
│  Delta Bronze  (bronze_logs)      ← raw, append-only                │
│  Delta Silver  (silver_logs)      ← parsed, deduped, by service     │
│  Delta Gold    (gold_error_counts)← per-minute error rollups        │
│  Delta Tokens  (log_tokens)       ← keyword → event_id index        │
│  Delta Graph   (service_graph)    ← trace_id → service edges        │
└─────────────────────────────────────────────────────────────────────┘
```

---

### 4.1 Input Layer — API Gateway

**Technology:** FastAPI  
**Port:** 8080 (external)  
**Responsibility:** Single HTTPS entry point for the outside world.

```
POST /tickets          ← GitHub webhook, Jira webhook, manual
GET  /tickets/{id}     ← poll triage status
GET  /agents           ← which SME agents are registered
GET  /healthz
```

Normalizes any ticket format into the generic `Ticket` proto and forwards
to the Orchestrator via gRPC. Streams `Finding` objects back to the caller
as each SME agent responds — so the caller sees partial results immediately.

---

### 4.2 Orchestrator Agent

**Technology:** Python + gRPC server  
**Port:** 50050 (internal)  
**Responsibility:** Coordination, not domain knowledge.

The Orchestrator knows nothing about Kafka, consumer groups, or billing.
It knows how to:

1. **Classify** — use an LLM to map ticket text to domain keywords, then
   look up which registered agents own those domains
2. **Parallelize** — call `Investigate()` on all relevant agents simultaneously
3. **Sequence** — read `blockers` and `needs_from` fields in each `Finding`
   to build a dependency-ordered consultation plan
4. **Aggregate** — merge all findings into a single `TriageResult`
5. **Decide** — if `needs_human` is false across all findings, autonomously
   open a PR using the GitHub API; otherwise escalate with full context

The Orchestrator maintains an **agent registry** — a live map of
`agent_name → grpc_addr + tools`. Agents self-register on boot via
`RegisterAgent()`. No hardcoded routing.

---

### 4.3 SME Agents

**Technology:** Python + gRPC server (one container per agent)  
**Port:** 50051+ (internal, assigned per agent)  
**Responsibility:** Deep domain knowledge + tool execution.

Every SME agent:

- Inherits from `SMEAgentBase`
- Declares its domain in `DOMAIN` (plain English, used by classifier)
- Adds tools via the `@tool` decorator — each tool is a Python async function
- Implements the same 3 gRPC RPCs: `GetTools`, `Investigate`, `ConsultAbout`

Tools are the differentiator. Each agent's tools are grounded in its domain's
real production data. The MCP server provides shared log-level tools; each
agent adds domain-specific tools on top.

**Current SME Agents:**

| Agent | Domain | Key Tools |
|---|---|---|
| `kora-global` | Cluster Linking, failover, offset clamping, topic mirroring | `failover_latency`, `cluster_links`, `offset_clamp_trace`, `mirror_lag` |
| `consumer-team` | Consumer groups, GroupCoordinator, offsets, rebalancing | `group_state`, `offset_index`, `rebalance_history`, `lag_analysis` |
| `oss-kafka` | Apache Kafka protocol, KIPs, API versioning, OSS contribution | `kip_search`, `api_key_registry`, `compat_matrix`, `kip_template` |
| `broker-team` | Topic lifecycle, partition management, log segments | `topic_config`, `partition_health`, `ownership_check`, `schema_impact` |
| `billing-team` | Usage metering, cost impact, meter definitions | `meter_catalog`, `cost_impact`, `billing_events`, `usage_patterns` |

Adding a new agent = new Python file + new docker-compose service.
Zero changes to proto, Orchestrator, or existing agents.

---

### 4.4 Tool Layer — MCP Server

**Technology:** FastAPI  
**Port:** 8000 (internal)  
**Responsibility:** Shared observability tools backed by Delta tables.

All SME agents call these tools via HTTP. The MCP server abstracts Spark SQL
queries so agents don't need a Spark session.

| Tool | What it does |
|---|---|
| `query_logs` | Read-only SQL SELECT against allowed Delta tables |
| `top_errors` | Most frequent ERROR messages for a service in N minutes |
| `search_keyword` | Token-index search → joined back to `silver_logs` |
| `pipeline_status` | Health snapshot of Kafka + Delta pipeline |
| `optimize_table` | Compact a Delta table (small-file fix) |
| `blast_radius` | Given a service, find all downstream services via `trace_id` joins |
| `dependency_graph` | Return the live service call graph built from `trace_id` edges |

The `blast_radius` and `dependency_graph` tools are new — built on a Spark
aggregation job that mines `trace_id` cross-service co-occurrences from
`silver_logs` and writes edges to `delta/service_graph`.

---

### 4.5 Observability Layer — Data Pipeline

**Technology:** Kafka + Spark Structured Streaming + Delta Lake  
**Responsibility:** Continuously ingest logs and materialize queryable tables.

```
loadgen (Go) ──► ingest-gateway (Go gRPC) ──► Kafka: logs.raw
                                                     │
                                      Spark Streaming ▼
                                               Bronze Delta
                                               (raw, append)
                                                     │
                                      Spark Streaming ▼
                                               Silver Delta
                                               (parsed, deduped,
                                                partitioned by
                                                service/date/hour)
                                                     │
                               Spark Batch (scheduled) ▼
                            ┌──────────────────────────────┐
                            │  Gold: gold_error_counts      │
                            │  Tokens: log_tokens           │
                            │  Graph: service_graph         │
                            └──────────────────────────────┘
```

The `service_graph` table is new and critical — it powers the dependency graph:

```sql
-- Built by spark/build_service_graph.py
SELECT   a.service AS from_service,
         b.service AS to_service,
         COUNT(*)  AS co_occurrences
FROM     silver_logs a
JOIN     silver_logs b ON a.trace_id = b.trace_id
                       AND a.service <> b.service
GROUP BY a.service, b.service
```

This turns `trace_id` co-occurrence into a directed call graph — every edge
represents a real request that crossed a service boundary.

---

## 5. Communication Protocols

| Between | Protocol | Why |
|---|---|---|
| External world → API Gateway | HTTPS / REST | Standard, works with webhooks |
| API Gateway → Orchestrator | gRPC | Low latency, streaming, typed |
| Orchestrator → SME Agents | gRPC | Same — agents call each other too |
| SME Agents → MCP Server | HTTP | MCP is HTTP-native; simple enough |
| MCP Server → Delta Tables | Spark SQL (PySpark) | Delta is Spark-native |
| Log producers → Kafka | gRPC (LogIngestionService) | Batching + backpressure built-in |
| Kafka → Delta | Spark Structured Streaming | Exactly-once, checkpoint recovery |

**Key principle:** gRPC for agent-to-agent (typed, streaming, fast).
MCP for agent-to-tools (HTTP, discoverable, swappable). These are different
layers and should not be conflated.

---

## 6. Generic Proto Design

One proto file covers all agents. Tools are declared dynamically — agents
register their own tools at boot. No per-agent proto.

```proto
// proto/sme_agents.proto

syntax = "proto3";
package sme.v1;

message Tool {
  string name        = 1;
  string description = 2;
  string agent       = 3;
  map<string, string> params = 4;
}

message ToolResult {
  string tool_name = 1;
  string agent     = 2;
  string result    = 3;  // JSON — each tool owns its shape
  bool   success   = 4;
  string error     = 5;
}

message Ticket {
  string id              = 1;
  string title           = 2;
  string description     = 3;
  string source          = 4;  // "jira" | "github" | "slack"
  repeated string labels = 5;
}

message Finding {
  string   agent                   = 1;
  string   role                    = 2;  // "proposer"|"code-owner"|"upstream-gate"
  string   summary                 = 3;
  repeated string evidence         = 4;  // trace_ids, event_ids, perf numbers
  repeated string blockers         = 5;  // agent names this agent blocks
  repeated string needs_from       = 6;  // agent names this agent needs input from
  repeated ToolResult tool_results = 7;
  bool     needs_human             = 8;
  string   escalation_reason       = 9;
  float    confidence              = 10;
}

message ConsultRequest {
  Ticket  ticket         = 1;
  Finding requesting_from = 2;
}

message TriageResult {
  string            ticket_id        = 1;
  repeated Finding  findings         = 2;
  repeated string   execution_order  = 3;
  string            pr_outline       = 4;
  bool              requires_human   = 5;
  string            escalation_reason = 6;
  repeated string   approvals_needed = 7;
}

// Every SME agent implements these 3 RPCs — no exceptions
service SMEAgent {
  rpc GetTools       (Empty)          returns (ToolList);
  rpc Investigate    (Ticket)         returns (Finding);
  rpc ConsultAbout   (ConsultRequest) returns (Finding);
}

service Orchestrator {
  rpc TriageTicket   (Ticket)         returns (stream Finding);
  rpc RegisterAgent  (AgentManifest)  returns (Empty);
}

message AgentManifest {
  string          agent_name = 1;
  string          domain     = 2;
  string          grpc_addr  = 3;
  repeated Tool   tools      = 4;
}

message ToolList { repeated Tool tools = 1; }
message Empty    {}
```

---

## 7. Worked Example — Consumer Groups Per Topic

**Ticket:**
> "Cluster Linking offset clamping during failover is too slow. Currently
> calls `ListGroups()` on entire cluster then filters. Need topic-partition
> scoped group lookup. Affects: Kora Global → Consumer Team → OSS Kafka."

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

**SME Agent Consultation Flow:**

```
Orchestrator classifies:
  → kora-global (proposer, has perf evidence)
  → consumer-team (code owner, GroupCoordinator)
  → oss-kafka (upstream gate, protocol + KIP)

── ROUND 1: Investigate() in parallel ──────────────────────────────

kora-global.Investigate(ticket):
  tool: failover_latency()     → "clampOffsets p99=8200ms, target=50ms"
  tool: offset_clamp_trace()   → "bottleneck is ListGroups RPC, 50k groups"
  Finding:
    summary: "Need ListGroupsForTopicPartition API"
    evidence: [trace_id_a, trace_id_b, latency histogram]
    needs_from: ["consumer-team", "oss-kafka"]
    confidence: 0.95

consumer-team.Investigate(ticket):
  tool: offset_index()         → "groups keyed by group_id only, no tp index"
  tool: group_state()          → "50,312 active groups in cluster"
  Finding:
    summary: "New inverted index needed in GroupCoordinator.
              Option A: in-memory reverse map (group_id, topic, partition).
              Option B: new scan with early exit.
              Recommend Option A. Needs new Kafka API key."
    blockers: ["oss-kafka"]    ← blocks OSS for KIP
    needs_from: ["kora-global"] ← need scope confirmation

oss-kafka.Investigate(ticket):
  tool: kip_search("ListGroups topic partition filter")
       → "KIP-518 adds state/type filter — NOT topic-partition. No existing KIP."
  tool: api_key_registry("ListGroups")
       → "API key 16, current version v4"
  Finding:
    summary: "Requires new KIP. Extend ListGroups request v5 with
              list_groups_for_topic_partitions filter.
              Old clients (v4): full list (backward compat preserved).
              Timeline: ~6 weeks to Apache Kafka trunk."
    needs_from: ["consumer-team"]  ← needs KIP draft from code owner

── ROUND 2: ConsultAbout() in dependency order ─────────────────────

consumer-team.ConsultAbout(kora-global finding):
  Finding updated:
    "Confirmed scope: Apache Kafka (not Kora-only).
     Consumer Team will draft KIP.
     Option A approved — memory overhead acceptable in cloud."

oss-kafka.ConsultAbout(consumer-team finding):
  Finding updated:
    "KIP accepted for drafting. Sponsor: [oss-kafka committer].
     Precedent: KIP-518. Draft owner: Consumer Team."

── FINAL: Orchestrator builds TriageResult ─────────────────────────

execution_order:
  1. kora-global confirms scope             [DONE — confirmed OSS]
  2. consumer-team drafts KIP               [owner: consumer-team]
  3. oss-kafka community vote               [~4 weeks]
  4. consumer-team implements inverted index [after vote]
  5. kora-global updates clampOffsets()     [after implementation]
  6. billing-team adds meter if new API     [parallel to step 5]

approvals_needed:
  - consumer-team lead    (Option A memory trade-off)
  - oss-kafka committer   (KIP sponsorship)

pr_outline:
  PR 1: GroupCoordinator in-memory reverse index (consumer-team)
  PR 2: ListGroups v5 request schema (consumer-team)
  PR 3: ClusterLinking.clampOffsets() updated call (kora-global)

requires_human: true
escalation_reason: "Two team lead approvals required before implementation"
```

---

## 8. Repository Layout

```
log-analytics-copilot/
│
├── proto/
│   ├── logs.proto              # LogEvent + LogIngestionService (existing)
│   └── sme_agents.proto        # Generic SME agent + Orchestrator RPCs
│
├── gateway/
│   ├── main.py                 # FastAPI — external entry point
│   └── Dockerfile
│
├── orchestrator/
│   ├── main.py                 # gRPC server + agent registry
│   ├── classifier.py           # LLM domain classifier
│   ├── consultation.py         # Investigate → ConsultAbout loop
│   └── Dockerfile
│
├── agents/
│   ├── base_agent.py           # SMEAgentBase + @tool decorator
│   ├── kora_global_agent.py    # Kora Global SME Agent
│   ├── consumer_team_agent.py  # Consumer Team SME Agent
│   ├── oss_kafka_agent.py      # OSS Kafka SME Agent
│   ├── broker_team_agent.py    # Broker Team SME Agent (future)
│   ├── billing_team_agent.py   # Billing Team SME Agent (future)
│   └── Dockerfile              # shared — all agents use same image
│
├── mcp-server/
│   ├── main.py                 # FastAPI MCP tool server (existing)
│   ├── tools.py                # Tool implementations (existing)
│   └── requirements.txt
│
├── spark/
│   ├── kafka_to_delta.py       # Kafka → Bronze → Silver streaming (existing)
│   ├── build_gold.py           # Silver → Gold error counts
│   ├── build_token_index.py    # Silver → log_tokens keyword index
│   └── build_service_graph.py  # Silver → service_graph (trace_id edges)  ← NEW
│
├── ingest-gateway/             # Go gRPC server + Kafka producer (Week 1)
├── loadgen/                    # Go load generator (Week 1)
├── scripts/
│   ├── kafka-smoke.sh
│   ├── spark-smoke.sh
│   ├── spark-submit.sh
│   ├── produce-fake-logs.py
│   └── _count_delta.py
│
├── checkpoints/                # Spark streaming checkpoints (gitignored)
├── delta/                      # Delta table data (gitignored)
├── diagrams/                   # Architecture diagrams
├── docker-compose.yml
├── README.md
└── ARCHITECTURE.md             ← this file
```

---

## 9. Data Schema

### LogEvent (proto/logs.proto)

| Field | Type | Notes |
|---|---|---|
| `timestamp` | string | ISO-8601 UTC |
| `service` | string | Partition key in Silver table |
| `level` | string | DEBUG / INFO / WARN / ERROR |
| `message` | string | Free-form, tokenized for keyword index |
| `trace_id` | string | Groups events across services — **the dependency graph key** |
| `event_id` | string | Unique per log line, used for dedup |
| `host` | string | Originating host |

### Delta Tables

| Table | Layer | Partition | Description |
|---|---|---|---|
| `bronze_logs` | Bronze | `ingest_date` | Raw JSON from Kafka, append-only |
| `silver_logs` | Silver | `service / date / hour` | Parsed, deduped on `(trace_id, event_id)` |
| `gold_error_counts` | Gold | `service / minute` | Per-minute error counts + top messages |
| `log_tokens` | Gold | `service` | Token → event_id inverted index for keyword search |
| `service_graph` | Gold | — | `(from_service, to_service, co_occurrences)` — live dependency graph |

### Finding (proto/sme_agents.proto)

Every SME agent returns this same shape. Fields are additive — agents only
fill what they know.

---

## 10. Adding a New SME Agent

1. Create `agents/my_team_agent.py`:

```python
from agents.base_agent import SMEAgentBase, tool

class MyTeamAgent(SMEAgentBase):
    AGENT_NAME = "my-team"
    DOMAIN     = "plain English description of what this team owns"

    @tool("my_tool")
    async def my_tool(self, param: str) -> dict:
        """One-line description shown to Orchestrator's classifier."""
        result = await self.mcp.query_logs(f"SELECT ... WHERE service='my-service'")
        return result

    @tool("another_tool")
    async def another_tool(self, topic: str, partition: int) -> dict:
        """Another domain-specific tool."""
        ...
```

2. Add to `docker-compose.yml`:

```yaml
my-team-agent:
  build: ./agents
  command: python -m agents.my_team_agent
  environment:
    - ORCHESTRATOR_ADDR=orchestrator:50050
    - MCP_ADDR=mcp-server:8000
```

That's it. The agent self-registers with the Orchestrator on boot. No changes
to proto, Orchestrator, or any other agent.

---

## 11. Docker Compose Services

| Service | Image | Port | Role |
|---|---|---|---|
| `kafka` | `bitnamilegacy/kafka:3.7` | 9092 | Log stream broker (KRaft mode) |
| `spark-master` | `bitnamilegacy/spark:3.5` | 8080, 7077 | Spark cluster master |
| `spark-worker` | `bitnamilegacy/spark:3.5` | 8081 | Spark worker |
| `mcp-server` | `python:3.12-slim` | 8000 | MCP tool server |
| `gateway` | `python:3.12-slim` | 8080 | API Gateway (external entry) |
| `orchestrator` | `python:3.12-slim` | 50050 | Orchestrator gRPC server |
| `kora-global-agent` | `python:3.12-slim` | 50051 | Kora Global SME Agent |
| `consumer-team-agent` | `python:3.12-slim` | 50052 | Consumer Team SME Agent |
| `oss-kafka-agent` | `python:3.12-slim` | 50053 | OSS Kafka SME Agent |
| `broker-team-agent` | `python:3.12-slim` | 50054 | Broker Team SME Agent |
| `billing-team-agent` | `python:3.12-slim` | 50055 | Billing Team SME Agent |

---

## 12. Build Roadmap

### Phase 0 — Observability backbone (done)
- [x] Kafka KRaft + Spark streaming
- [x] Bronze + Silver Delta tables (real data written May 2026)
- [x] MCP server with 5 tools (`query_logs`, `top_errors`, `search_keyword`,
      `pipeline_status`, `optimize_table`)
- [x] `proto/logs.proto` + `LogIngestionService`

### Phase 1 — SME Agent skeleton (Week 1)
- [ ] `proto/sme_agents.proto` — generic types + RPCs
- [ ] `agents/base_agent.py` — `SMEAgentBase` + `@tool` decorator
- [ ] `orchestrator/main.py` — agent registry + `TriageTicket`
- [ ] `gateway/main.py` — HTTP → gRPC translation
- [ ] Wire `PySparkExecutor` replacing `StubSparkExecutor` in `tools.py`

### Phase 2 — First 3 SME Agents (Week 2)
- [ ] `agents/consumer_team_agent.py` — richest domain, 4 tools
- [ ] `agents/kora_global_agent.py` — 4 tools grounded in CL failover logs
- [ ] `agents/oss_kafka_agent.py` — KIP search + protocol tools
- [ ] Orchestrator consultation loop (Investigate → ConsultAbout)

### Phase 3 — Dependency graph + blast radius (Week 3)
- [ ] `spark/build_service_graph.py` — mine `trace_id` edges into `service_graph`
- [ ] MCP tools: `blast_radius` + `dependency_graph`
- [ ] Orchestrator uses graph to pre-classify ticket domains

### Phase 4 — Full demo (Week 4)
- [ ] End-to-end: POST a ticket → stream findings → `TriageResult`
- [ ] `broker-team-agent` + `billing-team-agent`
- [ ] Autonomous PR drafting (GitHub API) when `requires_human=false`
- [ ] Consumer groups per topic example works end-to-end

---

## 13. Design Decisions

### Why gRPC between agents, not HTTP?

Agents need streaming responses (findings arrive incrementally as tools run),
typed contracts (a `Finding` must have specific fields), and low latency
(consultation chains can be 3–4 hops deep). gRPC handles all three.
HTTP/JSON works for the external API gateway because webhooks expect it.

### Why MCP for tools, not gRPC?

MCP is HTTP-native and designed for LLM tool use — it's discoverable (the
manifest endpoint), simple to add tools to, and already integrated with the
log analytics backbone. Agent-to-agent communication is gRPC because it needs
typing and streaming. Tool calls are MCP because they're request-response and
the MCP server is already built.

### Why one generic proto, not one per agent?

If each agent had its own proto, adding an agent requires touching the proto
layer and potentially the Orchestrator. With a generic proto, `Finding` carries
everything any agent could return — the `tool_results` field is a list of
`ToolResult` where the result field is JSON (each tool owns its shape). New
agents add zero new proto types.

### Why is `trace_id` the dependency graph key?

`trace_id` is already in every `LogEvent`. When the same `trace_id` appears
in logs from `payments-service` AND `auth-service`, a real cross-service
request happened. Mining these co-occurrences gives a dependency graph that
is: (a) derived from actual traffic, not documentation; (b) always up to date
as long as logs flow; (c) weighted by volume (high co-occurrence = strong
dependency). No human writes or maintains it.

### Why not one big agent?

A single agent with all domain knowledge would: (a) have a bloated context
window, (b) produce lower-quality answers because it can't specialize,
(c) not model the real approval structure (Consumer Team lead != Billing Team
lead), and (d) not scale — adding a new domain shouldn't require retraining
or changing a monolith. The network of specialists mirrors the real org.
