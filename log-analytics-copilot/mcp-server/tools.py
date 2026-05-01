"""
MCP tool implementations for the Log Analytics Copilot.

Each function corresponds 1:1 to a tool exposed by the FastAPI server in
``main.py``. The functions return plain dictionaries / lists so they can be
serialized as JSON and consumed by an LLM-driven agent.

The Spark layer is abstracted behind ``SparkExecutor``. By default a
``StubSparkExecutor`` is used so you can run the server end-to-end before
Spark is configured. Replace it with ``PySparkExecutor`` (Week 2) once the
Delta tables exist.
"""

from __future__ import annotations

import os
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


# Delta table paths. These match the layout described in the architecture doc
# and can be overridden via environment variables for local vs. cluster runs.
DELTA_ROOT = os.environ.get("DELTA_ROOT", "/workspace/delta")
BRONZE_PATH = os.environ.get("DELTA_BRONZE_PATH", f"{DELTA_ROOT}/bronze_logs")
SILVER_PATH = os.environ.get("DELTA_SILVER_PATH", f"{DELTA_ROOT}/silver_logs")
GOLD_PATH = os.environ.get("DELTA_GOLD_PATH", f"{DELTA_ROOT}/gold_error_counts")
TOKENS_PATH = os.environ.get("DELTA_TOKENS_PATH", f"{DELTA_ROOT}/log_tokens")


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    elapsed_ms: float

    def to_json(self) -> dict[str, Any]:
        return {
            "columns": self.columns,
            "rows": self.rows,
            "row_count": len(self.rows),
            "elapsed_ms": round(self.elapsed_ms, 2),
        }


class SparkExecutor(ABC):
    """Abstract executor so we can swap stub <-> real PySpark session."""

    @abstractmethod
    def sql(self, query: str) -> QueryResult: ...

    @abstractmethod
    def optimize(self, table_path: str, where: str | None = None) -> dict[str, Any]: ...

    @abstractmethod
    def health(self) -> dict[str, Any]: ...


