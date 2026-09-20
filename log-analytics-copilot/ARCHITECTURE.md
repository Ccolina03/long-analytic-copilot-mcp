# SME Agent Network — Architecture

> **One-line pitch:** Give us a bug ticket, and a network of specialized AI
> engineers — each with persistent domain ownership, each armed with the
> right tools — will figure out who needs to be involved, why, what the
> dependencies are, and what needs to change. No human plays coordinator.

## How to Read This Document

This is written for an engineer who has never seen this project before.
It is organized in eight parts, in the order you should actually read them:

- **Part I** explains the problem and the core idea, at a high level, before
  any implementation detail. Read this first even if you're impatient to
  see code.
- **Part II** explains how *one* agent works in isolation.
- **Part III** explains how *multiple* agents work together — this is the
  actual product.
- **Part IV** shows the full system as one diagram, once you understand the
  pieces.
- **Part V** walks through one real example end to end, so the abstract
  ideas above become concrete.
- **Part VI** explains why this compounds into a defensible product over
  time, rather than being a clever demo that plateaus.
- **Part VII** is the practical build plan.
- **Part VIII** is a FAQ answering the "why not just do X instead" questions
  a skeptical engineer will ask.

One example is used consistently throughout the entire document: **three
Kafka/Confluent teams — Kora Global (Cluster Linking), Consumer Team
(GroupCoordinator), and OSS Kafka (protocol/KIPs) — coordinating on a
real performance ticket.** Concrete runbooks for these three agents live in
[`runbooks/`](./runbooks/) and are referenced throughout.

---

# Part I — The Problem and the Idea

## 1. The Problem This Solves

When a bug ticket is opened in a real engineering organization, a human
has to do a lot of work *before* they can even start fixing anything:

- Figure out **which team owns** the affected code
- Understand **unfamiliar codepaths** across service boundaries they don't
  work in day to day
- Find the **right subject-matter expert** for each domain the bug touches
- Determine **what other systems** could be affected by a fix
- Coordinate **approvals** from multiple team leads who each own a piece
  of the change
- Hold all of this in their head simultaneously while also trying to debug

This process routinely takes hours to days — and most of that time is
**coordination overhead**, not actual engineering work. The engineer isn't
slow at writing code; they're slow at discovering who to talk to and what
those people know.

The underlying reason this is slow: every engineering organization has a
real dependency graph. Team A's Cluster Linking feature depends on Team B's
consumer group internals, which depend on Team C's protocol version
support. **That graph is real, but it lives in people's heads and Slack
history — not in any queryable system.** Nobody wrote it down because it
emerged gradually, one incident and one PR at a time, and no single person
holds the whole thing.

## 2. Why This Isn't Already Solved

Multi-agent orchestration is already a crowded space. Before building
anything, it's worth being precise about what already exists and where the
actual gap is — otherwise this is "another AI wrapper" with no real
differentiation.

| Company | Core abstraction | What they actually provide |
|---|---|---|
| **Superset** | Many coding agents → parallel execution | Runs hundreds of generic coding agents in parallel on independent tasks |
| **Agent Relay** | Shared infrastructure | Messaging, session history, and tool plumbing (GitHub/Linear/Slack) between agents |
| **Glen** | Organizational memory | Aggregates agent sessions, Slack, PRs, tickets, and docs for retrieval by future agents |
| **Linzumi** | Human → many agents | Team chat interface that directs dozens of coding agents; turns org decisions into a source of truth |

Every one of these answers **"how do agents run, talk, or remember."**
None of them answer **"who should be involved in this specific change, why,
and what's the actual technical impact — grounded in who really owns what
code."** That's the gap.

```
Superset:      many agents  →  parallel execution
Agent Relay:   many agents  →  shared plumbing (messaging/tools/history)
Glen:          many agents  →  shared memory (retrieval)
Linzumi:       human        →  many agents (command & control)

THIS PROJECT:  ticket → SME agents → SME agents (typed consultation)
                      → coordinated action → human approval only when needed
```

Two of these deserve a direct comment:

- **Agent Relay** is an infrastructure layer this product could plausibly
  run *on top of* someday (shared messaging, tool plumbing) — it is not a
  reason not to build this, because it solves a different problem.
- **Glen** is the closest adjacent risk, because organizational memory
  sounds similar to a knowledge graph. The distinction: memory is
  *retrieval* — "find me things that look related to this." This system is
  *reasoning + ownership + action* — "here is exactly who owns this, why
  they're affected, and what evidence proves it." Retrieval degrades
  gracefully into noise as an org scales; a structured ownership graph gets
  more precise as it accumulates more resolved tickets (see Part VI).

## 3. The Core Idea in One Picture

Before any implementation detail, here is the entire idea in one flow:

```
 A ticket describing a real engineering problem arrives
                         │
                         ▼
 An Orchestrator figures out which teams' domains the ticket touches —
 not by keyword guessing, but by looking up a real graph of who owns
 what code and what depends on what
                         │
                         ▼
 Each relevant team's SME Agent investigates using its OWN tools,
 grounded in its OWN domain's real production data — it does not
 guess about code it doesn't own
                         │
                         ▼
 When one agent's investigation touches another team's domain, it sends
 that team's agent a structured, typed question — not a free-text message —
 and gets back a structured, typed answer with a confidence score and an
 explicit list of anything it couldn't determine
                         │
                         ▼
 The Orchestrator assembles all of this into one report: root cause,
 who needs to approve what, in what order, and what's still uncertain
                         │
                         ▼
 A human is looped in ONLY when the system itself says a decision
 genuinely requires human judgment — not for every step
```

Everything in Parts II–IV is the mechanics of making each of these five
steps real and reliable, rather than "an LLM winging it."

## 4. The Hypothesis We're Testing

Before scaling any of this into a real platform, there's one question that
has to be answered honestly:

> **Does a network of specialized agents — each with persistent domain
> ownership, communicating through a typed protocol with explicit
> boundaries — actually produce better answers than one very capable coding
> agent given the entire codebase and a huge context window?**

If the answer is yes, everything described in this document is justified.
If the answer is no, this entire architecture is unnecessary complexity and
a single strong agent is simply the better product.

This is why the build plan (§13, Part VII) deliberately starts with an
**investigation-only** phase — no autonomous code changes — and explicitly
measures this system against a single-agent baseline before building
anything further. The architecture is a hypothesis until that comparison
is run.

---

# Part II — How a Single Agent Works

## 5. The Three-Layer Agent Model

A "SME Agent" in this system is not just an LLM with a clever system
prompt. Every agent is built from three distinct layers of context, and
keeping them separate is what makes the agent behave like a real domain
expert instead of a chatbot that happens to know some Kafka trivia.

```
                    ┌─────────────────────┐
                    │   Team SME Agent     │
                    │  e.g. "Kora Global   │
                    │  (Cluster Linking)"  │
                    └──────────┬──────────┘
                               │
             ┌─────────────────┼─────────────────┐
             ▼                 ▼                 ▼
       DOMAIN MEMORY      LIVE CONTEXT       CAPABILITIES
     (persistent, built    (specific to      (what this agent is
      up over months)       THIS ticket)      actually allowed to
                                                touch or call)
             │                 │                 │
       ┌─────┴─────┐      ┌────┴─────┐      ┌────┴─────┐
       │ Owned code│      │ Ticket   │      │ GitHub   │
       │ Design    │      │ text     │      │ Jira     │
       │ decisions │      │ Recent   │      │ Slack    │
       │ Past PRs  │      │ incidents│      │ CI/CD    │
       │ Invariants│      │ Related  │      │ MCP tools│
       │ Known bugs│      │ traces   │      │ (scoped  │
       │ Tests     │      │          │      │  to its  │
       │           │      │          │      │  domain) │
       └───────────┘      └──────────┘      └──────────┘
```

