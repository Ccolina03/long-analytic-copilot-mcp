"""HTTP API the web UI talks to.

Free-tier shape
---------------
The UI is a Next.js app (Vercel free). This API is a small FastAPI process
(Render / Fly / Railway free, or localhost). They only exchange JSON and
SSE — no shared database.

    POST /api/tickets           start a ticket (returns id immediately)
    POST /api/demo              start the canned MirrorMaker demo ticket
    POST /api/webhooks/jira     Jira issue webhook → start a ticket run
    GET  /api/tickets/{id}      snapshot: events so far + finding if done
    GET  /api/tickets/{id}/stream   SSE of TraceEvents as they happen
    GET  /api/directory         the org directory discovery uses
    GET  /api/health
"""

from __future__ import annotations

import json
import pathlib
import queue
import threading
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from agents.design_doc import render_one_pager
from agents.discovery import DIRECTORY
from agents.network import IMPLEMENTED, build_network
from agents.trace import Tracer
from proto.sme_agents import Ticket

app = FastAPI(title="SME Agent Network", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_FIXTURE = (
    pathlib.Path(__file__).resolve().parent.parent
    / "tests/integration/fixtures/group_discovery_ticket.json"
)

_lock = threading.Lock()
_runs: dict[str, dict[str, Any]] = {}


def _new_run(ticket: Ticket) -> dict[str, Any]:
    run = {
        "id": ticket.ticket_id,
        "status": "running",
        "ticket": ticket.to_dict(),
        "events": [],
        "trace": None,
        "finding": None,
        "doc": None,
        "error": None,
        "_queue": queue.Queue(),
    }
    with _lock:
        _runs[ticket.ticket_id] = run
    return run


def _execute(run: dict[str, Any]) -> None:
    ticket_data = run["ticket"]
    ticket = Ticket.from_dict(ticket_data)
    tracer = Tracer(ticket_id=ticket.ticket_id, title=ticket.title)

    def _push(event):
        payload = event.to_dict()
        run["events"].append(payload)
        run["_queue"].put(payload)

    tracer.on_event(_push)

    try:
        network = build_network(tracer=tracer)
        owner = network.get(ticket.team)
        if owner is None:
            raise ValueError(
                f"no implemented agent for team {ticket.team!r}; "
                f"known: {', '.join(IMPLEMENTED)}"
            )
        finding = owner.own_ticket(ticket)
        run["finding"] = finding.to_dict()
        run["doc"] = render_one_pager(finding)
        run["trace"] = tracer.to_dict()
        run["status"] = "done"
    except Exception as exc:  # noqa: BLE001 — surface to the UI, don't crash the worker
        run["status"] = "error"
        run["error"] = str(exc)
    finally:
        run["_queue"].put(None)


@app.get("/api/health")
def health():
    return {"status": "ok", "implemented": list(IMPLEMENTED)}


@app.get("/api/directory")
def directory():
    return {
        "teams": [
            {
                "agent_id": c.agent_id,
                "domain": c.domain,
                "owns": list(c.owns),
                "answers": list(c.answers),
                "implemented": c.implemented,
            }
            for c in DIRECTORY
        ]
    }


@app.get("/api/tickets")
def list_tickets():
    with _lock:
        return [
            {
                "id": r["id"],
                "status": r["status"],
                "title": r["ticket"].get("title", ""),
                "team": r["ticket"].get("team", ""),
                "event_count": len(r["events"]),
            }
            for r in _runs.values()
        ]


@app.post("/api/tickets")
def start_ticket(payload: dict[str, Any]):
    if not payload.get("team"):
        raise HTTPException(status_code=400, detail="team is required")
    if "ticket_id" not in payload:
        payload = dict(payload)
        payload["ticket_id"] = str(uuid.uuid4())
    ticket = Ticket.from_dict(payload)
    run = _new_run(ticket)
    threading.Thread(target=_execute, args=(run,), daemon=True).start()
    return {"id": ticket.ticket_id, "status": "running"}


@app.post("/api/demo")
def start_demo():
    raw = json.loads(_FIXTURE.read_text())
    raw["ticket_id"] = str(uuid.uuid4())
    return start_ticket(raw)


def _team_from_jira_fields(fields: dict[str, Any]) -> str:
    """Map Jira components / labels onto an implemented SME team."""
    labels = {str(x).lower() for x in (fields.get("labels") or [])}
    components = {
        str(c.get("name", "")).lower()
        for c in (fields.get("components") or [])
        if isinstance(c, dict)
    }
    blob = labels | components
    mapping = [
        ("mirrormaker", ("mirrormaker", "mm2", "mirror")),
        ("group-coordinator", ("group-coordinator", "consumer-groups", "coordinator")),
        ("kafka-broker", ("broker", "kafka-broker")),
        ("kafka-clients", ("clients", "kafka-clients", "kip")),
        ("kafka-security", ("security", "acl", "auth")),
    ]
    for team, keys in mapping:
        if any(k in blob for k in keys):
            return team
    # Custom field override used in webhook docs / automation rules.
    custom = fields.get("sme_team") or fields.get("customfield_sme_team")
    if isinstance(custom, str) and custom in IMPLEMENTED:
        return custom
    return "mirrormaker"


@app.post("/api/webhooks/jira")
def jira_webhook(payload: dict[str, Any]):
    """Accept a Jira issue webhook (or simplified payload) and start a run.

    Compatible with Atlassian "Issue created" webhooks. Minimal test shape::

        {
          "issue": {
            "key": "KAFKA-18231",
            "self": "https://issues.apache.org/jira/rest/api/2/issue/123",
            "fields": {
              "summary": "...",
              "description": "...",
              "priority": {"name": "High"},
              "labels": ["mirrormaker", "performance"],
              "components": [{"name": "mirrormaker"}]
            }
          }
        }
    """
    issue = payload.get("issue") if isinstance(payload.get("issue"), dict) else payload
    fields = issue.get("fields") if isinstance(issue.get("fields"), dict) else {}
    if not fields and not issue.get("summary") and not payload.get("title"):
        raise HTTPException(status_code=400, detail="expected Jira issue payload")

    key = str(issue.get("key") or payload.get("key") or uuid.uuid4())
    summary = str(
        fields.get("summary")
        or issue.get("summary")
        or payload.get("title")
        or key
    )
    description = str(
        fields.get("description")
        or issue.get("description")
        or payload.get("description")
        or ""
    )
    priority_raw = fields.get("priority") or payload.get("priority") or "medium"
    if isinstance(priority_raw, dict):
        priority = str(priority_raw.get("name", "medium")).lower()
    else:
        priority = str(priority_raw).lower()

    source_url = str(
        issue.get("self")
        or payload.get("source_url")
        or f"https://jira.example/browse/{key}"
    )
    # Prefer browse URL when we only have a REST self link.
    if "/rest/api/" in source_url and key:
        base = source_url.split("/rest/api/")[0]
        source_url = f"{base}/browse/{key}"

    team = str(payload.get("team") or _team_from_jira_fields(fields))
    labels = fields.get("labels") or payload.get("labels") or []
    if not isinstance(labels, list):
        labels = [str(labels)]

    ticket_payload = {
        "ticket_id": str(uuid.uuid4()),
        "team": team,
        "title": summary,
        "description": description,
        "priority": priority,
        "labels": [str(x) for x in labels],
        "source": "jira",
        "source_url": source_url,
    }
    return start_ticket(ticket_payload)


@app.get("/api/tickets/{ticket_id}")
def get_ticket(ticket_id: str):
    run = _runs.get(ticket_id)
    if run is None:
        raise HTTPException(status_code=404, detail="unknown ticket")
    return {
        "id": run["id"],
        "status": run["status"],
        "ticket": run["ticket"],
        "events": run["events"],
        "trace": run["trace"],
        "finding": run["finding"],
        "doc": run["doc"],
        "error": run["error"],
    }


@app.get("/api/tickets/{ticket_id}/stream")
def stream_ticket(ticket_id: str):
    run = _runs.get(ticket_id)
    if run is None:
        raise HTTPException(status_code=404, detail="unknown ticket")

    def gen():
        # Replay anything already recorded, then follow the queue.
        seen = 0
        while True:
            while seen < len(run["events"]):
                yield _sse(run["events"][seen])
                seen += 1
            item = run["_queue"].get()
            if item is None:
                yield "event: done\ndata: {}\n\n"
                break
            # The listener already appended; skip if we raced the snapshot.
            if seen < len(run["events"]) and run["events"][seen] == item:
                yield _sse(item)
                seen += 1
            elif item not in run["events"][:seen]:
                yield _sse(item)

    return StreamingResponse(gen(), media_type="text/event-stream")


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def main() -> None:
    import uvicorn
    uvicorn.run("api.main:app", host="0.0.0.0", port=8080, reload=True)


if __name__ == "__main__":
    main()
