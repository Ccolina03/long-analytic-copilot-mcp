# SME Agent Network — Architecture

> **One-line pitch:** Every team gets an AI SME that replaces the work of
> the human specialist on that team — it owns that team's tickets, does
> that team's investigation with that team's tools, and consults other
> teams' SME agents directly when a fix crosses a boundary. No central
> brain classifies tickets or plays coordinator.

## How to Read This Document

This is written for an engineer who has never seen this project before.
It is organized in eight parts, in the order you should actually read
them:

- **Part I** explains the problem and the core idea, at a high level,
  before any implementation detail.
- **Part II** explains how *one* agent works in isolation — it is a full
  replacement for one team's human SME, not a generic assistant.
- **Part III** explains how agents consult each other directly, peer to
  peer, when a ticket crosses a boundary — this is the actual product.
- **Part IV** shows the full system as one diagram, once the pieces are
  understood.
- **Part V** walks through one real example end to end.
- **Part VI** explains why this compounds into a defensible product over
  time.
- **Part VII** is the practical build plan.
- **Part VIII** is a FAQ answering the "why not just do X" questions.
- **Part IX** is a phased implementation plan with concrete unit and
  integration tests gating every step.

One example is used consistently throughout: **the Kora Global team
(Cluster Linking) has already identified that its offset-clamping code is
too slow and has a rough idea of the fix. It owns the ticket and drives
the work, consulting the Consumer Team (GroupCoordinator) and the OSS
Kafka team (protocol/KIPs) directly along the way.** Concrete runbooks for
these three agents live in [`runbooks/`](./runbooks/).

---

# Part I — The Problem and the Idea

## 1. The Problem This Solves

In a real engineering organization, most tickets do not start as a mystery
that needs to be triaged from scratch. **A specific team notices a
specific problem in a system it owns, and usually already has a rough
idea of the fix.** For example: the Cluster Linking team notices that
their failover path is slow, traces it to a specific function, and has a
hypothesis — "we probably need an indexed lookup instead of a full scan."
That team files the ticket, and that team is the one who has to drive it
to resolution.

The hard part isn't figuring out *whose* problem this is — they already
know. The hard part is everything downstream of that:

- The proposed fix touches **code owned by another team**
  (`GroupCoordinator` belongs to the Consumer Team, not Cluster Linking),
  and someone has to actually validate whether the idea works there
- That validation might surface a **further dependency** neither team
  anticipated (a protocol change requiring upstream approval through the
  Kafka KIP process)
- Each of those teams has to actually **do real diligence** — check
  memory cost, check compatibility, check precedent — not just say "sounds
  fine"
- Multiple team leads may need to **approve** before work can even start
- The originating team has to **track all of this and assemble a plan**,
  without anyone playing full-time project manager

This is coordination and cross-team validation work, and it routinely
takes days even when the original diagnosis took an afternoon. The
originating team already did the hard engineering thinking; what eats the
calendar is chasing down the right person on two other teams, getting them
to actually look at it, and stitching their answers together.

## 2. Why This Isn't Already Solved

Multi-agent orchestration is already a crowded space. Before building
anything, it's worth being precise about what already exists and where
the actual gap is.

| Company | Core abstraction | What they actually provide |
|---|---|---|
| **Superset** | Many coding agents → parallel execution | Runs hundreds of generic coding agents in parallel on independent tasks |
| **Agent Relay** | Shared infrastructure | Messaging, session history, and tool plumbing (GitHub/Linear/Slack) between agents |
| **Glen** | Organizational memory | Aggregates agent sessions, Slack, PRs, tickets, and docs for retrieval by future agents |
| **Linzumi** | Human → many agents | Team chat interface that directs dozens of coding agents; turns org decisions into a source of truth |

Every one of these answers **"how do agents run, talk, or remember."**
None of them answer **"who else needs to weigh in on this specific
change, and what does their team's actual expertise say about it."**
That's the gap, and it's specifically a gap about **domain ownership**,
not about agent infrastructure.

```
Superset:      many agents  →  parallel execution
Agent Relay:   many agents  →  shared plumbing (messaging/tools/history)
Glen:          many agents  →  shared memory (retrieval)
Linzumi:       human        →  many agents (command & control)

THIS PROJECT:  a team's own problem → that team's SME agent owns it and
                       drives it → consults other teams' SME agents
                       directly, peer to peer, exactly when it needs to →
                       assembles the plan itself → human approval only
                       when a real decision requires one
```

**Glen** is the closest adjacent risk, because organizational memory
sounds similar to what's described here. The distinction: memory is
*retrieval* — "find me things that look related to this." What's described
here is a **team's actual domain expert, replicated as software, doing
that team's actual job** — running that team's tools, checking that team's
invariants, and giving an answer with the same authority a senior engineer
on that team would.

## 3. The Core Idea in One Picture

```
 A team notices a real, specific problem in a system it owns, and already
 has a rough idea of the fix — this is the normal starting point, not a
 mystery ticket that needs to be classified by something else first
                         │
                         ▼
 That team's SME Agent owns the ticket, exactly the way the human
 specialist on that team would if it landed in their queue
                         │
                         ▼
 The owning agent investigates using ITS OWN tools, grounded in ITS OWN
 team's real production data, and firms up the proposed direction
                         │
                         ▼
 When the fix touches a codepath or approval owned by another team, the
 owning agent sends THAT team's SME Agent a structured, typed request
 directly — peer to peer, the same way two human engineers would message
 each other — there is no broker in between deciding who talks to whom
                         │
                         ▼
 The consulted agent does ITS OWN real diligence, exactly as the human
 specialist on that team would — and if its own investigation surfaces a
 further dependency, it consults a third team's agent the same way
                         │
                         ▼
 The ORIGINATING agent — the one that owns the ticket — gathers what it
 learned and assembles the final plan itself: root cause, approvals
 needed, execution order, and what's still genuinely uncertain
                         │
                         ▼
 A human is looped in only when the owning agent itself determines a
 decision genuinely requires human judgment — not for every step
```

Everything in Parts II–IV is the mechanics of making each of these steps
real: what it means for an agent to "own" a ticket, how it knows who else
to talk to, and how that conversation stays precise instead of degrading
into vague chat.

## 4. The Hypothesis We're Testing

Before scaling any of this into a real platform, there's one question that
has to be answered honestly:

> **Does a network of agents — each one a genuine replacement for a
> specific team's SME, communicating peer to peer through a typed
> protocol — actually produce better cross-team answers than one very
> capable coding agent given the entire codebase and a huge context
> window?**

If yes, everything described in this document is justified. If no, this
architecture is unnecessary complexity and a single strong agent is simply
the better product.

This is why the build plan (§13, Part VII) deliberately starts with an
**investigation-only** phase — no autonomous code changes — and explicitly
measures this system against a single-agent baseline before building
anything further.

---

# Part II — How a Single Agent Works

## 5. The Three-Layer Agent Model

A SME Agent in this system is not a generic assistant with a clever
system prompt — it is meant to be a genuine, standing replacement for the
human specialist on one specific team. It is built from three distinct
layers of context, and keeping them separate is what makes it behave like
a real domain expert rather than a chatbot that happens to know some
Kafka trivia.

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
assistant, and it is the slowest-changing layer. For the Kora Global
agent, this is everything in
[`runbooks/kora-global-sme.md`](./runbooks/kora-global-sme.md): which
repositories it owns, exactly which files and line ranges within them,
known performance invariants, and the history of past incidents in
Cluster Linking. This is deliberately **not** a single vector database
with everything dumped in and retrieved by similarity search — see §7 for
why ownership and dependency are graph questions, not similarity
questions.