**Domain Memory** is what makes the agent an SME rather than a generic
assistant, and it is the slowest-changing layer — it should look roughly
the same today as it did last month, updated only as the team's real
knowledge grows. For the Kora Global agent, this is everything in
[`runbooks/kora-global-sme.md`](./runbooks/kora-global-sme.md): which
repositories it owns, exactly which files and line ranges within them,
known performance invariants, and the history of past incidents in Cluster
Linking. This is deliberately **not** a single vector database with
everything dumped in and retrieved by similarity search. "Who owns this
file" and "what depends on this service" are graph questions — see §7 for
why that distinction matters and how it's actually stored.

**Live Context** is the opposite: it is specific to the one ticket being
worked right now, and it disappears once the ticket is resolved (though
the *outcome* of resolving it gets written back into Domain Memory and the
Knowledge Graph — see Part VI). This includes the raw ticket text, any
recent related incidents, in-flight changes to the same codepaths, and
whatever trace or log evidence gets pulled while investigating.

**Capabilities** are the explicit, scoped set of things the agent is
actually allowed to do: which MCP tools it can call, whether it can read or
write GitHub PRs, whether it can post to Slack, whether it can trigger a
CI run. Capabilities are scoped per agent on purpose — the Billing agent
cannot open a pull request against the Broker team's repository, even if
an LLM inside it decided that seemed like a good idea. This is an
authorization boundary, not a suggestion in a prompt.

## 6. Ownership Boundaries

Every agent has an explicit, non-overlapping ownership boundary. This is
the detail that turns "five LLMs with slightly different system prompts"
into something that actually mirrors a real engineering organization, and
it's enforced by code, not just by asking the model nicely.