class StubSparkExecutor(SparkExecutor):
    """In-memory stub for local development before Spark is wired up.

    It returns deterministic-ish fake rows so the agent can be developed and
    demoed without bringing up the full pipeline.
    """

    def sql(self, query: str) -> QueryResult:
        start = time.perf_counter()
        # Heuristic: pick columns based on the kind of query the agent ran.
        lowered = query.lower()
        if "count" in lowered and "level" in lowered:
            cols = ["service", "level", "count"]
            rows = [
                ["payments-service", "ERROR", 184],
                ["payments-service", "WARN", 902],
                ["auth-service", "ERROR", 47],
            ]
        elif "message" in lowered:
            cols = ["timestamp", "service", "level", "message"]
            rows = [
                ["2026-04-30T15:46:12Z", "payments-service", "ERROR", "stripe charge timeout after 30s"],
                ["2026-04-30T15:46:14Z", "payments-service", "ERROR", "stripe charge timeout after 30s"],
                ["2026-04-30T15:46:18Z", "payments-service", "ERROR", "db connection reset"],
            ]
        else:
            cols = ["service", "events"]
            rows = [["payments-service", 12_034], ["auth-service", 8_211]]
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return QueryResult(columns=cols, rows=rows, elapsed_ms=elapsed_ms)

    def optimize(self, table_path: str, where: str | None = None) -> dict[str, Any]:
        files_before = random.randint(400, 900)
        files_after = max(8, files_before // random.randint(20, 40))
        return {
            "table": table_path,
            "where": where,
            "files_before": files_before,
            "files_after": files_after,
            "bytes_rewritten_mb": round(files_before * 1.7, 1),
            "note": "stub: replace with real DeltaTable.optimize() in Week 3",
        }

    def health(self) -> dict[str, Any]:
        return {
            "executor": "stub",
            "delta_root": DELTA_ROOT,
            "bronze": BRONZE_PATH,
            "silver": SILVER_PATH,
            "gold": GOLD_PATH,
            "tokens": TOKENS_PATH,
        }


# Single module-level executor. Swap to PySparkExecutor in Week 2.
EXECUTOR: SparkExecutor = StubSparkExecutor()


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


# Tables the agent is allowed to query. Anything outside this allowlist is
# rejected to prevent the LLM from exfiltrating or mutating arbitrary tables.
ALLOWED_TABLES = {"bronze_logs", "silver_logs", "gold_error_counts", "log_tokens"}

# Statements that should never appear in agent-issued SQL.
FORBIDDEN_KEYWORDS = (
    "insert ",
    "update ",
    "delete ",
    "drop ",
    "alter ",
    "merge ",
    "create ",
    "truncate ",
    "vacuum ",
)


def _validate_sql(sql: str) -> None:
    lowered = sql.lower().strip()
    if not lowered.startswith("select"):
        raise ValueError("query_logs only supports SELECT statements")
    for kw in FORBIDDEN_KEYWORDS:
        if kw in lowered:
            raise ValueError(f"forbidden keyword in SQL: {kw.strip()}")
    if not any(t in lowered for t in ALLOWED_TABLES):
        raise ValueError(
            "query must reference one of the allowed tables: "
            + ", ".join(sorted(ALLOWED_TABLES))
        )


def query_logs(sql: str) -> dict[str, Any]:
    """Run a read-only SQL query against the Delta tables."""
    _validate_sql(sql)
    return EXECUTOR.sql(sql).to_json()


def top_errors(service: str, last_minutes: int = 30, limit: int = 10) -> dict[str, Any]:
    """Top error messages for ``service`` in the last ``last_minutes`` minutes."""
    if last_minutes <= 0 or last_minutes > 24 * 60:
        raise ValueError("last_minutes must be between 1 and 1440")
    sql = f"""
        SELECT service, level, message, COUNT(*) AS count
        FROM silver_logs
        WHERE service = '{service}'
          AND level = 'ERROR'
          AND timestamp >= current_timestamp() - INTERVAL {int(last_minutes)} MINUTES
        GROUP BY service, level, message
        ORDER BY count DESC
        LIMIT {int(limit)}
    """
    return EXECUTOR.sql(sql).to_json()


def search_keyword(
    keyword: str,
    last_minutes: int = 60,
    service: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Keyword search using the token index, joined back to ``silver_logs``."""
    if not keyword or not keyword.strip():
        raise ValueError("keyword is required")
    token = keyword.strip().lower()
    service_clause = f"AND s.service = '{service}'" if service else ""
    sql = f"""
        SELECT s.timestamp, s.service, s.level, s.message
        FROM log_tokens t
        JOIN silver_logs s
          ON t.event_id = s.event_id
        WHERE t.token = '{token}'
          AND t.timestamp >= current_timestamp() - INTERVAL {int(last_minutes)} MINUTES
          {service_clause}
        ORDER BY s.timestamp DESC
        LIMIT {int(limit)}
    """
    return EXECUTOR.sql(sql).to_json()


def pipeline_status() -> dict[str, Any]:
    """Health snapshot for the data + control plane."""
    return {
        "status": "ok",
        "components": {
            "kafka_topic": "logs.raw",
            "bronze": BRONZE_PATH,
            "silver": SILVER_PATH,
            "gold": GOLD_PATH,
            "tokens": TOKENS_PATH,
        },
        "executor": EXECUTOR.health(),
    }


def optimize_table(service: str | None = None, target: str = "silver") -> dict[str, Any]:
    """Compact a Delta table; optionally scoped to a single ``service`` partition."""
    if target not in {"bronze", "silver", "gold", "tokens"}:
        raise ValueError("target must be one of: bronze, silver, gold, tokens")
    table_path = {
        "bronze": BRONZE_PATH,
        "silver": SILVER_PATH,
        "gold": GOLD_PATH,
        "tokens": TOKENS_PATH,
    }[target]
    where = f"service = '{service}'" if service else None
    return EXECUTOR.optimize(table_path, where=where)
