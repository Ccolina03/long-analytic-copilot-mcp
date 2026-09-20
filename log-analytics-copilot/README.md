# SME Agent Network

Specialized engineering agents that pick up a ticket, figure out which teams are actually affected, skip the ones that aren’t, and write a short design decision — without a central orchestrator.

**Idea:** when something like a Kafka performance ticket lands, the owning SME investigates, discovers the blast radius across peer teams, deliberates with them, and lands a 1-pager. Humans still decide; the agents do the routing and the draft.

**Value:** less time spent pinging the wrong people, clearer ownership, and a decision brief you can review instead of a long chat thread.

## Demo

**[▶ Watch the demo (video)](https://github.com/Ccolina03/sme-agent-network/blob/main/log-analytics-copilot/demo/sme-network-demo.webm)**

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
agents/   SME agents, discovery, tracing
api/      FastAPI + Jira webhook
web/      mesh UI
demo/     recorded walkthrough video
runbooks/ architecture + CODEOWNERS
```
