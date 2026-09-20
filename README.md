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

## Project layout

Code lives under [`log-analytics-copilot/`](log-analytics-copilot/).
