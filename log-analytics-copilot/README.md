# SME Agent Network

Specialized engineering agents that pick up a ticket, figure out which teams are actually affected, skip the ones that aren’t, and write a short design decision — without a central orchestrator.

**Idea:** when something like a Kafka performance ticket lands, the owning SME investigates, discovers the blast radius across peer teams, deliberates with them, and lands a 1-pager. Humans still decide; the agents do the routing and the draft.

**Value:** less time spent pinging the wrong people, clearer ownership, and a decision brief you can review instead of a long chat thread.

## Demo

**[▶ Watch the demo (video)](https://github.com/Ccolina03/sme-agent-network/blob/main/log-analytics-copilot/demo/sme-network-demo.webm)**

~80s cut with beat captions, 3× mesh replay, and background music
(“Calm Loop / Relaxing” by wipics, [CC0](https://opengameart.org/content/calm-loop)).

```bash
python -m pytest -q
python -m api.main                    # http://127.0.0.1:8080
cd web && npm install && npm run dev  # http://localhost:3000
```

Open http://localhost:3000 → **Watch recorded demo** (or **Run live**).

## How it works

1. **Jira intake** — ticket routes to the owning SME  
2. **Discovery** — consult who owns the blast radius, skip everyone else  
3. **Deliberation** — peers answer across the mesh  
4. **1-pager** — decision brief with consulted / skipped  

## Tech stack

**Languages**
- **Python** — agents, API gateway, discovery, knowledge graph, LLM router, tests  
- **TypeScript / React** — Next.js mesh UI  
- **SQL** — knowledge-graph schema (`knowledge_graph/schema.sql`)  
- **Markdown** — per-team runbooks + CODEOWNERS  

**Backend & agents**
- **FastAPI** + **Uvicorn** — API gateway (tickets, Jira webhooks, health, directory, traces)  
- **pytest** — ~558 unit / API / e2e tests  
- Peer **SME agents** — no central orchestrator; owning agent drives the run  

**Protocols & messaging**
- Typed **`ImpactRequest` / `ImpactResponse`** protocol (`proto/`) — gRPC-shaped peer consults  
- **`DirectTransport`** in-process today (same call shapes as a gRPC channel)  
- **SSE / JSON** snapshots from the gateway to the UI  

**Graphs & ownership**
- **Knowledge graph** — team / repo / code-path ownership edges (Postgres-ready)  
- **CODEOWNERS** ingest + query (`knowledge_graph/`)  
- **Discovery directory** — impact signals → consult or skip with a recorded reason  

**Frontend**
- **Next.js 15** (App Router) + **React 19** + **CSS** — live mesh floor + cinematic demo replay  

**Integrations**
- **Jira** issue webhooks → normalized ticket → owning SME  
- Optional **LLM** providers via cheapest-model router + per-ticket budget (`llm/`)  

**Domain**
- Apache **Kafka** SME teams (MirrorMaker, Group Coordinator, Broker, Clients, Security, …)  

## Highlights (resume-ready)

- Built a **peer-to-peer SME agent mesh** (no central orchestrator) where the owning agent drives investigation, discovery, multi-round deliberation, and a design **1-pager**
- Implemented **impact-signal discovery** that consults only blast-radius owners and **skips ~50% of peers** with recorded reasons (demo: 4 consult / 4 skip) instead of a hardcoded peer list
- Shipped a **FastAPI API gateway** that normalizes **Jira webhooks** into tickets, routes by owning team, and exposes run traces for a **Next.js** mesh UI
- Modeled cross-team asks as a typed **gRPC-shaped protocol** (`ImpactRequest`/`ImpactResponse`) over `DirectTransport`, with runbooks + CODEOWNERS as ownership source of truth

## Jira

```
POST /api/webhooks/jira
```

Labels/components map to an SME team (`mirrormaker`, `group-coordinator`, `kafka-broker`, `kafka-clients`, `kafka-security`). See `api/main.py` for the payload shape.

## Deploy

| Piece | Host |
|-------|------|
| `web/` | Vercel |
| `api/` | Render / Fly / Railway |

Set `API_URL` for the Next.js rewrite in `web/next.config.ts`.

## Layout

```
agents/          SME agents, discovery, tracing
api/             FastAPI API gateway + Jira webhook
web/             Next.js mesh UI
proto/           Ticket / Finding / ImpactRequest·Response
knowledge_graph/ ownership ingest + query
llm/             cheapest-model router + budget
router/          team → agent intake
runbooks/        architecture + CODEOWNERS
demo/            recorded walkthrough video
```
