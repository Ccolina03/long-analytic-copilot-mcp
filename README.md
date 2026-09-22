# SME Agent Network

Specialized engineering agents that pick up a ticket, figure out which teams are actually affected, skip the ones that aren’t, and write a short design decision — without a central orchestrator.

**Idea:** when something like a Kafka performance ticket lands, the owning SME investigates, discovers the blast radius across peer teams, deliberates with them, and lands a 1-pager. Humans still decide; the agents do the routing and the draft.

**Value:** less time spent pinging the wrong people, clearer ownership, and a decision brief you can review instead of a long chat thread.

## Demo

**[▶ Watch the demo (video)](https://github.com/Ccolina03/sme-agent-network/blob/main/log-analytics-copilot/demo/sme-network-demo.webm)**

~80s cut with beat captions, 3× mesh replay, and background music
(“Calm Loop / Relaxing” by wipics, [CC0](https://opengameart.org/content/calm-loop)).

Or run it locally:

```bash
cd log-analytics-copilot
python -m api.main                    # API on :8080
cd web && npm install && npm run dev  # UI on :3000
```

Open http://localhost:3000 → **Watch recorded demo** (or **Run live**).

## How it works (short)

1. **Jira intake** — ticket routes to the owning SME  
2. **Discovery** — consult who owns the blast radius, skip everyone else  
3. **Deliberation** — peers answer across the mesh  
4. **1-pager** — decision brief with who was consulted and who was skipped  

Jira webhook: `POST /api/webhooks/jira` (details in [`log-analytics-copilot/README.md`](log-analytics-copilot/README.md)).

## Tech stack

| Layer | What |
|-------|------|
| **API gateway** | FastAPI — ticket intake, Jira webhook normalize, health/directory, SSE/trace snapshots |
| **Ticket router** | Dumb team → owning SME handoff (no LLM classifier in the middle) |
| **Agent mesh** | Peer-to-peer SME agents; typed `ImpactRequest` / `ImpactResponse` protocol (`proto/`) |
| **Transport** | In-process `DirectTransport` today; same shapes as a gRPC peer channel |
| **Discovery** | Impact signals → consult / skip via `agents/discovery.py` + team directory |
| **Ownership** | Runbooks + CODEOWNERS; knowledge graph ingest/query (Postgres-ready) |
| **LLM** | Cheapest-model router + per-ticket budget (`llm/`) |
| **UI** | Next.js 15 App Router, TypeScript — mesh floor, cinematic demo replay |
| **Integrations** | Jira issue webhooks; recorded demo + live run paths |
| **Tests** | pytest (~558) across agents, API, router, knowledge graph, e2e |

## Highlights (resume-ready)

- Built a **peer-to-peer SME agent mesh** (no central orchestrator) where the owning agent drives investigation, discovery, multi-round deliberation, and a design **1-pager**
- Implemented **impact-signal discovery** that consults only blast-radius owners and **skips ~50% of peers** with recorded reasons (demo: 4 consult / 4 skip) instead of a hardcoded peer list
- Shipped a **FastAPI API gateway** that normalizes **Jira webhooks** into tickets, routes by owning team, and exposes run traces for a **Next.js** mesh UI
- Modeled cross-team asks as a typed **gRPC-shaped protocol** (`ImpactRequest`/`ImpactResponse`) over `DirectTransport`, with runbooks + CODEOWNERS as ownership source of truth

## Project layout

Code lives under [`log-analytics-copilot/`](log-analytics-copilot/).
