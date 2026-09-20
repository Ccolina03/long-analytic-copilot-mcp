# SME Agent Network

Domain experts that discover who owns the blast radius, skip everyone else,
and converge on a one-page design brief. No central orchestrator.

## Run the demo

```bash
python -m pytest -q

python -m api.main                         # http://127.0.0.1:8080
cd web && npm install && npm run dev       # http://localhost:3000
```

- Open `/` for the branded intro, then **Run live** or **Watch recorded demo**
- Open `/?demo=1` to autoplay the recorded trace (used for video capture)

### Record a video demo

With the UI running on `:3000`:

```bash
cd web
npx playwright install chromium
npm run record-demo
# → demo/sme-network-demo.webm
```

## Jira integration

Point a Jira **Issue created** (or automation) webhook at:

```
POST /api/webhooks/jira
```

Example payload:

```json
{
  "issue": {
    "key": "KAFKA-18231",
    "self": "https://issues.apache.org/jira/rest/api/2/issue/1",
    "fields": {
      "summary": "Group discovery is slow",
      "description": "…",
      "priority": { "name": "High" },
      "labels": ["mirrormaker", "performance"],
      "components": [{ "name": "mirrormaker" }]
    }
  }
}
```

Labels/components map onto an SME team (`mirrormaker`, `group-coordinator`,
`kafka-broker`, `kafka-clients`, `kafka-security`). Override with `"team"` in
the payload if needed.

## What you are looking at

1. **Intake** — Jira ticket lands on the owning SME  
2. **Discovery** — consult the blast radius, skip the rest  
3. **Deliberation** — peers answer across the mesh  
4. **1-pager** — decision brief with scoreline (consulted / skipped / rounds)

## Deploy

| Piece | Suggested free host |
|-------|---------------------|
| `web/` Next.js | Vercel |
| `api/` FastAPI | Render / Fly / Railway |

Set the Next.js rewrite target (`web/next.config.ts`) to your API URL in
production.

## Layout

```
agents/          SME agents, discovery, tracing
api/             FastAPI + Jira webhook
web/             Next.js mesh UI
demo/            recorded demo video (generated)
proto/           Ticket, Finding, ImpactRequest/Response
runbooks/        architecture + CODEOWNERS (not ticket solutions)
knowledge_graph/ ownership graph
llm/             cheapest-model router
router/          dumb team → agent intake
examples/        last rendered 1-pager
ARCHITECTURE.md  the design paper
```