**Live Context** is specific to the one ticket being worked right now: the
raw ticket text (including whatever diagnosis the filing team already
did), any recent related incidents, in-flight changes to the same
codepaths, and whatever additional trace or log evidence gets pulled
while investigating further. It disappears once the ticket is resolved
(though the *outcome* gets written back into the Knowledge Graph — see
Part VI).

**Capabilities** are the explicit, scoped set of things the agent is
actually allowed to do: which MCP tools it can call, whether it can read
or write GitHub PRs, whether it can post to Slack, whether it can trigger
a CI run. The Billing agent cannot open a pull request against the
Broker team's repository, even if the underlying LLM decided that seemed
useful — this is an authorization boundary enforced in code, not a
suggestion in a prompt.

### Every agent has two jobs, not one

Because there is no central brain doing classification and delegation
(see Part III), every agent needs to be able to do two distinct things:

1. **Own a ticket** — when the problem originates in its own team's
   domain, it drives the entire ticket from investigation through to a
   final recommendation, consulting other agents as needed and producing
   the final write-up itself.
2. **Get consulted** — when *another* agent's ticket touches its domain,
   it receives a structured request, does real diligence using its own
   tools (not a shortcut answer), and responds — and if that diligence
   surfaces a further dependency outside its own domain, it consults a
   third agent the same way, on its own initiative.

Both roles use the exact same agent, the exact same tools, and the exact
same typed protocol (§8) — "owning" and "being consulted" are just two
entry points into one agent, mirroring how a human engineer both drives
their own team's tickets and gets pulled into other teams' investigations.

## 6. Ownership Boundaries

Every agent has an explicit, non-overlapping ownership boundary — the
detail that makes this a network of specialists rather than five copies
of the same generalist with different system prompts. It's enforced in
code, not just requested in a prompt.

