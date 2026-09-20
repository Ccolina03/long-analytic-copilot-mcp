"""
Ticket Router — Phase 4 / Slice 23.

Deliberately dumb: reads the ``team`` field off an incoming ticket JSON
payload and forwards it to that team's agent.  Zero domain-classification
logic lives here — all judgment lives in the owning agent.

Endpoints
---------
  POST /tickets          — accept a ticket, route to the owning agent
  GET  /health           — liveness probe

Team → agent address mapping
-----------------------------
Configured via the ``AGENT_ADDRESSES`` dict (overrideable for tests).
In production this would be loaded from a config file or service registry.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from proto.sme_agents import Ticket

app = FastAPI(title="SME Agent Ticket Router", version="0.1.0")

# ---------------------------------------------------------------------------
# Team → agent address mapping
# ---------------------------------------------------------------------------

# Default addresses — override via env vars for local dev / test
_DEFAULT_ADDRESSES: dict[str, str] = {
    "kora-global":    os.environ.get("AGENT_ADDR_KORA",     "http://kora-global-agent:8001"),
    "consumer-team":  os.environ.get("AGENT_ADDR_CONSUMER", "http://consumer-team-agent:8002"),
    "oss-kafka":      os.environ.get("AGENT_ADDR_OSS",      "http://oss-kafka-agent:8003"),
    "broker-team":    os.environ.get("AGENT_ADDR_BROKER",   "http://broker-team-agent:8004"),
}

# Mutable reference so tests can swap it without restarting the app
AGENT_ADDRESSES: dict[str, str] = dict(_DEFAULT_ADDRESSES)


def get_agent_address(team: str) -> str | None:
    """Return the gRPC/HTTP address for ``team``, or None if unknown."""
    return AGENT_ADDRESSES.get(team)


def normalize_ticket(payload: dict[str, Any]) -> Ticket:
    """Parse and normalise a raw JSON payload into a Ticket.

    Accepts GitHub issue webhook, Jira webhook, or raw Ticket JSON.
    All formats must have a ``team`` field (or a label that sets it).

    Raises:
        KeyError if ``team`` is missing or empty.
        ValueError if the payload is otherwise malformed.
    """
    # Normalise GitHub issue webhook format
    if "issue" in payload and "labels" in payload.get("issue", {}):
        issue = payload["issue"]
        team = _extract_team_from_labels(issue.get("labels", []))
        if not team:
            raise KeyError("team")
        return Ticket.from_dict({
            "team": team,
            "title": issue.get("title", ""),
            "description": issue.get("body", ""),
            "source": "github",
            "source_url": issue.get("html_url", ""),
            "labels": [lbl["name"] for lbl in issue.get("labels", [])],
        })

    # Normalise Jira webhook format
    if "issue" in payload and "fields" in payload.get("issue", {}):
        fields = payload["issue"]["fields"]
        team = (
            fields.get("team", {}).get("name", "")
            or fields.get("customfield_team", "")
        )
        if not team:
            raise KeyError("team")
        return Ticket.from_dict({
            "team": team,
            "title": fields.get("summary", ""),
            "description": fields.get("description", ""),
            "source": "jira",
            "source_url": payload["issue"].get("self", ""),
            "priority": fields.get("priority", {}).get("name", "medium").lower(),
        })

    # Raw Ticket JSON (direct API calls, tests)
    if "team" not in payload or not payload.get("team"):
        raise KeyError("team")
    return Ticket.from_dict(payload)


def _extract_team_from_labels(labels: list[dict]) -> str:
    """Find a label named ``team: <name>`` or ``team/<name>`` in a label list."""
    for lbl in labels:
        name: str = lbl.get("name", "")
        if name.startswith("team:"):
            return name[5:].strip()
        if name.startswith("team/"):
            return name[5:].strip()
    return ""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "known_teams": sorted(AGENT_ADDRESSES.keys())}


@app.post("/tickets")
async def submit_ticket(payload: dict[str, Any]) -> JSONResponse:
    """Accept a ticket payload and route it to the owning team's agent.

    Returns a routing confirmation immediately; the agent runs asynchronously.
    A ticket with no ``team`` field is rejected with HTTP 400 — this system
    has no classifier and treats a missing team as a configuration error.
    """
    # Normalise
    try:
        ticket = normalize_ticket(payload)
    except KeyError:
        raise HTTPException(
            status_code=400,
            detail=(
                "ticket must have a 'team' field. "
                "Assign it to a team before submitting — this router has no classifier."
            ),
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"malformed payload: {exc}")

    # Route
    agent_address = get_agent_address(ticket.team)
    if agent_address is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"unknown team '{ticket.team}'. "
                f"Known teams: {sorted(AGENT_ADDRESSES.keys())}"
            ),
        )

    return JSONResponse(
        status_code=202,
        content={
            "routed_to": ticket.team,
            "ticket_id": ticket.ticket_id,
            "agent_address": agent_address,
            "status": "accepted",
        },
    )
