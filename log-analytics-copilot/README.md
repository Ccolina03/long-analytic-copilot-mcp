# Log Analytics Copilot (MCP Agent)

An "Ask your logs" Copilot aligned with the Databricks Vancouver Log Analytics
problem space: high-volume ingestion, Bronze/Silver/Gold Delta tables, a token
index for keyword search, and an LLM agent that drives the whole thing through
an MCP control plane.

## Problem statement

Modern SaaS platforms produce log streams measured in terabytes per day and,
at scale, petabytes per month. Engineers don't want to write Spark SQL during
an incident — they want to ask "what broke in payments-service in the last 30
minutes?" and get a ranked answer with example log lines and a suggested
mitigation. This project shows the full pipeline behind that experience.

## Architecture

```
loadgen (Go)
   │
   ▼
ingest-gateway (Go gRPC, batching + backpressure)
   │
   ▼
Kafka topic: logs.raw  (partition key = service)
   │
   ▼
Spark Structured Streaming  ──►  Delta Bronze (raw, append-only)
                              ──►  Delta Silver (parsed, dedup, partitioned)
                              ──►  Delta Gold   (errors/min, top messages)

Spark batch job  ──► Delta log_tokens (token → event_id index)
Spark batch job  ──► OPTIMIZE / VACUUM (small-file compaction)

FastAPI MCP server  ── exposes tools ──►  Copilot CLI (LLM agent)
```

## Repository layout

```
log-analytics-copilot/
  proto/             # gRPC schema (logs.proto)
  ingest-gateway/    # Go gRPC server + Kafka producer (Week 1)
  loadgen/           # Go log generator (Week 1)
  spark/             # Streaming + indexing + optimization jobs (Week 2/3)
  mcp-server/        # FastAPI control plane (Week 3, scaffolded)
  copilot/           # LLM-driven CLI agent (Week 3)
  diagrams/          # Architecture image
  docker-compose.yml # Kafka + Zookeeper + Spark + MCP server
  README.md
```

## Data schema

`LogEvent` (see `proto/logs.proto`):

| field      | type   | notes                                  |
|------------|--------|----------------------------------------|
| timestamp  | string | ISO-8601 UTC                            |
| service    | string | partition key                           |
| level      | string | DEBUG / INFO / WARN / ERROR             |
| message    | string | free-form, tokenized for the index      |
| trace_id   | string | groups logs across services             |
| event_id   | string | unique per log line, used for dedup     |
| host       | string | originating host                        |

## Delta Bronze / Silver / Gold

- **bronze_logs** – raw JSON as ingested from Kafka, append-only.
- **silver_logs** – parsed schema, deduped on `(trace_id, event_id)`,
  partitioned by `service`, `date`, `hour`.
- **gold_error_counts** – per-minute error counts per service plus the top
  error messages, used by the UX queries.

## Indexing strategy

The `log_tokens` Delta table maps each lowercase token from `silver_logs.message`
to the originating `event_id` along with `service` and `timestamp`. Keyword
queries become a partition-pruned scan of `log_tokens` joined back to
`silver_logs`, which is dramatically cheaper than full-text scanning the raw
message column.

## Optimization

`spark/optimize_delta.py` (Week 3) compacts small files in `silver_logs` and
`log_tokens`, reporting file count and bytes rewritten before/after. This is
the canonical fix for the small-file problem on Delta.

## How to run locally

### 1. Bring up infrastructure

```bash
docker compose up -d zookeeper kafka spark-master spark-worker
```

### 2. Run the MCP server

The server runs against an in-memory stub executor by default, so you can
demo the agent surface before Spark is fully wired up.

```bash
docker compose up mcp-server
# then:
curl http://localhost:8000/healthz
curl http://localhost:8000/mcp/manifest
curl -X POST http://localhost:8000/tools/top_errors \
  -H 'content-type: application/json' \
  -d '{"service":"payments-service","last_minutes":30}'
```

To run it without Docker:

```bash
cd mcp-server
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

### 3. Generate code from the proto (Week 1)

```bash
# Go
protoc --go_out=. --go-grpc_out=. proto/logs.proto
```

### 4. Run the ingestion gateway and load generator (Week 1)

Coming in Week 1: `go run ./ingest-gateway` and `go run ./loadgen --rate 5000`.

### 5. Run the Spark streaming job (Week 2)

The wrapper script handles the right Maven coordinates for Delta + Kafka,
and the `--once` flag uses `Trigger.AvailableNow` to drain the topic and exit
(useful for tests):

```bash
# Continuous: tail Kafka forever, write Bronze + Silver every 5s
./scripts/spark-submit.sh spark/kafka_to_delta.py

# One-shot: drain everything currently in Kafka, then exit
./scripts/spark-submit.sh spark/kafka_to_delta.py --once --starting-offsets earliest
```

### 6. End-to-end pipeline smoke test

Wipes Delta + checkpoints, produces N synthetic events through Kafka, runs the
streaming job in `--once` mode, and asserts row counts in both Bronze and
Silver Delta tables:

```bash
./scripts/spark-smoke.sh           # default N=200
N=10000 ./scripts/spark-smoke.sh   # bigger payload
```

## Failure recovery story

The Spark streaming job uses a checkpoint directory at
`checkpoints/log_stream`, watermarks late events, and applies
`dropDuplicates(["trace_id", "event_id"])`. Killing and restarting the job
must not produce duplicate rows in `silver_logs` — that test is the
"exactly-once-ish" guarantee documented in Step 7.

## Benchmarks

Fill in as each week's deliverables land. Numbers below are from the local
M-series Mac smoke runs:

| metric                                      | value |
|---------------------------------------------|-------|
| Ingestion throughput (logs/sec)             | TBD (Week 1) |
| End-to-end latency (Kafka → Delta commit)   | ~5 s (one micro-batch trigger) |
| Spark `--once` drain of 200 events          | ~10 s after JAR cache warm |
| Query latency before OPTIMIZE               | TBD (Week 3) |
| Query latency after OPTIMIZE                | TBD (Week 3) |
| File count before / after compaction        | TBD (Week 3) |
| Bytes scanned reduction with token index    | TBD (Week 3) |

## Future work

- Replace stub executor with a long-lived PySpark session in the MCP server.
- Bloom filter / Z-order on `silver_logs` for additional pruning.
- Anomaly detection on `gold_error_counts` to power "Why did X spike?".
- Deploy MCP server behind auth and add per-tool rate limits.
