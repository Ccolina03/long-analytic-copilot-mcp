"""
FastAPI MCP server exposing the Log Analytics Copilot tools.

Each route under ``/tools/*`` corresponds to a tool the LLM agent can call.
The ``/mcp/manifest`` endpoint advertises the tool catalog in a shape that an
MCP-aware client can consume directly.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from tools import (
    optimize_table,
    pipeline_status,
    query_logs,
    search_keyword,
    top_errors,
)


app = FastAPI(
    title="Log Analytics Copilot — MCP Server",
    version="0.1.0",
    description="Control plane exposing query/search/health/optimize tools over Delta logs.",
)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class QueryLogsRequest(BaseModel):
    sql: str = Field(..., description="Read-only SELECT against allowed Delta tables.")


class TopErrorsRequest(BaseModel):
    service: str
    last_minutes: int = 30
    limit: int = 10


class SearchKeywordRequest(BaseModel):
    keyword: str
    last_minutes: int = 60
    service: str | None = None
    limit: int = 50


class OptimizeTableRequest(BaseModel):
    service: str | None = None
    target: str = Field("silver", description="bronze | silver | gold | tokens")


# ---------------------------------------------------------------------------
# Tool catalog (MCP manifest)
# ---------------------------------------------------------------------------


TOOL_MANIFEST: list[dict[str, Any]] = [
    {
        "name": "query_logs",
        "description": "Run a read-only SQL SELECT against the Delta log tables.",
        "input_schema": QueryLogsRequest.model_json_schema(),
        "endpoint": "/tools/query_logs",
    },
    {
        "name": "top_errors",
        "description": "Return the most frequent ERROR messages for a service in the last N minutes.",
        "input_schema": TopErrorsRequest.model_json_schema(),
        "endpoint": "/tools/top_errors",
    },
    {
        "name": "search_keyword",
        "description": "Keyword search across logs using the token index, joined back to silver_logs.",
        "input_schema": SearchKeywordRequest.model_json_schema(),
        "endpoint": "/tools/search_keyword",
    },
    {
        "name": "pipeline_status",
        "description": "Health snapshot of the ingestion + Delta pipeline.",
        "input_schema": {"type": "object", "properties": {}},
        "endpoint": "/tools/pipeline_status",
    },
    {
        "name": "optimize_table",
        "description": "Compact a Delta table to fix the small-file problem; optionally scoped to one service.",
        "input_schema": OptimizeTableRequest.model_json_schema(),
        "endpoint": "/tools/optimize_table",
    },
]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/mcp/manifest")
def manifest() -> dict[str, Any]:
    return {"tools": TOOL_MANIFEST}


def _safe_call(fn, *args, **kwargs) -> JSONResponse:
    try:
        return JSONResponse(fn(*args, **kwargs))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"tool error: {exc}") from exc


@app.post("/tools/query_logs")
def tool_query_logs(req: QueryLogsRequest) -> JSONResponse:
    return _safe_call(query_logs, req.sql)


@app.post("/tools/top_errors")
def tool_top_errors(req: TopErrorsRequest) -> JSONResponse:
    return _safe_call(top_errors, req.service, req.last_minutes, req.limit)


@app.post("/tools/search_keyword")
def tool_search_keyword(req: SearchKeywordRequest) -> JSONResponse:
    return _safe_call(
        search_keyword, req.keyword, req.last_minutes, req.service, req.limit
    )


@app.get("/tools/pipeline_status")
def tool_pipeline_status() -> JSONResponse:
    return _safe_call(pipeline_status)


@app.post("/tools/optimize_table")
def tool_optimize_table(req: OptimizeTableRequest) -> JSONResponse:
    return _safe_call(optimize_table, req.service, req.target)