Here are the three agents that exist today, with their real ownership
boundaries (the full detail, including exact file paths and line numbers,
is in each agent's runbook):

```
kora-global          (Cluster Linking — the "proposer" in our example)
  owns:
    confluent/kora-cluster-linking/src/main/java/io/confluent/clusterlink/
    → see runbooks/kora-global-sme.md for exact files and line ranges

consumer-team        (GroupCoordinator — the "code owner" in our example)
  owns:
    apache/kafka: core/src/main/scala/kafka/coordinator/group/
    confluent/kora-group-coordinator/
    → see runbooks/consumer-team-sme.md for exact files and line ranges

oss-kafka            (Protocol + KIP process — the "upstream gate")
  owns:
    apache/kafka: clients/src/main/resources/common/message/*.json
    the Apache Kafka KIP process itself
    → see runbooks/oss-kafka-sme.md for exact files and precedent KIPs
```

**The rule that makes this matter:** when an agent's investigation touches
a codepath it does not own, it is not allowed to guess about it — even if
the underlying LLM is perfectly capable of guessing correctly most of the
time. Instead, it must send a structured request to the agent that *does*
own that codepath (this is the typed protocol in §8). The Orchestrator
enforces this by checking, after the fact, that any codepath an agent cites
in its findings actually falls within that agent's declared ownership. If
it doesn't, the finding gets flagged as unverified rather than trusted as
fact.

This is deliberately the opposite failure mode of a single giant-context
agent, which will confidently reason about code it has never actually
touched in production, simply because it fits in the context window.

---

# Part III — How Agents Work Together

Part II described one agent in isolation. The actual product only exists
once multiple agents can reliably (a) know who else to talk to, and
(b) exchange information without the message degrading into vague prose
after a couple of hops. That's what this part covers.

## 7. The Engineering Knowledge Graph

When the Orchestrator receives a ticket, its first job is to figure out
which agents even need to be involved. It does this by querying a real,
structured graph of the organization's engineering reality — not by
guessing from keywords in the ticket text, and not by semantic similarity
search over a pile of documents.

The reason this needs to be a graph and not a vector database: the
questions that actually matter are graph-traversal questions, not
similarity questions. "Who owns this file," "what does merging this PR
block," and "which team must approve this class of change" cannot be
answered by finding text that looks similar — they can only be answered by
walking real edges between real entities.

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

### How this is actually stored

The MVP storage choice is deliberately boring: **Postgres**, with the graph
represented as two tables — nodes (`entities`) and edges. A dedicated graph
database is not needed until query complexity genuinely outgrows SQL
joins, which is unlikely to happen before there's real product-market fit
to justify the added operational complexity.

```sql
CREATE TABLE entities (
  id          UUID PRIMARY KEY,
  type        TEXT NOT NULL,   -- 'service' | 'repository' | 'codepath' | 'decision' | 'team' | 'rule'
  name        TEXT NOT NULL,
  metadata    JSONB            -- repo path, line ranges, description, etc.
);

CREATE TABLE edges (
  id           UUID PRIMARY KEY,
  from_id      UUID REFERENCES entities(id),
  to_id        UUID REFERENCES entities(id),
  relation     TEXT NOT NULL,  -- 'owns' | 'depends_on' | 'calls' | 'protected_by' | 'must_approve' | ...
  evidence     JSONB,          -- trace_ids, PR links, ticket ids that established this edge
  confidence   FLOAT DEFAULT 1.0,
  created_at   TIMESTAMPTZ DEFAULT now(),
  last_seen_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_edges_from ON edges(from_id, relation);
CREATE INDEX idx_edges_to   ON edges(to_id, relation);
```

### Where the edges actually come from

Nobody manually types this graph in — it has to be mined from signals that
already exist in a real engineering organization:

| Source | Produces |
|---|---|
| CODEOWNERS files + git history | `Team --owns--> Repository`, `Team --owns--> CodePath` |
| PR descriptions and review comments | `Decision --applies_to--> CodePath`, `Decision --introduced_by--> PR` |
| Log traces (`trace_id` shared across services) | `Service --depends_on--> Service`, weighted by how often it happens |
| Static call-graph analysis | `CodePath --calls--> CodePath` |
| Outcomes of tickets this system itself resolves | `ChangeType --must_approve--> Team`, learned from who actually signed off |

That last row is important and is expanded on in Part VI: every ticket this
system resolves makes the graph slightly more accurate for the *next*
ticket. That compounding effect is the actual long-term moat, not any
single clever agent.

## 8. Agent-to-Agent Protocol

Once the Orchestrator knows which agents are relevant, those agents need
to actually exchange information with each other. The obvious approach —
just let them message each other in free text, the way a human Slack
conversation would look — has a specific, serious failure mode: **free-text
LLM-to-LLM communication compounds interpretation error with every hop.**
By the third agent in a chain, claims have quietly drifted from "here is
evidence" into "here is my paraphrase of someone else's paraphrase." Nobody
downstream can tell where confidence should have dropped.

So agents exchange a **typed, structured message** instead. Here is the
actual example from our running scenario: `kora-global` (Cluster Linking)
has found that its offset-clamping code is too slow, and needs to ask
`consumer-team` (who owns GroupCoordinator) whether a fix is even possible.

**Request, from `kora-global` to `consumer-team`:**

```json
{
  "from": "kora-global",
  "to": "consumer-team",
  "request_type": "impact_analysis",
  "change_description": "Need an indexed lookup: given a topic-partition, return only the consumer groups subscribed to it, instead of scanning every group in the cluster.",
  "questions": [
    "Can GroupCoordinator support an indexed reverse lookup by topic-partition?",
    "What would the memory cost be at Confluent Cloud scale?",
    "Does this require a new Kafka protocol version?"
  ],
  "ticket_id": "CL-4821"
}
```

**Response, from `consumer-team` back to `kora-global`:**

```json
{
  "impact_level": "MEDIUM",
  "affected_components": ["GroupCoordinator", "GroupMetadata"],
  "invariants": ["group state must stay consistent across a rebalance"],
  "codepaths": ["GroupCoordinator#handleListGroups"],
  "tests": ["GroupCoordinatorTest#testListGroupsFiltering"],
  "confidence": 0.9,
  "open_questions": ["needs a new Kafka API version — ask oss-kafka"]
}
```

Why this specific shape matters: the agent (or the Orchestrator) reasoning
about the answer sees **actual falsifiable claims** — a named list of
affected components, an explicit confidence number, and a field that says
exactly what this agent could *not* determine — instead of a paragraph of
prose it has to re-interpret. This is auditable (you can point at exactly
which claim came from where), diffable (you can compare two responses
mechanically), and it can be checked against the Knowledge Graph from §7
before anyone downstream trusts it.

`open_questions` deserves particular attention: an agent admitting "I don't
know this part" is treated as a **first-class, successful output**, not a
failure to hide. This is precisely the signal the Orchestrator uses to
decide when a ticket needs to escalate to a human (§9) instead of being
silently resolved with a confident-sounding guess.

### The formal schema

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
  string   impact_level               = 1;  // "LOW" | "MEDIUM" | "HIGH"
  repeated string affected_components = 2;
  repeated string invariants          = 3;
  repeated string codepaths           = 4;
  repeated string tests               = 5;
  float    confidence                 = 6;
  repeated string open_questions      = 7;
}
```

## 9. The Ticket as Orchestration Layer

Putting §7 and §8 together: the ticket itself is not something an agent
merely *answers* with a paragraph. It is the **unit of work** that drives
the entire pipeline from first read to final recommendation, accumulating
structured artifacts — findings, typed impact responses, evidence,
confidence scores — at every stage, instead of a wall of chat transcript
that a human has to re-read to understand what actually happened.

```
                    Jira / GitHub Ticket
                            │
                            ▼
                    ┌───────────────┐
                    │  Orchestrator │
                    └───────┬───────┘
                            │
              query the Knowledge Graph (§7) to find
              which agents own the affected entities
                            │
          ┌─────────────────┼─────────────────┐
          ▼                 ▼                 ▼
    kora-global agent  consumer-team agent  oss-kafka agent
          │                 │                 │
          │◄── ImpactRequest / ImpactResponse ─┤
          │     (typed protocol, §8, both ways) │
          └─────────────────┼─────────────────┘
                            ▼
                     Impact Analysis
                     (assembled from every
                      agent's structured findings)
                            │
                            ▼
                       Root Cause
                            │
                            ▼
                  Recommended Fix + Execution Order
                            │
                            ▼
                    Human approval gate
              (only triggered if the system itself
               says requires_human = true)
```

The reason this design produces an auditable final answer rather than a
black box: at the end, you can point to exactly which agent said what,
with what confidence, and what evidence backed it up — because every step
along the way was a typed message, not a summarized conversation.

---

# Part IV — The Full System

## 10. System Architecture, Layer by Layer

With Parts II and III explained, here is the complete system as one
diagram. Every box below corresponds to something already explained above.

```
┌─────────────────────────────────────────────────────────────────────┐
│  INPUTS                                                              │
│  GitHub Issues · Jira Webhooks · Slack Alerts · Manual HTTP POST    │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ HTTPS
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  API GATEWAY  (FastAPI)                                              │
│  • Single external entry point, handles auth + rate limiting         │
│  • Normalizes any ticket format (Jira/GitHub/raw) into one shape     │
│  • Translates the incoming HTTP request into an internal gRPC call,  │
│    then streams findings back to the caller as they arrive          │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ gRPC: TriageTicket(Ticket)
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  ORCHESTRATOR                                                        │
│  • Keeps a live registry of every SME agent that has registered      │
│  • Classifies which agents are relevant by querying the Knowledge    │
│    Graph (§7) — not by keyword-matching the ticket text              │
│  • Calls each relevant agent's Investigate() method in parallel      │
│  • Routes typed ImpactRequest / ImpactResponse messages between      │
│    agents as their investigations surface cross-team questions (§8)  │
│  • Validates that every codepath an agent cites falls inside that    │
│    agent's declared ownership boundary (§6) — flags it if not        │
│  • Assembles the final result and escalates to a human only if the   │
│    agents themselves signal that a decision requires one             │
└──────┬────────────┬────────────┬────────────┬────────────┬──────────┘
       │ gRPC       │ gRPC       │ gRPC       │ gRPC       │ gRPC
       ▼            ▼            ▼            ▼            ▼
┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
│   SME    │ │   SME    │ │   SME    │ │  SME     │ │  SME     │
│  Agent:  │ │  Agent:  │ │  Agent:  │ │ Agent:   │ │ Agent:   │
│  Kora    │ │ Consumer │ │   OSS    │ │ Broker   │ │ Billing  │
│  Global  │ │  Team    │ │  Kafka   │ │ (future) │ │ (future) │
│          │ │          │ │          │ │          │ │          │
│ built    │ │ built    │ │ built    │ │          │ │          │
│ from the │ │ from the │ │ from the │ │          │ │          │
│ 3-layer  │ │ 3-layer  │ │ 3-layer  │ │          │ │          │
│ model §5 │ │ model §5 │ │ model §5 │ │          │ │          │
└────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘
     │            │            │             │            │
     └────────────┴────────────┴─────────────┴────────────┘
                               │ MCP tool calls
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  MCP TOOL SERVER  (FastAPI)                                          │
│  Shared observability tools any agent's Capabilities layer can call: │
│  query_logs · top_errors · search_keyword · blast_radius             │
│  Plus domain-specific tools declared per agent — see each agent's    │
│  runbook in runbooks/ for its full tool list                         │
└──────────────────────────────┬──────────────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  DATA LAYER                                                          │
│                                                                      │
│  Knowledge Graph (Postgres)   ← entities + edges, described in §7   │
│  Log pipeline (Kafka → Delta) ← one input source that feeds         │
│                                  depends_on edges into the graph     │
│  GitHub / Jira / Slack ingestion ← another input source, feeding    │
│                                     ownership and decision edges     │
└─────────────────────────────────────────────────────────────────────┘
```

One clarification that matters for anyone who saw an earlier version of
this project: the Kafka/Spark/Delta log pipeline that this repository
started with is **one input source that feeds the Knowledge Graph** — it
is not the center of the architecture. The center of the architecture is
the Knowledge Graph and the typed agent protocol above it; the log pipeline
is plumbing that supplies one kind of evidence (`depends_on` edges mined
from shared `trace_id`s across services) among several.

---

# Part V — Seeing It Work

## 11. End-to-End Walkthrough: Consumer Groups Per Topic

Everything above is abstract until you watch one real ticket move through
it. This section is both the technical mechanism explained step by step
*and* the actual demo script — nothing here is simplified for
presentation purposes; every tool call and message shown is a real tool
declared in that agent's runbook.

**The ticket, in plain language:** Confluent's Cluster Linking feature has
to "clamp" consumer offsets during a failover — meaning, for every consumer
group that was reading a topic on the old cluster, figure out the right
place for it to resume reading on the new cluster. Today, doing this
requires listing *every* consumer group in the entire cluster and checking
each one to see if it happens to be subscribed to the topic being failed
over. On a large cluster with 50,000 groups, this takes 8–12 seconds and
puts heavy load on the exact component (`GroupCoordinator`) that's already
under stress during a failover. What's actually needed is a lookup that
goes directly from "this topic-partition" to "these specific groups,"
without scanning everything else.

```
clampOffsets(topic, partition):                 # what happens today
  all_groups = ListGroups()                     # O(n_groups) — 8s for 50k groups
  for group in all_groups:
    if topic in DescribeGroup(group):            # extra RPC per group
      clamp(group, topic, partition)

clampOffsets(topic, partition):                 # what's actually needed
  groups = ListGroupsForTopicPartition(topic, partition)  # O(1) — <50ms
  for group in groups:
    clamp(group, topic, partition)
```

Here is the system working through it, minute by minute:

```
13:42:01  Ticket received:
          "Cluster Linking offset clamping during failover is too slow.
           Currently calls ListGroups() on the entire cluster and filters."

13:42:08  Orchestrator queries the Knowledge Graph (§7):
          "ListGroups" codepath   → owned_by → consumer-team
          "clampOffsets" codepath → owned_by → kora-global
          → decides to involve: kora-global, consumer-team, oss-kafka

13:42:12  kora-global agent investigates first — it's the team in pain,
          and it owns the evidence:
          tool get_failover_latency()   → p99 = 11,400ms, 91% of that
                                           time spent inside listGroups
          tool get_offset_clamp_trace() → confirms 50,312 groups were
                                           scanned to find just 4 matches
          → produces a finding with confidence 0.95, and flags that it
            needs input from both consumer-team and oss-kafka

13:42:21  kora-global sends consumer-team a typed ImpactRequest (§8):
          "Need an indexed lookup by topic-partition. Can GroupCoordinator
           support this? What would it cost in memory?"

13:42:27  consumer-team agent responds — it owns GroupCoordinator, so it
          can answer with real detail instead of guessing:
          tool get_offset_storage_schema()     → confirms no reverse
                                                   index exists today
          tool estimate_index_memory_cost()    → ~14MB overhead for a
                                                   50,000-group cluster
          ImpactResponse:
            affected_components: [GroupCoordinator, GroupMetadata]
            invariants: ["group state must stay consistent across rebalance"]
            confidence: 0.9
            open_questions: ["needs a new Kafka API version — ask oss-kafka"]

13:42:31  consumer-team forwards the remaining open question to oss-kafka:
          "Does adding a topic-partition filter to ListGroups need a KIP?"

13:42:44  oss-kafka agent responds — it owns the protocol and the KIP
          process, so it checks against real precedent instead of guessing:
          tool search_kips("ListGroups topic partition filter")
               → finds KIP-518 as the closest precedent, confirms it does
                 NOT cover this case — a new KIP is genuinely required
          tool check_compat(api_key=16, proposed_version=5, ...)
               → passes, with one note: interaction with the newer
                 KIP-848 consumer protocol needs explicit review
          ImpactResponse: impact_level = HIGH, confidence = 0.92

13:43:12  Orchestrator checks every codepath cited above against each
          agent's declared ownership boundary (§6) — all citations check
          out, nothing gets flagged as unverified

13:43:20  Orchestrator assembles the final result:
          execution_order:
            1. kora-global confirms this should go upstream, not stay
               Confluent-internal
            2. consumer-team drafts the KIP
            3. oss-kafka runs the community vote (~4 weeks)
            4. consumer-team implements the approved change
            5. kora-global integrates the new API into clampOffsets()
          approvals_needed: [consumer-team lead, oss-kafka committer]
          requires_human: true
          escalation_reason: "Two team lead approvals are required before
                               implementation can begin — this is a real
                               decision, not something to resolve silently"

13:43:22  The ticket is updated with: the root cause, the affected
          components, every piece of evidence collected, the approval
          order, and the one open question (KIP-848 compatibility) that
          the system explicitly could not resolve on its own.
```

Every line above corresponds to a real tool declared in that agent's
runbook and a real typed message from §8 — nothing here is a scripted
narration written just for a pitch. In an actual demo, you should be able
to click into any line and see the exact JSON `ImpactRequest`/
`ImpactResponse` and the raw tool output behind it. **That auditability is
the actual product**, not a side effect of it.

The full domain runbooks referenced above — with exact repository paths,
line ranges, and complete tool specifications — are:

- [`runbooks/kora-global-sme.md`](./runbooks/kora-global-sme.md)
- [`runbooks/consumer-team-sme.md`](./runbooks/consumer-team-sme.md)
- [`runbooks/oss-kafka-sme.md`](./runbooks/oss-kafka-sme.md)

---

# Part VI — Why This Is Defensible

## 12. The Moat: A Compounding Knowledge Graph

Anyone can wrap an LLM in a nice UI this week. The actual defensibility
question is: what does this system have in six months that a competitor
starting from scratch does not?

Look back at the walkthrough in Part V. Notice the exact chain the
Orchestrator needed to reason through, before calling a single agent:

```
Failover
   ↓
Offset clamping   (owned by kora-global)
   ↓
ListGroups scan   (owned by consumer-team)
   ↓
GroupCoordinator index design
   ↓
Kafka protocol version bump
   ↓
KIP approval   (owned by oss-kafka)
```

The first time this ticket type is seen, discovering that chain requires
every agent to actually investigate and consult each other, the way Part V
walked through. **But that chain — "changes to offset clamping eventually
need protocol-level approval through consumer-team and oss-kafka" — gets
written back into the Knowledge Graph as real `depends_on` and
`must_approve` edges once this ticket resolves.**

So the *next* time a ticket mentions failover or offset clamping, the
Orchestrator already knows, before calling a single agent, that this is
likely to cross three domains and end in a KIP approval — because it saw
that exact pattern resolve once before. A generic coding agent with a large
context window has to re-derive this reasoning from scratch, from the raw
codebase, on every single ticket, because it has no persistent place to
store "I figured this out already."

Concretely, every ticket this system resolves writes back:

- New `depends_on` edges discovered during impact analysis that weren't
  in the graph before
- New `must_approve` edges learned from who actually signed off on the
  resulting change
- Updated `confidence` scores on existing edges — confirmed if the outcome
  matched, downgraded if it didn't
- New `Decision` nodes linking the resulting PR back to the reasoning that
  produced it

This means the system's answer to "who needs to be involved" gets
**structurally more accurate with every ticket it resolves** — not just
"there's more text available for retrieval," which is what a memory-only
competitor (like Glen, from §2) provides. A team that has run this system
for a year has a graph that a competitor starting today cannot buy,
scrape, or reconstruct from public data, because most of those edges only
exist as tribal knowledge until this system extracts and records them.

---

# Part VII — How We'll Build It

## 13. Phased Rollout

Given the open hypothesis in §4, the rollout is deliberately staged so
that autonomous code changes are *never* the first thing built. Each phase
only starts once the previous one has actually demonstrated its claim.

### Phase 1 — Investigation & Impact Analysis (the MVP)

```
Ticket → investigation → cross-team impact analysis → root cause →
a written-up recommended fix (NOT automatically applied)
```

Success is measured by comparing this system's triage — accuracy, speed,
and specifically how many real cross-team impacts it catches — against
both a human on-call engineer and a single large-context-window coding
agent given the same ticket and full repository access. This comparison
*is* the test of the hypothesis in §4.

### Phase 2 — Draft Changes

```
Phase 1 output → branch + tests + a draft PR (a human merges it)
```

This phase only starts after Phase 1 shows that cross-agent impact analysis
genuinely catches things a single agent misses — that's the actual
differentiator being tested, and it needs to be proven before investing in
code generation.

### Phase 3 — Autonomous Remediation

```
Phase 2 output → auto-merge for low-risk, high-confidence changes →
human review reserved for the escalated subset
```

This phase is gated behind sustained accuracy in Phase 2, in production,
over real tickets — not behind a demo looking good.

## 14. Repository Layout

```
log-analytics-copilot/
│
├── proto/
│   ├── logs.proto               # LogEvent + LogIngestionService (existing)
│   └── sme_agents.proto         # Ticket, Finding, ImpactRequest/Response,
│                                 # Orchestrator + SMEAgent RPCs (§8)
│
├── knowledge-graph/
│   ├── schema.sql                # entities + edges tables (§7)
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
│   ├── ownership_validator.py    # checks cited codepaths against §6 boundaries
│   └── Dockerfile
│
├── agents/
│   ├── base_agent.py             # SMEAgentBase + @tool decorator + 3-layer context (§5)
│   ├── kora_global_agent.py
│   ├── consumer_team_agent.py
│   ├── oss_kafka_agent.py
│   ├── broker_team_agent.py      # future
│   ├── billing_team_agent.py     # future
│   └── Dockerfile
│
├── runbooks/                     # persistent Domain Memory per agent (§5)
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
│   ├── kafka_to_delta.py         # not the center of the architecture (§10)
│   ├── build_service_graph.py    # trace_id → depends_on edges (feeds §7)
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

## 15. Data Schema

### LogEvent (`proto/logs.proto`)

| Field | Type | Notes |
|---|---|---|
| `timestamp` | string | ISO-8601 UTC |
| `service` | string | Partition key in the Silver log table |
| `level` | string | DEBUG / INFO / WARN / ERROR |
| `message` | string | Free-form, tokenized for keyword search |
| `trace_id` | string | Shared across services for one logical request — this is what feeds `depends_on` edges into the Knowledge Graph |
| `event_id` | string | Unique per log line, used for deduplication |
| `host` | string | Originating host |

### Knowledge Graph tables (Postgres — see §7)

| Table | Description |
|---|---|
| `entities` | Services, repositories, codepaths, decisions, teams, and rules |
| `edges` | Typed relationships between entities, each with supporting evidence and a confidence score |

### Delta tables (log pipeline — one input source, see §10)

| Table | Layer | Description |
|---|---|---|
| `bronze_logs` | Bronze | Raw JSON as ingested from Kafka, append-only |
| `silver_logs` | Silver | Parsed and deduplicated on `(trace_id, event_id)`, partitioned by service |
| `service_graph` | Gold | `(from_service, to_service, co_occurrences)` — feeds `depends_on` edges into the Knowledge Graph |

### `Finding` / `ImpactResponse` (`proto/sme_agents.proto`)

Every SME agent returns these same shapes (§8). Fields are additive —
an agent only fills in what it actually knows, and `open_questions` is a
first-class field for admitted uncertainty rather than a forced guess.

## 16. Adding a New SME Agent

Adding a new domain to the network is meant to be a small, mechanical
change — not a redesign of the proto or the Orchestrator.

**Step 1 — Write the runbook.** Create `runbooks/my-team-sme.md`
describing what the team owns (exact repo paths and, where useful, line
ranges), and what domain-specific tools it needs. Use the three existing
runbooks as the template for the level of detail expected.

**Step 2 — Implement the agent:**

```python
from agents.base_agent import SMEAgentBase, tool

class MyTeamAgent(SMEAgentBase):
    AGENT_NAME = "my-team"
    DOMAIN     = "plain English description of what this team owns"
    OWNS       = ["path/to/repo/", "another/owned/path/"]  # enforced per §6

    @tool("my_tool")
    async def my_tool(self, param: str) -> dict:
        """One-line description shown to the Orchestrator's classifier."""
        return await self.mcp.query_logs(f"SELECT ... WHERE service='my-service'")
```

**Step 3 — Register it.** Add the new agent to `docker-compose.yml`. On
boot, it registers itself with the Orchestrator automatically.

Nothing about the proto, the Orchestrator, or any existing agent needs to
change. The Knowledge Graph picks up the new `Team --owns--> Repository`
edges directly from the new agent's declared `OWNS` list and runbook.

## 17. Build Roadmap

### Phase 0 — Observability backbone (done)
- [x] Kafka (KRaft mode) + Spark streaming, Bronze/Silver Delta tables
- [x] MCP server with 5 tools (`query_logs`, `top_errors`, `search_keyword`,
      `pipeline_status`, `optimize_table`)
- [x] `proto/logs.proto` + `LogIngestionService`
- [x] Three SME runbooks (`kora-global`, `consumer-team`, `oss-kafka`)

### Phase 1 — Knowledge Graph + typed protocol (Weeks 1–2)
- [ ] `knowledge-graph/schema.sql` — entities + edges (§7)
- [ ] `knowledge-graph/ingest_github.py` — CODEOWNERS + PR history → ownership edges
- [ ] `proto/sme_agents.proto` — `Ticket`, `Finding`, `ImpactRequest`/`ImpactResponse` (§8)
- [ ] `orchestrator/ownership_validator.py` — enforce §6 boundaries in code

### Phase 2 — First three SME agents (Weeks 3–4)
- [ ] `agents/consumer_team_agent.py`, `kora_global_agent.py`, `oss_kafka_agent.py`
- [ ] Orchestrator consultation loop using the typed protocol
- [ ] Knowledge-graph-driven classifier, replacing keyword-only routing

### Phase 3 — Prove the core hypothesis (Week 5)
- [ ] Run the same 10–20 real tickets through (a) this multi-agent system
      and (b) a single large-context-window agent with full repo access
- [ ] Compare root-cause accuracy, cross-team impacts caught, and time to answer
- [ ] **Decision point:** proceed to Phase 2 of the rollout (§13) only if
      the multi-agent system demonstrably wins on impact-catch rate

### Phase 4 — Draft PRs (Phase 2 of the rollout, §13)
- [ ] Fix Planner + Code Agent + Test Agent
- [ ] The consumer-groups-per-topic example from Part V produces an actual
      draft pull request, end to end

---

# Part VIII — Design Rationale (FAQ)

## 18. Design Decisions

**Why a typed agent-to-agent protocol instead of free-form chat?**
Free-form LLM-to-LLM chat compounds interpretation error with every hop —
by the third agent in a chain, claims have quietly drifted from evidence
into paraphrase, with no visible signal of where confidence should have
dropped. A typed `ImpactRequest`/`ImpactResponse` (§8) forces every agent
to commit to structured, falsifiable claims — a list of affected
components, an explicit confidence score, an explicit list of open
questions — that the Orchestrator can check against the Knowledge Graph
before anything downstream is allowed to trust them.

**Why a Postgres knowledge graph instead of a vector database?**
"Who owns this," "what does this block," and "which team must approve this
kind of change" are graph-traversal questions, not similarity-search
questions. A vector database answers "what text looks similar to this
text" — it fundamentally cannot answer "what is the actual dependency chain
between offset clamping and a Kafka protocol version bump." Postgres with
a plain entities/edges schema is boring, cheap to run, and directly answers
the questions this product actually needs answered. A dedicated graph
database is only worth the added operational complexity if query patterns
genuinely outgrow SQL joins later.

**Why enforce explicit ownership boundaries instead of letting every
agent know everything?**
Without a boundary, every agent is tempted to guess about code it doesn't
actually own — which is exactly the failure mode of a single big-context
agent: confident, fluent, and sometimes wrong about code it has never
really worked in. Requiring an agent to send a typed request to the agent
that actually owns a codepath, rather than answering from a guess, is what
makes the network's *aggregate* answer more reliable than any single
agent's guess. That only holds if the boundary is enforced by the
Orchestrator in code (§6), not left as a polite suggestion in a prompt.

**Why gRPC between agents, but MCP for tool calls?**
Agent-to-agent messages need typed contracts and can be several hops deep
in a single ticket's consultation chain — gRPC is built for exactly that.
Tool calls are simple request-response calls against the MCP server, which
is already HTTP-native and discoverable through its manifest endpoint.
These are genuinely different kinds of communication and conflating them
into one protocol would make both worse.

**Why is the log pipeline treated as one peripheral input, not the
center of the system?**
An earlier version of this project treated the Kafka/Spark/Delta log
pipeline as the core architecture. It's genuinely useful — shared
`trace_id`s across services are one legitimate source of `depends_on`
edges — but it is one ingestion pipeline among several (GitHub, Jira,
Slack, static analysis). The Knowledge Graph (§7) and the typed agent
protocol (§8) are the actual product; log ingestion is plumbing that feeds
one kind of evidence into it.

**Why not start with autonomous code changes?**
Because the hypothesis in §4 — that specialized agents with ownership
boundaries and typed communication beat one strong generalist agent — has
not actually been proven yet. Building auto-merge PR generation before
answering that question risks building an elaborate architecture that a
much simpler system would have matched just as well. Phase 1 of the
rollout (§13) exists specifically to settle that question honestly before
any further investment.

---

# Part IX — Implementation & Validation Plan

Part VII gave the high-level roadmap (Phase 0 through Phase 4). This part
turns that roadmap into buildable slices small enough to implement in a
day or two each, with an explicit test suite gating every slice. **Nothing
in this plan is considered "done" until its tests are green** — the tests
are the actual definition of done, not a nice-to-have added afterward.

## 19. How to Use This Plan

Each numbered phase below maps onto Part VII's roadmap, broken into
smaller slices. Every slice has the same five parts:

- **Build** — the file(s) to create
- **Unit tests** — fast, no network/DB/Docker, test one function in isolation
- **Integration tests** — real Postgres / real gRPC / real MCP server, but
  still automated and fast enough to run on every commit
- **Manual validation** — a command you can run by hand to sanity-check
  the slice before trusting the automated tests
- **Definition of done** — the specific, checkable condition that means
  this slice is genuinely finished, not just "code exists"

Work through the phases in order — each one depends on the previous one
existing and passing its tests. Do not start Phase 2 with Phase 1's tests
red; that debt compounds badly in a multi-agent system where later phases
depend on earlier ones being trustworthy, not just present.

## 20. Phase 1 — Knowledge Graph Foundation

### 20.1 Slice: Postgres Schema

**Build:** `knowledge-graph/schema.sql`, `knowledge-graph/db.py` (connection
pool + a small migration runner that applies `schema.sql` idempotently)

**Unit tests** (`knowledge-graph/tests/test_schema.py`, no live DB needed —
test the SQL string / migration logic itself):
- `test_schema_sql_is_valid_syntax` — parse the file with a SQL parser
  (e.g. `sqlparse`) and assert no syntax errors
- `test_migration_is_idempotent_on_repeat_apply` — running the migration
  runner twice against a mocked cursor issues `CREATE TABLE IF NOT EXISTS`

**Integration tests** (`knowledge-graph/tests/test_schema_integration.py`,
against a real throwaway Postgres — spin one up with
`docker run -d postgres:16` in a test fixture):
- `test_entities_and_edges_tables_exist_after_migration`
- `test_edge_from_id_and_to_id_enforce_foreign_key` — inserting an edge
  with a nonexistent `from_id` raises an integrity error
- `test_edge_confidence_defaults_to_one_point_zero`
- `test_indexes_exist_on_edges_from_and_to` — query
  `pg_indexes` and assert `idx_edges_from` / `idx_edges_to` are present

**Manual validation:**
```bash
docker run -d --name kg-test -e POSTGRES_PASSWORD=test -p 5433:5432 postgres:16
psql postgresql://postgres:test@localhost:5433/postgres -f knowledge-graph/schema.sql
psql postgresql://postgres:test@localhost:5433/postgres -c '\dt'
# expect to see: entities, edges
```

**Definition of done:** `pytest knowledge-graph/tests/test_schema*.py -v`
is fully green, and the manual `\dt` shows both tables with the indexes
from §7 present.

### 20.2 Slice: GitHub Ingestion

**Build:** `knowledge-graph/ingest_github.py` — parses `CODEOWNERS` files
and recent PR metadata, writes `Team --owns--> Repository/CodePath` edges

**Unit tests:**
- `test_parses_codeowners_line_into_pattern_and_owner`
- `test_ignores_comment_lines_and_blank_lines`
- `test_maps_wildcard_pattern_to_codepath_entity_with_correct_metadata`
- `test_pr_description_with_no_owner_mention_produces_no_decision_edge`

**Integration tests** (against a small fixture repo checked into
`knowledge-graph/tests/fixtures/sample-repo/`, containing a real
`CODEOWNERS` file and a couple of commits):
- `test_ingest_produces_owns_edge_for_each_codeowners_entry`
- `test_ingest_is_re_runnable_without_duplicating_edges` — run twice,
  assert edge count is unchanged, `last_seen_at` is updated instead

**Manual validation:**
```bash
python -m knowledge_graph.ingest_github --repo runbooks/ --dry-run
# expect printed edges like:
# Team(consumer-team) --owns--> CodePath(core/src/.../GroupCoordinator.scala)
```

**Definition of done:** running the ingestor against the three existing
runbooks' declared `OWNS` paths produces the exact ownership edges shown
in §6, verified by an integration test that asserts on the specific edge
rows, not just "some edges exist."

### 20.3 Slice: Trace-Based Dependency Mining

**Build:** `knowledge-graph/ingest_traces.py` — reads the `service_graph`
Delta/Gold table produced by the log pipeline (§10) and writes
`Service --depends_on--> Service` edges weighted by co-occurrence count

**Unit tests:**
- `test_co_occurrence_row_produces_depends_on_edge_with_matching_weight`
- `test_self_referential_rows_are_excluded` (`from_service == to_service`)
- `test_zero_co_occurrence_rows_are_skipped`

**Integration tests** (reuse `scripts/produce-fake-logs.py` to generate a
known, small set of synthetic logs with controlled `trace_id` overlap
across services):
- `test_end_to_end_from_synthetic_logs_produces_expected_edge_count` —
  produce logs where `service-a` and `service-b` share exactly 5 `trace_id`s
  and no others, run the full pipeline, assert exactly one `depends_on`
  edge with weight 5

**Manual validation:**
```bash
python -m knowledge_graph.ingest_traces --source delta/service_graph
psql ... -c "SELECT * FROM edges WHERE relation = 'depends_on' LIMIT 10;"
```

**Definition of done:** the integration test above is green, and running
the ingestor against the real Delta data already on disk in this repo
(from the earlier May 2026 pipeline run) produces at least one
`depends_on` edge that matches something a human can verify by eye in the
raw logs.

### 20.4 Slice: Graph Query API

**Build:** `knowledge-graph/query.py` — the functions the Orchestrator
actually calls: `owning_team(codepath)`, `depends_on(service, depth=1)`,
`must_approve(change_type)`

**Unit tests** (against a small in-memory or SQLite-backed fixture graph,
not Postgres — these should be fast):
- `test_owning_team_returns_correct_team_for_exact_path_match`
- `test_owning_team_returns_none_for_unknown_path`
- `test_depends_on_respects_requested_depth`
- `test_depends_on_returns_edges_sorted_by_confidence_descending`

**Integration tests** (seed a real Postgres test DB with the ownership
edges from §20.2 using the three real runbooks):
- `test_owning_team_of_list_groups_returns_consumer_team` — this is the
  literal example from §10: `owning_team("ListGroups")` must return
  `"consumer-team"`
- `test_owning_team_of_clamp_offsets_returns_kora_global` — likewise,
  `owning_team("clampOffsets")` must return `"kora-global"`

**Manual validation:**
```bash
python -c "
from knowledge_graph.query import owning_team
print(owning_team('ListGroups'))    # expect: consumer-team
print(owning_team('clampOffsets'))  # expect: kora-global
"
```

**Definition of done for Phase 1:** all tests across 20.1–20.4 pass in CI,
and the two manual query calls above return the correct agent names
against a graph built entirely from real ingestion — this is the exact
lookup the Orchestrator performs at `13:42:08` in the Part V walkthrough,
now backed by real code instead of a narrated example.

## 21. Phase 2 — Typed Protocol

### 21.1 Slice: Proto Definitions

**Build:** `proto/sme_agents.proto` (the schema from §8), plus generated
Python stubs via `protoc`

**Unit tests** (`proto/tests/test_messages.py`):
- `test_impact_request_round_trips_through_serialize_and_parse`
- `test_impact_response_open_questions_defaults_to_empty_list`
- `test_finding_confidence_field_accepts_float_between_0_and_1`

**Integration tests:**
- `test_protoc_generates_python_stubs_without_error` — actually invoke
  `protoc` in the test (or as a pre-test build step) and import the result
- `test_grpc_channel_can_send_impact_request_to_stub_server` — spin up a
  minimal test gRPC server that echoes the request back, confirm the
  message survives the wire round trip unchanged

**Manual validation:**
```bash
python -c "
from proto import sme_agents_pb2 as pb
m = pb.ImpactRequest(from_agent='kora-global', to_agent='consumer-team',
                      request_type='impact_analysis')
print(m)
"
```

**Definition of done:** `protoc` compiles cleanly, and the manual command
above prints a well-formed message with the fields set as expected.

### 21.2 Slice: Ownership Validator

**Build:** `orchestrator/ownership_validator.py` — checks that every
`codepaths` entry in a `Finding`/`ImpactResponse` falls inside the issuing
agent's declared `OWNS` list

**Unit tests:**
- `test_citation_matching_an_owned_exact_path_passes`
- `test_citation_matching_an_owned_prefix_path_passes` — e.g. an owned
  path of `core/coordinator/group/` matches a cited file
  `core/coordinator/group/GroupMetadata.scala`
- `test_citation_outside_all_owned_paths_is_flagged_needs_verification`
- `test_empty_codepaths_list_passes_trivially`

**Integration tests:**
- `test_validator_against_consumer_team_runbook_fixture` — load the real
  `OWNS` list from `runbooks/consumer-team-sme.md`'s corresponding agent
  manifest, and confirm a citation of `GroupCoordinator.scala` passes while
  a citation of `OffsetClampingService.java` (which belongs to
  `kora-global`) is correctly flagged

**Manual validation:** run the validator against the three canned
`ImpactResponse` payloads from the Part V walkthrough and confirm all
pass (since that walkthrough was written to be internally consistent).

**Definition of done for Phase 2:** the proto compiles and round-trips
correctly, and the ownership validator correctly distinguishes valid from
invalid citations using the real runbooks as ground truth — not synthetic
fixtures invented just for the test.

## 22. Phase 3 — Base Agent Framework

### 22.1 Slice: `SMEAgentBase` and the `@tool` Decorator

**Build:** `agents/base_agent.py`

**Unit tests:**
- `test_tool_decorator_registers_method_under_given_name`
- `test_get_tools_lists_every_decorated_method_on_the_instance`
- `test_registering_two_tools_with_the_same_name_raises_configuration_error`
- `test_tool_docstring_is_exposed_as_tool_description`

**Integration tests:**
- `test_agent_boot_calls_register_agent_on_orchestrator` — start a stub
  gRPC `Orchestrator` server that records incoming `RegisterAgent` calls,
  boot a minimal test agent subclass against it, assert exactly one
  `AgentManifest` was received with the correct `agent_name` and tool list

**Manual validation:**
```bash
python -m agents.tests.fixtures.dummy_agent --orchestrator-addr localhost:50050
# in another terminal, hit the stub orchestrator's registry endpoint and
# confirm "dummy-agent" with its 2 dummy tools shows up
```

**Definition of done:** a minimal fixture agent with two dummy `@tool`
methods boots, registers itself, and its manifest is visible in a test
Orchestrator's registry within two seconds of boot.

### 22.2 Slice: Domain Memory Loader

**Build:** agent boot logic that reads the corresponding
`runbooks/<agent-name>-sme.md` file and extracts the declared `OWNS` paths
and `DOMAIN` description used by the classifier (§23.2)

**Unit tests:**
- `test_parses_owns_paths_from_runbook_convention`
- `test_missing_runbook_file_raises_a_clear_configuration_error`
- `test_malformed_runbook_missing_domain_section_raises_clear_error`

**Definition of done for Phase 3:** the three real agents
(`kora-global`, `consumer-team`, `oss-kafka`) each boot successfully,
correctly load their declared ownership paths from their real runbook
files (not hardcoded in the agent's Python source), and register with a
running Orchestrator.

## 23. Phase 4 — Orchestrator Core

### 23.1 Slice: Agent Registry

**Build:** `orchestrator/registry.py`

**Unit tests:**
- `test_register_agent_adds_new_entry`
- `test_re_registering_the_same_agent_name_updates_rather_than_duplicates`
- `test_list_agents_returns_every_currently_registered_agent`
- `test_lookup_by_owned_path_returns_correct_agent`

### 23.2 Slice: Knowledge-Graph-Driven Classifier

**Build:** `orchestrator/classifier.py`

**Unit tests:**
- `test_extracts_likely_codepath_or_component_mentions_from_ticket_text`
- `test_maps_extracted_mention_to_owning_agent_via_knowledge_graph_query`
- `test_falls_back_to_keyword_match_against_agent_domain_strings_when_graph_has_no_hit`

**Integration tests:**
- `test_classify_consumer_groups_ticket_selects_all_three_agents` — feed
  in the *exact* ticket text used throughout Part V ("Cluster Linking
  offset clamping during failover is too slow...") against a Knowledge
  Graph seeded from the three real runbooks, and assert the classifier
  selects exactly `{kora-global, consumer-team, oss-kafka}` — no more, no
  fewer

**Definition of done:** the integration test above is green using the real
seeded graph from Phase 1, not a mocked classifier response.

### 23.3 Slice: Consultation Loop

**Build:** `orchestrator/consultation.py`

**Unit tests:**
- `test_investigate_is_called_on_every_classified_agent_in_parallel` —
  assert wall-clock time is closer to the slowest single agent than to
  the sum of all agents (proves parallelism, not just correctness)
- `test_impact_request_is_routed_to_the_agent_named_in_needs_from`
- `test_consultation_loop_terminates_after_a_max_hop_count` — construct a
  pathological fixture where two mocked agents keep citing each other in
  `needs_from` and assert the loop stops instead of running forever

**Integration tests:**
- `test_full_consultation_matches_part_v_structure` — mock the three
  agents to return the exact canned `Finding`/`ImpactResponse` payloads
  shown in the Part V walkthrough, run the consultation loop for real, and
  assert the resulting message sequence matches: kora-global investigates
  first, then sends an `ImpactRequest` to consumer-team, which forwards a
  derived question to oss-kafka

### 23.4 Slice: `TriageResult` Assembly

**Build:** the assembly step inside `orchestrator/main.py`

**Unit tests:**
- `test_requires_human_is_true_when_any_finding_carries_unresolved_open_questions_above_a_confidence_threshold`
- `test_execution_order_places_blocking_agents_before_the_agents_they_block`
- `test_approvals_needed_is_deduplicated_across_multiple_findings`

**Definition of done for Phase 4:** running `TriageTicket()` against the
three *mocked* agents (using their Part V canned responses) produces a
`TriageResult` whose `execution_order` has five steps in the correct
order, whose `approvals_needed` contains exactly the two names from §10,
and whose `requires_human` is `true` with a non-empty
`escalation_reason` — i.e., the mocked version of the full Part V
walkthrough passes as an automated test before a single real agent exists.

## 24. Phase 5 — First Three SME Agents (Real Tools, Real Data)

Each of the three agents gets the same test structure. Below is the
pattern; apply it once per agent using each agent's runbook as the tool
specification.

### 24.1 `kora-global` agent

**Build:** `agents/kora_global_agent.py` implementing the four tools from
[`runbooks/kora-global-sme.md`](./runbooks/kora-global-sme.md):
`get_failover_latency`, `get_offset_clamp_trace`, `list_active_links`,
`get_consumer_groups_for_link`

**Unit tests** (MCP calls mocked):
- `test_get_failover_latency_returns_the_documented_json_shape`
- `test_get_offset_clamp_trace_returns_phase_breakdown_summing_to_total_ms`
- `test_investigate_produces_a_finding_with_needs_from_consumer_team_and_oss_kafka`

**Integration tests** (against a real running MCP server, using its stub
executor — see the existing `mcp-server/tools.py`):
- `test_kora_global_tools_successfully_call_real_mcp_endpoints_and_parse_responses`

### 24.2 `consumer-team` agent

**Build:** `agents/consumer_team_agent.py` implementing
`get_group_state`, `get_groups_for_topic_partition`,
`get_offset_storage_schema`, `estimate_index_memory_cost`,
`get_rebalance_history` from
[`runbooks/consumer-team-sme.md`](./runbooks/consumer-team-sme.md)

**Unit tests:**
- `test_estimate_index_memory_cost_matches_the_documented_formula` — feed
  in `group_count=50000, avg_subscriptions=8` and assert the result
  matches the ~14MB figure worked out by hand in the runbook
- `test_get_offset_storage_schema_returns_the_documented_key_value_format`

**Integration tests:**
- `test_consultabout_responds_correctly_to_a_kora_global_impact_request` —
  send the exact `ImpactRequest` JSON from §8, assert the response
  contains `open_questions: ["needs a new Kafka API version — ask oss-kafka"]`

### 24.3 `oss-kafka` agent

**Build:** `agents/oss_kafka_agent.py` implementing `search_kips`,
`get_api_spec`, `check_compat`, `get_kip_template` from
[`runbooks/oss-kafka-sme.md`](./runbooks/oss-kafka-sme.md)

**Unit tests:**
- `test_search_kips_ranks_kip_518_as_closest_precedent_for_the_fixture_query`
  (using a small fixture KIP index checked into
  `agents/tests/fixtures/kip_index.json`, not a live network call to the
  real Apache wiki)
- `test_check_compat_flags_the_kip_848_compatibility_note`

**Definition of done for Phase 5:** each agent, run standalone against a
locally running MCP server, produces a `Finding` whose content matches —
not word for word, but in the substantive claims — the corresponding
agent's step in the Part V walkthrough.

## 25. Phase 6 — Full Multi-Agent Integration Test (No Mocks)

This is the single most important test in the whole plan: it replays
Part V's walkthrough for real, against real running services, with no
mocked agents anywhere.

**Build:** `tests/integration/test_consumer_groups_per_topic_e2e.py`

**Setup:**
```bash
docker compose up -d   # real Orchestrator, all 3 real agents,
                       # real MCP server, real Knowledge Graph
                       # seeded from the 3 real runbooks
```

**Test steps:**
1. `POST` the exact ticket text from Part V to the API Gateway
2. Collect the streamed `Finding` events
3. Assert:
   - all three agents (`kora-global`, `consumer-team`, `oss-kafka`)
     appear in the stream, in that causal order
   - the final `TriageResult.execution_order` has exactly the five steps
     from §10, in the same order
   - `approvals_needed` contains `consumer-team lead` and
     `oss-kafka committer` (allowing for reasonable string variation)
   - `requires_human == true`
4. Run `ownership_validator` (§21.2) against every codepath cited across
   every finding in the run and assert zero `needs_verification` flags

**Definition of done for Phase 6:** a single command,
`pytest tests/integration/test_consumer_groups_per_topic_e2e.py -v`,
passes against the live `docker compose` stack. From this point forward,
this test is the regression guard — it must stay green through every
subsequent phase, because it's the concrete proof that the abstract
architecture in Parts I–IV actually works end to end.

## 26. Phase 7 — API Gateway

### 26.1 Slice: Ticket Normalization

**Build:** `gateway/main.py` — endpoints that accept GitHub, Jira, or raw
JSON payloads and normalize them into the `Ticket` proto

**Unit tests:**
- `test_normalize_github_issue_webhook_payload`
- `test_normalize_jira_webhook_payload`
- `test_normalize_raw_manual_post_payload`
- `test_malformed_payload_returns_400_with_a_clear_error_message`

### 26.2 Slice: Streaming Response

**Integration tests:**
- `test_client_receives_partial_findings_before_the_final_triage_result` —
  assert at least one `Finding` event arrives measurably before the
  connection closes with the final `TriageResult`

**Manual validation:**
```bash
curl -N -X POST http://localhost:8080/tickets \
  -H 'content-type: application/json' \
  -d @fixtures/consumer_groups_ticket.json
# expect to see Finding events stream in one at a time, followed by
# a final TriageResult event
```

**Definition of done for Phase 7:** the manual `curl` command above
visibly streams partial results rather than blocking silently until
everything is done.

## 27. Phase 8 — Hypothesis Validation Harness

This phase is different in kind from the previous ones: its purpose is
not to verify that code behaves as specified, but to test the actual
product hypothesis from §4. There is no "unit test" for a hypothesis —
instead, this phase builds a small evaluation harness and a scoring
report.

**Build:**
- `eval/tickets/` — 10–20 real or realistic tickets (the consumer-groups-
  per-topic ticket plus similar-shaped ones), each with a human-labeled
  ground truth: which teams should actually be involved, and why
- `eval/run_comparison.py` — runs every ticket through (a) this
  multi-agent system and (b) a single large-context-window agent given the
  same ticket and full repository read access
- `eval/scoring.py` — scores both runs against the ground truth on:
  - **recall** — of the teams that should have been identified, how many
    were actually caught
  - **precision** — of the teams identified, how many were actually
    relevant (catches over-escalation)
  - **time to answer**
  - **confidence calibration** — did a high confidence score actually
    correlate with a correct answer, across both systems

**"Tests" for this phase are regression thresholds, not pass/fail unit
tests:**
- `test_multiagent_recall_does_not_regress_below_baseline_run` — once a
  first baseline number exists, CI fails if a later change drops recall
  below it
- `test_eval_harness_runs_all_fixture_tickets_without_crashing` — the one
  true unit-test-shaped check in this phase; the harness itself must be
  reliable even before its output is meaningful

**Definition of done for Phase 8:** `eval/results/report.md` is generated
automatically and shows, ticket by ticket, whether the multi-agent system
caught cross-team impacts that the single-agent baseline missed (or vice
versa). **This report is the actual decision gate** for whether Phase 2 of
the rollout (§13 — drafting real code changes) is worth building at all.

## 28. Test Pyramid Summary

| Phase | Unit tests | Integration tests | Special |
|---|---|---|---|
| 1 — Knowledge Graph | ~15 | ~6 | — |
| 2 — Typed Protocol | ~7 | ~3 | — |
| 3 — Base Agent Framework | ~7 | ~1 | — |
| 4 — Orchestrator Core | ~12 | ~2 | — |
| 5 — Three SME Agents | ~15 (5 per agent) | ~6 (2 per agent) | — |
| 6 — Full Integration | 0 | 1 (but the most important one) | — |
| 7 — API Gateway | ~4 | ~1 | — |
| 8 — Hypothesis Validation | ~1 | — | Evaluation harness + scoring report |

The shape of this pyramid is intentional: most of the volume is unit
tests on individual tools and validators (cheap, fast, run on every save),
a smaller number of integration tests confirm the real infrastructure
wiring (Postgres, gRPC, MCP), and there is exactly **one** load-bearing
end-to-end test (Phase 6) that proves the whole system works together —
everything after that treats it as a non-negotiable regression guard.

## 29. CI Gate Checklist

Before merging any change that touches a given phase's code:

- [ ] Every unit test for that phase passes
- [ ] Every integration test for that phase passes
- [ ] The Phase 6 end-to-end test (§25) still passes, once it exists —
      it is the permanent regression guard for the whole system
- [ ] `ownership_validator` reports zero new `needs_verification` flags
      introduced by the change
- [ ] If the change touches an agent's tools, that agent's runbook in
      `runbooks/` is updated to match — the runbook and the code must
      never drift apart, since the runbook is the source of truth other
      agents and the classifier rely on