Here are the three agents that exist today (full detail, including exact
file paths and line numbers, is in each agent's runbook):

```
kora-global          (Cluster Linking — owns the example ticket below)
  owns:
    confluent/kora-cluster-linking/src/main/java/io/confluent/clusterlink/
    → see runbooks/kora-global-sme.md

consumer-team        (GroupCoordinator — consulted for feasibility)
  owns:
    apache/kafka: core/src/main/scala/kafka/coordinator/group/
    confluent/kora-group-coordinator/
    → see runbooks/consumer-team-sme.md

oss-kafka            (Protocol + KIP process — consulted for the upstream path)
  owns:
    apache/kafka: clients/src/main/resources/common/message/*.json
    the Apache Kafka KIP process itself
    → see runbooks/oss-kafka-sme.md
```

**The rule that makes this matter:** when an agent's own investigation
touches a codepath it does not own, it is not allowed to guess about it,
even if the underlying LLM could probably guess correctly most of the
time. It has to actually message the agent that owns that codepath (§8).
Every agent self-checks this before finalizing any finding: it validates
that every codepath it is about to cite falls within its own declared
ownership, and marks anything else as something it obtained from another
agent's response rather than its own knowledge. This is the opposite
failure mode of a single giant-context agent, which will confidently
reason about code it has never actually worked in, simply because it fits
in the context window.

---

# Part III — How Agents Work Together

Part II described one agent in isolation, and its two jobs (owning a
ticket vs. being consulted). This part explains what actually happens
when a ticket needs more than one team — which is the actual product.

**There is no central Orchestrator that classifies tickets, decides who
gets called, or assembles the final answer.** The agent that owns the
ticket does all of that itself, the same way a human engineer driving a
cross-team fix would: they read the ticket (theirs, because it's their
team's problem), they figure out who else they probably need based on
what they already know about the system plus a quick lookup of who owns
what, they message those people directly, and they write the final
summary themselves once they have what they need.

The only central piece that exists is a **thin intake router** — described
in §9 — whose entire job is receiving an incoming ticket and handing it to
the team it's actually assigned to. It does not reason about the ticket's
content at all.

## 7. The Engineering Knowledge Graph

When the owning agent realizes its fix touches code it doesn't own, it
needs to know who to talk to — the same way a human engineer would check
an internal wiki, a CODEOWNERS file, or just ask around. Rather than
re-deriving this from scratch or guessing, every agent has access to a
shared, structured graph of the organization's real ownership and
dependency structure.

This needs to be a graph, not a vector database, because the actual
questions — "who owns this file," "what does merging this block," "which
team must approve this class of change" — are graph-traversal questions.
Semantic similarity search cannot answer "what is the dependency chain
between offset clamping and a Kafka protocol version bump"; it can only
find text that looks related.

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

Deliberately boring: **Postgres**, with the graph represented as two
tables — nodes (`entities`) and typed edges. A dedicated graph database is
not needed until query complexity genuinely outgrows SQL joins.

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

| Source | Produces |
|---|---|
| CODEOWNERS files + git history | `Team --owns--> Repository`, `Team --owns--> CodePath` |
| PR descriptions and review comments | `Decision --applies_to--> CodePath`, `Decision --introduced_by--> PR` |
| Log traces (`trace_id` shared across services) | `Service --depends_on--> Service`, weighted by frequency |
| Static call-graph analysis | `CodePath --calls--> CodePath` |
| Outcomes of tickets this system resolves | `ChangeType --must_approve--> Team`, learned from who actually signed off |

Any agent can query this graph directly — `owning_team("GroupCoordinator")`
— the same lightweight lookup a human would do before sending a Slack
message. That last row matters for Part VI: every ticket resolved through
this system makes the graph slightly more accurate for the next agent that
queries it.

## 8. Agent-to-Agent Protocol

Once the owning agent knows who else it needs, it messages that agent
directly — peer to peer, not through a broker. The obvious approach —
letting agents exchange free text the way a Slack thread would look — has
a specific, serious failure mode: **free-text LLM-to-LLM communication
compounds interpretation error with every hop.** By the third agent in a
chain, claims have quietly drifted from "here is evidence" into "here is
my paraphrase of someone else's paraphrase," with no visible signal of
where confidence should have dropped.

So agents exchange a **typed, structured message** instead. Here is the
actual example from the running scenario: `kora-global` already diagnosed
that its offset-clamping code is slow because of full group scans, and
has a rough fix idea. It now needs to ask `consumer-team` — who actually
owns `GroupCoordinator` — whether that idea is even feasible.

**Request, from `kora-global` directly to `consumer-team`:**

```json
{
  "from": "kora-global",
  "to": "consumer-team",
  "request_type": "impact_analysis",
  "change_description": "We've traced our offset-clamping slowness to a full ListGroups() scan on every failover. We want an indexed lookup: given a topic-partition, return only the subscribed groups, instead of scanning the whole cluster.",
  "questions": [
    "Can GroupCoordinator support an indexed reverse lookup by topic-partition?",
    "What would the memory cost be at Confluent Cloud scale?",
    "Does this require a new Kafka protocol version?"
  ],
  "ticket_id": "CL-4821"
}
```

**Response, from `consumer-team` directly back to `kora-global`:**

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

The response is not a courtesy reply — `consumer-team` actually ran its
own tools (`get_offset_storage_schema`, `estimate_index_memory_cost`) to
produce it, exactly as the human Consumer Team engineer would have if
Slacked with the same question. Notice the last field: `consumer-team`
itself does not know whether this needs a new protocol version, so it
says so explicitly and — on its own initiative, without being told to —
goes and asks `oss-kafka` next. **No third party decided that consumer-team
should talk to oss-kafka; consumer-team figured that out itself, the same
way a human engineer would realize mid-investigation that they need to
loop in one more person.**

`open_questions` is what makes this honest: an agent admitting "I don't
know this part" is a first-class, successful output, not a failure to
hide. It's also the signal that eventually tells the *ticket-owning* agent
(§9) when something needs to escalate to a human instead of being quietly
resolved with a confident-sounding guess.

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

## 9. The Ticket as a Unit of Ownership

Putting §7 and §8 together: a ticket belongs to exactly one team's agent
from start to finish. That agent is responsible for the entire arc —
investigation, reaching out to whichever other agents it decides it needs,
and writing the final recommendation — the same way a human engineer who
files a ticket for their own team stays the owner of it even while other
people get pulled in to help.

```
                    Jira / GitHub Ticket
                  (assigned to a specific team,
                   because that team found the problem)
                            │
                            ▼
                  ┌───────────────────┐
                  │  Ticket Router     │   ← the ONLY central component,
                  │  (intake only —    │     and it does no reasoning —
                  │   no reasoning)    │     it just reads "team: kora-
                  └─────────┬─────────┘     global" off the ticket and
                            │                hands it to that agent
                            ▼
                  kora-global agent
                  (owns this ticket end to end)
                            │
              investigates with its OWN tools,
              confirms its OWN diagnosis and fix idea
                            │
              looks up ownership in the Knowledge Graph (§7),
              decides on its own who else it needs
                            │
                            ▼
              sends a typed ImpactRequest directly to
              consumer-team (§8)
                            │
              consumer-team does its OWN real diligence,
              realizes it needs oss-kafka, and consults
              oss-kafka directly — on its own initiative
                            │
              responses flow back to kora-global
                            │
                            ▼
              kora-global — because it OWNS the ticket —
              assembles the final plan itself: root cause,
              approvals needed, execution order, and what's
              still genuinely uncertain
                            │
                            ▼
                    Human approval gate
              (only if kora-global itself determines a
               decision genuinely requires one)
```

The reason this produces an auditable final answer rather than a black
box: at the end, you can point to exactly which agent said what, with what
confidence, and what evidence backed it up — because every cross-team
exchange was a typed message (§8), and the final write-up was produced by
the one agent that was actually accountable for the ticket throughout,
not synthesized after the fact by something that wasn't really involved.

---

# Part IV — The Full System

## 10. System Architecture, Layer by Layer

With Parts II and III explained, here is the complete system. Notice there
is no "Orchestrator" box doing classification or consultation — that logic
now lives inside every agent, because every agent needs to be able to own
a ticket.

```
┌─────────────────────────────────────────────────────────────────────┐
│  INPUTS                                                              │
│  GitHub Issues · Jira Webhooks · Slack Alerts · Manual HTTP POST    │
│  Each ticket already carries (or is assigned) an owning team         │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ HTTPS
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  API GATEWAY + TICKET ROUTER  (FastAPI)                              │
│  • Single external entry point, handles auth + rate limiting         │
│  • Normalizes any ticket format (Jira/GitHub/raw) into one shape     │
│  • Reads which team the ticket is assigned to and hands it directly  │
│    to that team's agent — no domain classification, no reasoning     │
│  • Streams that agent's progress back to the caller as it works      │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ gRPC: OwnTicket(Ticket) — sent straight
                               │ to the one agent that owns it
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│           THE OWNING SME AGENT (e.g. kora-global)                    │
│  • Investigates with its own tools and its own Domain Memory (§5)    │
│  • Looks up ownership in the Knowledge Graph (§7) to decide who       │
│    else it needs — on its own, the way a human engineer would        │
│  • Sends typed ImpactRequests directly to other agents (§8)          │
│  • Assembles the final plan itself once it has what it needs         │
└──────┬─────────────────────────────────────────────────┬─────────────┘
       │ gRPC: ImpactRequest / ImpactResponse             │ gRPC (same protocol,
       ▼ (peer to peer — no broker in the middle)         │  may fan out further)
┌──────────┐                                        ┌──────────┐
│   SME    │  ── may itself consult a third agent → │   SME    │
│  Agent:  │     the same way, on its own            │  Agent:  │
│ Consumer │     initiative (e.g. consumer-team      │   OSS    │
│  Team    │◄──────────────────────────────────────► │  Kafka   │
└────┬─────┘                                        └────┬─────┘
     │                                                    │
     └─────────────────────┬──────────────────────────────┘
                            │ every agent's Capabilities layer
                            │ calls MCP tools the same way
                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│  MCP TOOL SERVER  (FastAPI)                                          │
│  Shared observability tools any agent can call:                      │
│  query_logs · top_errors · search_keyword · blast_radius             │
│  Plus domain-specific tools declared per agent — see runbooks/        │
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

One clarification for anyone who saw an earlier version of this document:
there used to be a central "Orchestrator" that classified tickets and ran
a consultation loop across agents. That responsibility has moved **into
each agent** — every agent can own a ticket and drive its own
consultations, the same way a human specialist would. The only thing that
remains centralized is dumb intake routing (which team does this ticket
belong to), and the shared Knowledge Graph and MCP tool server that every
agent reads from.

---

# Part V — Seeing It Work

## 11. End-to-End Walkthrough: Consumer Groups Per Topic

**The starting point is not a mystery bug.** The Kora Global team already
noticed, in its own production data, that failover is too slow, and it
already has a hypothesis about the fix. That's the normal case, and it's
where this walkthrough starts.

**What Kora Global already knows before filing the ticket:** during a
failover, `ClusterLinking.clampOffsets()` has to find every consumer group
subscribed to the topics being failed over. Today it does this by listing
*every* group in the entire cluster and checking each one — on a cluster
with 50,000 groups, that takes 8–12 seconds and hammers the exact
component (`GroupCoordinator`) that's already under stress during a
failover. Kora Global's rough idea: a lookup that goes directly from
"this topic-partition" to "these specific groups," without scanning
everything else.

```
clampOffsets(topic, partition):                 # what happens today
  all_groups = ListGroups()                     # O(n_groups) — 8s for 50k groups
  for group in all_groups:
    if topic in DescribeGroup(group):            # extra RPC per group
      clamp(group, topic, partition)

clampOffsets(topic, partition):                 # Kora Global's proposed fix
  groups = ListGroupsForTopicPartition(topic, partition)  # O(1) — <50ms
  for group in groups:
    clamp(group, topic, partition)
```

Here is `kora-global` owning this ticket from start to finish:

```
13:42:01  Ticket filed BY Kora Global, assigned to Kora Global:
          "Our clampOffsets() is too slow because of a full ListGroups()
           scan on every failover. We think we need an indexed lookup by
           topic-partition. Need to confirm this is feasible and figure
           out what it takes to ship."

13:42:03  Ticket Router reads "team: kora-global" off the ticket and
          hands it straight to the kora-global agent — no classification,
          no other agent is even aware of this yet

13:42:12  kora-global agent starts working ITS OWN ticket, using ITS OWN
          tools to firm up the diagnosis it already suspected:
          tool get_failover_latency()   → p99 = 11,400ms, 91% of that
                                           time spent inside listGroups
          tool get_offset_clamp_trace() → confirms 50,312 groups scanned
                                           to find just 4 real matches
          → its own hypothesis is now backed by hard evidence, but it
            knows it doesn't own GroupCoordinator, so it can't just
            assume the fix is buildable — it has to ask

13:42:21  kora-global looks up ownership in the Knowledge Graph (§7):
          "GroupCoordinator" → owned_by → consumer-team
          → sends consumer-team a typed ImpactRequest directly:
          "We've traced this to a full ListGroups() scan. Can
           GroupCoordinator support an indexed lookup by topic-partition?
           What would it cost in memory?"

13:42:27  consumer-team receives the request and does ITS OWN real
          diligence — not a courtesy answer, actual investigation:
          tool get_offset_storage_schema()  → confirms no reverse index
                                                exists today
          tool estimate_index_memory_cost() → ~14MB overhead for a
                                                50,000-group cluster
          ImpactResponse:
            affected_components: [GroupCoordinator, GroupMetadata]
            invariants: ["group state must stay consistent across rebalance"]
            confidence: 0.9
            open_questions: ["needs a new Kafka API version — ask oss-kafka"]

13:42:31  consumer-team, on its OWN initiative — nobody told it to —
          recognizes it needs a second opinion and consults oss-kafka
          directly: "Does adding a topic-partition filter to ListGroups
          need a KIP?"

13:42:44  oss-kafka does its OWN real diligence — it owns the protocol
          and the KIP process, so it checks against real precedent:
          tool search_kips("ListGroups topic partition filter")
               → finds KIP-518 as the closest precedent, confirms it does
                 NOT cover this case — a new KIP is genuinely required
          tool check_compat(api_key=16, proposed_version=5, ...)
               → passes, with one note: interaction with the newer
                 KIP-848 consumer protocol needs explicit review
          ImpactResponse: impact_level = HIGH, confidence = 0.92
          → responds back to consumer-team, who relays the combined
            answer back to kora-global

13:43:20  kora-global — because it OWNS this ticket — assembles the final
          plan itself, from what it learned across both consultations:
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

13:43:22  kora-global updates its OWN ticket with: the root cause
          (now backed by hard evidence, not just a hunch), the affected
          components across two other teams, the full approval chain,
          and the one open question (KIP-848 compatibility) nobody could
          fully resolve.
```

Every line above corresponds to a real tool declared in that agent's
runbook and a real typed message from §8 — nothing here is scripted for a
pitch. In an actual demo, you should be able to click into any line and
see the exact `ImpactRequest`/`ImpactResponse` JSON and the raw tool
output behind it. **That auditability is the actual product**, not a side
effect of it.

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

Look back at the walkthrough in Part V. Notice the chain that
`kora-global` had to discover by actually consulting people, because
nothing told it up front:

```
Failover
   ↓
Offset clamping   (owned by kora-global — where the ticket started)
   ↓
ListGroups scan   (owned by consumer-team — first agent consulted)
   ↓
GroupCoordinator index design
   ↓
Kafka protocol version bump
   ↓
KIP approval   (owned by oss-kafka — consulted by consumer-team, not
                 by kora-global directly — a chain kora-global didn't
                 even know to expect)
```

The first time this ticket type comes through, discovering that chain
requires the full sequence of consultations from Part V. **But that
chain — "changes to offset clamping eventually need protocol-level
approval through consumer-team and oss-kafka" — gets written back into the
Knowledge Graph as real `depends_on` and `must_approve` edges once this
ticket resolves.**

So the next time any team's agent owns a ticket that touches failover or
offset clamping, it can look this chain up directly instead of discovering
it step by step through live consultation. A generic coding agent with a
large context window has no persistent place to store "this exact chain
was already worked out" — it re-derives it from the raw codebase every
single time.

Concretely, every ticket resolved through this system writes back:

- New `depends_on` edges discovered during consultation that weren't in
  the graph before
- New `must_approve` edges learned from who actually signed off
- Updated `confidence` scores on existing edges — confirmed if the outcome
  matched, downgraded if it didn't
- New `Decision` nodes linking the resulting PR back to the reasoning that
  produced it

This means the system's answer to "who else do I need" gets **structurally
more accurate with every ticket it resolves** — not just "more text
available for retrieval," which is what a memory-only competitor (like
Glen, from §2) provides. A team that has run this system for a year has a
graph a competitor starting today cannot buy, scrape, or reconstruct from
public data, because most of these edges only exist as tribal knowledge
until agents actually consult each other and record what they learned.

---

# Part VII — How We'll Build It

## 13. Phased Rollout

Given the open hypothesis in §4, the rollout is deliberately staged so
that autonomous code changes are never the first thing built.

### Phase 1 — Investigation & Impact Analysis (the MVP)

```
A team's agent owns a real ticket → investigates with its own tools →
consults other agents directly as needed → writes up a recommended fix
(NOT automatically applied)
```

Success is measured by comparing this system's output — accuracy, speed,
and specifically how many real cross-team dependencies it catches —
against both the human specialist who would have owned this ticket, and a
single large-context-window coding agent given the same ticket and full
repository access.

### Phase 2 — Draft Changes

```
Phase 1 output → branch + tests + a draft PR (a human merges it)
```

Starts only after Phase 1 shows that peer-to-peer agent consultation
genuinely catches things a single agent misses.

### Phase 3 — Autonomous Remediation

```
Phase 2 output → auto-merge for low-risk, high-confidence changes →
human review reserved for the escalated subset
```

Gated behind sustained accuracy in Phase 2, in production, over real
tickets.

## 14. Repository Layout

```
log-analytics-copilot/
│
├── proto/
│   ├── logs.proto               # LogEvent + LogIngestionService (existing)
│   └── sme_agents.proto         # Ticket, Finding, ImpactRequest/Response,
│                                 # SMEAgent RPCs (§8) — no Orchestrator RPCs
│
├── knowledge-graph/
│   ├── schema.sql                # entities + edges tables (§7)
│   ├── ingest_github.py          # PRs, CODEOWNERS → ownership edges
│   ├── ingest_traces.py          # trace_id co-occurrence → depends_on edges
│   ├── ingest_ticket_outcomes.py # resolved tickets → must_approve edges
│   └── query.py                  # owning_team() / depends_on() — called
│                                  # directly by every agent, not a service
│
├── router/
│   ├── main.py                   # thin FastAPI intake — reads the
│   │                              # assigned team off a ticket and hands
│   │                              # it to that agent, no reasoning at all
│   └── Dockerfile
│
├── agents/
│   ├── base_agent.py             # SMEAgentBase: the 3-layer context (§5),
│   │                              # OwnTicket() workflow AND ConsultAbout()
│   │                              # workflow, ownership self-validation (§6)
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

Note the absence of an `orchestrator/` directory. What used to be
"Orchestrator logic" — the knowledge-graph lookup and the consultation
loop — is now a shared library (`knowledge-graph/query.py` and the
consultation logic inside `agents/base_agent.py`) that every agent calls
for itself. The only standalone service is `router/`, and it is
deliberately dumb.

## 15. Data Schema

### LogEvent (`proto/logs.proto`)

| Field | Type | Notes |
|---|---|---|
| `timestamp` | string | ISO-8601 UTC |
| `service` | string | Partition key in the Silver log table |
| `level` | string | DEBUG / INFO / WARN / ERROR |
| `message` | string | Free-form, tokenized for keyword search |
| `trace_id` | string | Shared across services for one logical request — feeds `depends_on` edges into the Knowledge Graph |
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

### `Ticket` / `Finding` / `ImpactResponse` (`proto/sme_agents.proto`)

Every agent's `OwnTicket()` result and every `ConsultAbout()` response uses
these same shapes (§8). Fields are additive — an agent only fills in what
it actually knows, and `open_questions` is a first-class field for
admitted uncertainty rather than a forced guess.

## 16. Adding a New SME Agent

Adding a new domain to the network is meant to be a small, mechanical
change.

**Step 1 — Write the runbook.** Create `runbooks/my-team-sme.md`
describing what the team owns (exact repo paths and, where useful, line
ranges), and what domain-specific tools it needs. Use the three existing
runbooks as the template.

**Step 2 — Implement the agent.** It inherits both the "own a ticket"
workflow and the "get consulted" workflow from the shared base class —
nothing custom needs to be written for either:

```python
from agents.base_agent import SMEAgentBase, tool

class MyTeamAgent(SMEAgentBase):
    AGENT_NAME = "my-team"
    DOMAIN     = "plain English description of what this team owns"
    OWNS       = ["path/to/repo/", "another/owned/path/"]  # enforced per §6

    @tool("my_tool")
    async def my_tool(self, param: str) -> dict:
        """One-line description used when reasoning about which tool to call."""
        return await self.mcp.query_logs(f"SELECT ... WHERE service='my-service'")
```

**Step 3 — Register it.** Add the new agent to `docker-compose.yml` and to
the Ticket Router's team → agent address mapping, so tickets assigned to
this team actually reach it.

Nothing about the proto or any existing agent needs to change. The
Knowledge Graph picks up the new `Team --owns--> Repository` edges
directly from the new agent's declared `OWNS` list.

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
- [ ] `knowledge-graph/query.py` — the lookup library every agent calls directly
- [ ] `proto/sme_agents.proto` — `Ticket`, `Finding`, `ImpactRequest`/`ImpactResponse` (§8)

### Phase 2 — Base agent framework + first three agents (Weeks 3–4)
- [ ] `agents/base_agent.py` — `OwnTicket()` and `ConsultAbout()` workflows,
      ownership self-validation (§6)
- [ ] `agents/consumer_team_agent.py`, `kora_global_agent.py`, `oss_kafka_agent.py`
- [ ] `router/main.py` — thin intake, team → agent address mapping

### Phase 3 — Prove the core hypothesis (Week 5)
- [ ] Run the same 10–20 real tickets through (a) this system, letting the
      real owning team's agent drive each one, and (b) a single
      large-context-window agent with full repo access
- [ ] Compare root-cause accuracy, cross-team dependencies caught, and
      time to answer
- [ ] **Decision point:** proceed to Phase 2 of the rollout (§13) only if
      this system demonstrably wins on cross-team catch rate

### Phase 4 — Draft PRs (Phase 2 of the rollout, §13)
- [ ] Fix Planner + Code Agent + Test Agent, invoked by the owning agent
      once its plan is assembled
- [ ] The consumer-groups-per-topic example from Part V produces an actual
      draft pull request, end to end

---

# Part VIII — Design Rationale (FAQ)

## 18. Design Decisions

**Why no central Orchestrator that classifies tickets and coordinates
agents?**
Because that's not how the real problem works. A ticket is not a mystery
that needs to be classified into domains by something outside the
originating team — a specific team already knows it's their problem, and
they're the ones with the context to know (roughly) who else might be
affected, the same way a human engineer would. Putting a classifier in
front of that adds a layer that can misroute or add latency without adding
real judgment. The agent that owns the problem is the right one to decide
who else to loop in, using the Knowledge Graph as a lookup tool, not a
decision-maker.

**If there's no Orchestrator, what does the Ticket Router actually do?**
Only one thing: reads which team a ticket is assigned to and hands it to
that team's agent. It does not reason about ticket content, does not
decide which other agents get involved, and does not assemble the final
result. That's deliberately dumb, on purpose — all the judgment lives in
the agent that owns the ticket.

**Why does the "get consulted" agent sometimes consult a third agent on
its own?**
Because that's what happens when a human specialist gets pulled into an
investigation and realizes mid-way that they need someone else's input
too — they don't report back "I don't know" and wait for someone else to
decide who to ask next; they go ask. Consumer Team consulting OSS Kafka in
Part V, without Kora Global ever asking for that specifically, is the
system working as intended, not an edge case.

**Why a typed agent-to-agent protocol instead of free-form chat?**
Free-form LLM-to-LLM chat compounds interpretation error with every hop —
by the third agent in a chain, claims have quietly drifted from evidence
into paraphrase, with no visible signal of where confidence should have
dropped. A typed `ImpactRequest`/`ImpactResponse` (§8) forces every agent
to commit to structured, falsifiable claims that the next agent, or the
owning agent assembling the final plan, can check rather than reinterpret.

**Why a Postgres knowledge graph instead of a vector database?**
"Who owns this," "what does this block," and "which team must approve this
kind of change" are graph-traversal questions, not similarity-search
questions. A vector database answers "what text looks similar to this
text" — it cannot answer "what is the actual dependency chain between
offset clamping and a Kafka protocol version bump." Postgres with a plain
entities/edges schema is boring, cheap to run, and directly answers the
questions this product actually needs answered.

**Why enforce explicit ownership boundaries instead of letting every
agent know everything?**
Without a boundary, every agent is tempted to guess about code it doesn't
actually own — which is exactly the failure mode of a single big-context
agent: confident, fluent, and sometimes wrong about code it has never
really worked in. Requiring an agent to message the agent that actually
owns a codepath, rather than answering from a guess, is what makes the
network's aggregate answer more reliable than any single agent's guess.

**Why gRPC between agents, but MCP for tool calls?**
Agent-to-agent messages need typed contracts and can be several hops deep
in a single ticket's consultation chain — gRPC is built for exactly that.
Tool calls are simple request-response calls against the MCP server,
which is already HTTP-native and discoverable through its manifest
endpoint. These are genuinely different kinds of communication.

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
boundaries and peer-to-peer typed communication beat one strong
generalist agent — has not actually been proven yet. Building auto-merge
PR generation before answering that question risks building an elaborate
architecture that a much simpler system would have matched just as well.
Phase 1 of the rollout (§13) exists specifically to settle that question
honestly before any further investment.

---

# Part IX — Implementation & Validation Plan

Part VII gave the high-level roadmap. This part turns that roadmap into
buildable slices small enough to implement in a day or two each, with an
explicit test suite gating every slice. **Nothing in this plan is
considered "done" until its tests are green** — the tests are the actual
definition of done, not a nice-to-have added afterward.

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
  this slice is genuinely finished

Work through the phases in order — each one depends on the previous one
existing and passing its tests.

## 20. Phase 1 — Knowledge Graph Foundation

### 20.1 Slice: Postgres Schema

**Build:** `knowledge-graph/schema.sql`, `knowledge-graph/db.py` (connection
pool + a small migration runner that applies `schema.sql` idempotently)

**Unit tests** (`knowledge-graph/tests/test_schema.py`, no live DB needed):
- `test_schema_sql_is_valid_syntax`
- `test_migration_is_idempotent_on_repeat_apply`

**Integration tests** (against a real throwaway Postgres):
- `test_entities_and_edges_tables_exist_after_migration`
- `test_edge_from_id_and_to_id_enforce_foreign_key`
- `test_edge_confidence_defaults_to_one_point_zero`
- `test_indexes_exist_on_edges_from_and_to`

**Manual validation:**
```bash
docker run -d --name kg-test -e POSTGRES_PASSWORD=test -p 5433:5432 postgres:16
psql postgresql://postgres:test@localhost:5433/postgres -f knowledge-graph/schema.sql
psql postgresql://postgres:test@localhost:5433/postgres -c '\dt'
# expect to see: entities, edges
```

**Definition of done:** `pytest knowledge-graph/tests/test_schema*.py -v`
is fully green.

### 20.2 Slice: GitHub Ingestion

**Build:** `knowledge-graph/ingest_github.py` — parses `CODEOWNERS` files
and recent PR metadata, writes `Team --owns--> Repository/CodePath` edges

**Unit tests:**
- `test_parses_codeowners_line_into_pattern_and_owner`
- `test_ignores_comment_lines_and_blank_lines`
- `test_maps_wildcard_pattern_to_codepath_entity_with_correct_metadata`

**Integration tests** (against a fixture repo in
`knowledge-graph/tests/fixtures/sample-repo/`):
- `test_ingest_produces_owns_edge_for_each_codeowners_entry`
- `test_ingest_is_re_runnable_without_duplicating_edges`

**Manual validation:**
```bash
python -m knowledge_graph.ingest_github --repo runbooks/ --dry-run
```

**Definition of done:** running the ingestor against the three existing
runbooks' declared `OWNS` paths produces the exact ownership edges shown
in §6.

### 20.3 Slice: Trace-Based Dependency Mining

**Build:** `knowledge-graph/ingest_traces.py`

**Unit tests:**
- `test_co_occurrence_row_produces_depends_on_edge_with_matching_weight`
- `test_self_referential_rows_are_excluded`

**Integration tests** (reuse `scripts/produce-fake-logs.py`):
- `test_end_to_end_from_synthetic_logs_produces_expected_edge_count`

**Definition of done:** the integration test is green, and running the
ingestor against the real Delta data already on disk produces at least
one `depends_on` edge verifiable by eye in the raw logs.

### 20.4 Slice: Graph Query Library

**Build:** `knowledge-graph/query.py` — `owning_team(codepath)`,
`depends_on(service, depth=1)`, `must_approve(change_type)`. This is a
**library**, imported directly by every agent — not a network service —
because every agent needs to run this lookup for itself.

**Unit tests:**
- `test_owning_team_returns_correct_team_for_exact_path_match`
- `test_owning_team_returns_none_for_unknown_path`
- `test_depends_on_respects_requested_depth`

**Integration tests** (seed a real Postgres test DB from the three real
runbooks):
- `test_owning_team_of_list_groups_returns_consumer_team` —
  `owning_team("ListGroups")` must return `"consumer-team"`
- `test_owning_team_of_clamp_offsets_returns_kora_global` —
  `owning_team("clampOffsets")` must return `"kora-global"`

**Manual validation:**
```bash
python -c "
from knowledge_graph.query import owning_team
print(owning_team('ListGroups'))    # expect: consumer-team
print(owning_team('clampOffsets'))  # expect: kora-global
"
```

**Definition of done for Phase 1:** all tests across 20.1–20.4 pass, and
the two manual query calls above return the correct agent names — this is
the exact lookup `kora-global` performs at `13:42:21` in the Part V
walkthrough, now backed by real code.

## 21. Phase 2 — Typed Protocol

### 21.1 Slice: Proto Definitions

**Build:** `proto/sme_agents.proto` (the schema from §8), plus generated
Python stubs

**Unit tests:**
- `test_impact_request_round_trips_through_serialize_and_parse`
- `test_impact_response_open_questions_defaults_to_empty_list`
- `test_finding_confidence_field_accepts_float_between_0_and_1`

**Integration tests:**
- `test_protoc_generates_python_stubs_without_error`
- `test_grpc_channel_can_send_impact_request_to_stub_server`

**Manual validation:**
```bash
python -c "
from proto import sme_agents_pb2 as pb
m = pb.ImpactRequest(from_agent='kora-global', to_agent='consumer-team',
                      request_type='impact_analysis')
print(m)
"
```

**Definition of done:** `protoc` compiles cleanly and the manual command
prints a well-formed message.

### 21.2 Slice: Ownership Self-Validation

**Build:** a method on `SMEAgentBase` (used by every agent on itself, not
a separate service) that checks any `codepaths` it's about to cite in a
`Finding`/`ImpactResponse` against its own declared `OWNS` list before
sending it out

**Unit tests:**
- `test_citation_matching_an_owned_exact_path_passes`
- `test_citation_matching_an_owned_prefix_path_passes`
- `test_citation_outside_owned_paths_is_flagged_needs_verification`

**Integration tests:**
- `test_validator_against_consumer_team_runbook_fixture` — load the real
  `OWNS` list from `consumer-team`'s manifest, confirm a citation of
  `GroupCoordinator.scala` passes while a citation of
  `OffsetClampingService.java` (which belongs to `kora-global`) is
  correctly flagged

**Definition of done for Phase 2:** the proto compiles and round-trips
correctly, and every agent correctly self-flags citations outside its own
ownership using the real runbooks as ground truth.

## 22. Phase 3 — Base Agent Framework

### 22.1 Slice: `SMEAgentBase`, the `@tool` Decorator, and Both Workflows

**Build:** `agents/base_agent.py` implementing:
- `OwnTicket(ticket)` — the workflow used when this agent owns the ticket:
  investigate with its own tools, decide who else to consult via the
  Knowledge Graph, send `ImpactRequest`s, assemble the final result
- `ConsultAbout(request)` — the workflow used when another agent consults
  this one: run relevant tools, decide (on its own) whether it needs to
  consult a third agent, respond

**Unit tests:**
- `test_tool_decorator_registers_method_under_given_name`
- `test_own_ticket_calls_investigate_before_deciding_who_to_consult`
- `test_own_ticket_only_consults_agents_returned_by_the_knowledge_graph_lookup`
- `test_consult_about_can_itself_trigger_a_further_consult_about_call`
- `test_consultation_depth_is_bounded_to_avoid_infinite_chains` — construct
  a pathological fixture where two mocked agents keep consulting each
  other and assert it stops instead of running forever

**Integration tests:**
- `test_own_ticket_end_to_end_against_two_mocked_peer_agents` — mock
  `consumer-team` and `oss-kafka` to return the exact canned responses from
  Part V, run `kora_global_agent.OwnTicket()` for real, and assert the
  resulting plan matches §11's `execution_order` and `approvals_needed`

**Manual validation:**
```bash
python -m agents.kora_global_agent --own-ticket fixtures/consumer_groups_ticket.json
# expect the full consultation sequence to run against mocked peers and
# print a final plan matching Part V
```

**Definition of done for Phase 3:** the integration test above is green —
this is the mocked version of the entire Part V walkthrough passing as an
automated test before a single *real* peer agent exists.

## 23. Phase 4 — Ticket Router (Thin Intake)

**Build:** `router/main.py` — reads the team assignment off an incoming
ticket and forwards it via gRPC to that team's agent's `OwnTicket()`
method; maintains a simple static or config-driven `team_name → grpc_addr`
mapping

**Unit tests:**
- `test_router_extracts_team_field_from_normalized_ticket`
- `test_router_maps_team_name_to_correct_agent_address`
- `test_router_returns_a_clear_error_for_an_unknown_team`

**Integration tests:**
- `test_router_forwards_ticket_and_streams_agent_progress_back_to_caller`

**Manual validation:**
```bash
curl -N -X POST http://localhost:8080/tickets \
  -H 'content-type: application/json' \
  -d '{"team": "kora-global", "title": "clampOffsets is slow", ...}'
# expect to see kora-global's progress stream, not a classification step
```

**Definition of done for Phase 4:** a ticket explicitly assigned to
`kora-global` reaches the `kora-global` agent and nothing else, with zero
domain-classification logic in the router itself.

## 24. Phase 5 — First Three SME Agents (Real Tools, Real Data)

Each of the three agents gets the same test structure, using its own
runbook as the tool specification.

### 24.1 `kora-global` agent

**Build:** `agents/kora_global_agent.py` implementing the four tools from
[`runbooks/kora-global-sme.md`](./runbooks/kora-global-sme.md)

**Unit tests** (MCP calls mocked):
- `test_get_failover_latency_returns_the_documented_json_shape`
- `test_get_offset_clamp_trace_returns_phase_breakdown_summing_to_total_ms`
- `test_own_ticket_produces_an_impact_request_addressed_to_consumer_team`

**Integration tests** (against a real running MCP server):
- `test_kora_global_tools_successfully_call_real_mcp_endpoints_and_parse_responses`

### 24.2 `consumer-team` agent

**Build:** `agents/consumer_team_agent.py` implementing the five tools
from [`runbooks/consumer-team-sme.md`](./runbooks/consumer-team-sme.md)

**Unit tests:**
- `test_estimate_index_memory_cost_matches_the_documented_formula`
- `test_consult_about_recognizes_it_needs_oss_kafka_and_issues_a_further_request`

**Integration tests:**
- `test_consult_about_responds_correctly_to_a_kora_global_impact_request` —
  send the exact `ImpactRequest` JSON from §8, assert the response
  contains `open_questions: ["needs a new Kafka API version — ask oss-kafka"]`
  **and** that a follow-up `ImpactRequest` to `oss-kafka` was actually sent

### 24.3 `oss-kafka` agent

**Build:** `agents/oss_kafka_agent.py` implementing the four tools from
[`runbooks/oss-kafka-sme.md`](./runbooks/oss-kafka-sme.md)

**Unit tests:**
- `test_search_kips_ranks_kip_518_as_closest_precedent_for_the_fixture_query`
- `test_check_compat_flags_the_kip_848_compatibility_note`

**Definition of done for Phase 5:** each agent, run standalone against a
locally running MCP server, produces output matching — in substance, not
word for word — the corresponding agent's step in the Part V walkthrough.

## 25. Phase 6 — Full Multi-Agent Integration Test (No Mocks)

The single most important test in the whole plan: it replays Part V for
real, against real running services, with no mocked agents anywhere, and
with `kora-global` genuinely driving the ticket itself.

**Build:** `tests/integration/test_consumer_groups_per_topic_e2e.py`

**Setup:**
```bash
docker compose up -d   # router, all 3 real agents, real MCP server,
                       # real Knowledge Graph seeded from the 3 runbooks
```

**Test steps:**
1. `POST` the exact ticket from Part V to the API Gateway, with
   `team: kora-global` explicitly set
2. Confirm the router sends it straight to `kora-global` and nowhere else
3. Collect the stream of cross-agent messages that `kora-global` triggers
   on its own — assert `consumer-team` and then `oss-kafka` appear, in
   that causal order, without either of them being pre-selected by
   anything other than `kora-global`'s own Knowledge Graph lookup and
   `consumer-team`'s own follow-up decision
4. Assert the final plan `kora-global` produces has exactly the five
   `execution_order` steps from §11, in the same order
5. Assert `approvals_needed` contains `consumer-team lead` and
   `oss-kafka committer`, and `requires_human == true`
6. Assert every codepath cited across every message in the run passes
   each agent's own ownership self-validation (§21.2) — zero unflagged
   out-of-bounds citations

**Definition of done for Phase 6:** a single command,
`pytest tests/integration/test_consumer_groups_per_topic_e2e.py -v`,
passes against the live `docker compose` stack, with `kora-global`
genuinely driving its own ticket and genuinely deciding — not being told —
who else to involve. From this point forward, this test is the permanent
regression guard for the whole system.

## 26. Phase 7 — API Gateway

### 26.1 Slice: Ticket Normalization

**Build:** `router/main.py` — endpoints that accept GitHub, Jira, or raw
JSON payloads and normalize them into the `Ticket` proto, preserving
whichever team field the source system provides

**Unit tests:**
- `test_normalize_github_issue_webhook_payload_preserves_team_label`
- `test_normalize_jira_webhook_payload_preserves_team_field`
- `test_malformed_payload_returns_400_with_a_clear_error_message`
- `test_payload_missing_a_team_assignment_returns_a_clear_error` — this
  system has no fallback classifier, so a ticket with no team is a
  configuration error, not something to guess about

### 26.2 Slice: Streaming Response

**Integration tests:**
- `test_client_receives_partial_progress_before_the_final_plan`

**Manual validation:**
```bash
curl -N -X POST http://localhost:8080/tickets \
  -H 'content-type: application/json' \
  -d @fixtures/consumer_groups_ticket.json
```

**Definition of done for Phase 7:** the manual `curl` command visibly
streams `kora-global`'s progress rather than blocking silently, and a
ticket without a team assignment is rejected rather than silently guessed.

## 27. Phase 8 — Hypothesis Validation Harness

This phase tests the actual product hypothesis from §4, not code
correctness. There is no unit test for a hypothesis — instead, this phase
builds a small evaluation harness and a scoring report.

**Build:**
- `eval/tickets/` — 10–20 real or realistic tickets, each already assigned
  to an owning team the way a real ticket would be, with a human-labeled
  ground truth of which *other* teams should actually get consulted
- `eval/run_comparison.py` — runs every ticket through (a) the owning
  agent driving itself, consulting peers as it decides to, and (b) a
  single large-context-window agent given the same ticket and full
  repository access
- `eval/scoring.py` — scores both on: recall of teams that should have
  been consulted, precision (catches over-escalation), time to answer,
  and confidence calibration

**Checks for this phase:**
- `test_eval_harness_runs_all_fixture_tickets_without_crashing`
- `test_multiagent_recall_does_not_regress_below_baseline_run` — once a
  first baseline number exists, CI fails if a later change drops recall
  below it

**Definition of done for Phase 8:** `eval/results/report.md` shows,
ticket by ticket, whether letting the owning agent consult peers on its
own initiative caught cross-team dependencies that the single-agent
baseline missed. **This report is the actual decision gate** for whether
Phase 2 of the rollout (§13) is worth building at all.

## 28. Test Pyramid Summary

| Phase | Unit tests | Integration tests | Special |
|---|---|---|---|
| 1 — Knowledge Graph | ~12 | ~6 | — |
| 2 — Typed Protocol | ~6 | ~3 | — |
| 3 — Base Agent Framework | ~5 | ~1 | — |
| 4 — Ticket Router | ~3 | ~1 | — |
| 5 — Three SME Agents | ~12 (4 per agent) | ~6 (2 per agent) | — |
| 6 — Full Integration | 0 | 1 (but the most important one) | — |
| 7 — API Gateway | ~4 | ~1 | — |
| 8 — Hypothesis Validation | ~2 | — | Evaluation harness + scoring report |

Most of the volume is unit tests on individual tools and the ownership
self-validation logic (cheap, fast, run on every save); a smaller number
of integration tests confirm the real infrastructure wiring (Postgres,
gRPC, MCP); and there is exactly **one** load-bearing end-to-end test
(Phase 6) that proves an agent can actually drive its own ticket through a
real, undirected, multi-hop consultation — everything after that treats
it as a non-negotiable regression guard.

## 29. CI Gate Checklist

Before merging any change that touches a given phase's code:

- [ ] Every unit test for that phase passes
- [ ] Every integration test for that phase passes
- [ ] The Phase 6 end-to-end test (§25) still passes, once it exists
- [ ] Every agent's ownership self-validation reports zero new
      `needs_verification` flags introduced by the change
- [ ] If the change touches an agent's tools, that agent's runbook in
      `runbooks/` is updated to match — the runbook and the code must
      never drift apart, since it's the source of truth the Knowledge
      Graph and every other agent rely on

---

# Part X — Model Selection and Cost

## 30. Why Agents Pick Their Own Model

A network of specialized agents deliberating over three rounds could easily
become the most expensive way ever devised to answer a bug report. Three agents
× three rounds × a frontier model per call is how you end up paying more per
ticket than the engineer would have cost. So model choice is a first-class
architectural concern here, not a configuration detail.

The thing that makes this tractable is a property of the design we already
have: **an SME agent's tools are deterministic API calls, not model calls.**
When the consumer-team agent computes the memory cost of a topic→group index,
that is arithmetic over real cluster data. When the oss-kafka agent checks
whether `ListGroupsRequest` v6 is wire-compatible, it reads an actual schema
file. None of that consumes a token, and none of it can be hallucinated.

The model is only needed for judgment:

| Work | Needs a model? | Tier |
|---|---|---|
| Running a tool (memory estimate, KIP search, schema diff) | No — deterministic | — |
| Deciding which tool to call | Yes, barely | `nano` |
| Summarizing tool output into a verdict | Yes | `small` |
| Writing a consultation response | Yes | `standard` |
| Authoring design alternatives, arguing against a peer's proposal | Yes, and quality shows | `deep` |

Because the expensive tier is also the rarest, total spend stays small while
the calls that actually determine output quality still get a capable model.

## 31. Tiers, Not Model Names

Agents declare a *capability tier*. They never name a model:

```python
class OssKafkaAgent(SMEAgentBase):
    LLM_TIER = "deep"          # wire-compat reasoning is the riskiest call here

class ConsumerTeamAgent(SMEAgentBase):
    LLM_TIER = "small"         # routine work is arithmetic over tool output
    # LLM_DESIGN_TIER = "deep" is inherited — design review escalates per call
```

`llm/router.py` resolves a tier to the cheapest model available in the current
environment. Three consequences worth stating:

1. **Model choice becomes a deployment decision.** Swapping the whole org onto
   a different provider is an environment change, not a code change.
2. **Agents are honest about what they need.** `oss-kafka` is on `deep`
   because shipping a breaking protocol change to every Kafka client is the
   worst failure mode in the system. `consumer-team` is on `small` because its
   routine output is a memory calculation. That asymmetry is deliberate and
   reviewable.
3. **Cheap by default, strong where it pays.** `LLM_DESIGN_TIER` lets a
   `small`-tier agent escalate to `deep` for the single design-review call
   that justifies the cost, rather than paying for depth on every call.

## 32. Running at Zero Cost

The default configuration costs nothing, because there isn't one. With no keys
and no local model, the router resolves to `NullLLM`, every agent falls back to
its deterministic authored content, and the full deliberation still runs and
still produces a complete 1-pager.

Three ways to run free, in increasing order of quality:

```bash
# 1. Nothing configured — authored content, $0.00, works offline
python -m llm.cli

# 2. Free local models via Ollama — real reasoning, $0.00, no rate limit
brew install ollama && ollama serve &
ollama pull qwen2.5:14b          # nano/small/standard tiers
ollama pull deepseek-r1:32b      # deep tier

# 3. Free hosted tiers — faster and stronger than local
export GROQ_API_KEY=...                  # ~14,400 requests/day free
export GEMINI_API_KEY=... SME_LLM_GEMINI_FREE_TIER=1   # ~1,500 requests/day
```

Paid, for reference: the whole three-agent deliberation on Groq's cheapest
models at each tier costs about **$0.009 per ticket**, or $8.89 per thousand
tickets. Per-ticket LLM cost is not the constraint on this business.

## 33. Cost Controls

All of these are environment variables — no code change, no redeploy:

| Variable | Effect |
|---|---|
| `SME_LLM_PREFER_FREE` | Prefer $0.00 models over cheap paid ones (default on) |
| `SME_LLM_MAX_COST_PER_MTOK` | Hard ceiling; models above it are never selected |
| `SME_LLM_TIER_<agent>` | Override one agent's tier, up or down |
| `SME_LLM_MODEL_<agent>` | Pin one agent to an exact `provider/model` |
| `SME_LLM_DISABLE` | Force authored fallback everywhere |

`SME_LLM_TIER_<agent>` is the interesting one for the hypothesis in §28. It
lets you run the same ticket with the whole org downgraded to `nano` and
measure how much output quality actually degrades. If a 8B model produces an
acceptable 1-pager, that is worth knowing — and if it doesn't, you have
evidence for where model quality genuinely matters rather than an assumption.

`llm/budget.py` enforces a per-ticket ceiling (default $0.50, deliberately far
above the ~$0.009 expected spend so it only trips on a genuine runaway) and
attributes every call to an agent, a tier, and a purpose:

```
Ticket KAFKA-18231: 6 LLM calls, $0.0089 of $0.50 budget
  tokens: 24,000 in (71% cached) / 4,200 out
  by agent:
    oss-kafka            $0.0058
    kora-global          $0.0020
    consumer-team        $0.0010
  by tier:
    deep                 $0.0078
    small                $0.0010
```

## 34. Prompt Caching Is Load-Bearing

Multi-round deliberation resends the same material every round: the agent's
role, its ownership list, its runbook, the ticket. Only the round's question
changes. That is precisely the shape prompt caching rewards, and the effect is
large — DeepSeek bills a cache hit at $0.003/M against $0.15/M for a miss, a
50× difference.

So `build_messages()` in `llm/provider.py` orders prompts to make the prefix
stable: role and ticket context go in the system message, per-round feedback
and the question go in the user message. Rounds 2 and 3 then hit the cache on
the bulk of their input tokens. `TicketBudget.cache_hit_rate` reports whether
this is actually working; a low rate means something is leaking volatile
content into the prefix, and there is a test asserting the system message is
byte-identical across rounds to catch that regression.

## 35. Failure Is Not Allowed to Be Fatal

`AgentLLM.reason()` never raises and never propagates a provider error. It
returns `None`, and `None` means "use your authored content." A provider
outage, a rate limit, a malformed response, or an exhausted budget all degrade
output quality without failing the ticket.

This is the most important invariant in this part of the system, and it is
tested directly: `agents/tests/test_agent_llm_integration.py` runs the same
ticket through the network three ways — with a working model, with no model,
and with a provider that raises on every call — and asserts the resulting
design docs are *structurally identical*. Same alternatives, same
recommendation, same round count, same teams, same human-escalation decision.
Only the prose depth differs.

That property is what keeps the model an upgrade rather than a dependency. The
verdicts, cited codepaths, design alternatives, and the `needs_org_authority`
flag are all derived from deterministic tool output, and LLM enrichment is
explicitly forbidden from overwriting them. A model can make the review read
better; it cannot invent a reason to page a human, and it cannot quietly
replace a tool-grounded verdict with a plausible-sounding wrong one.
