#!/usr/bin/env bash
#
# End-to-end smoke test for Kafka -> Spark Streaming -> Delta (Bronze + Silver).
#
# Steps
# -----
# 1. Pre-flight: kafka + spark-master containers must be running.
# 2. Wipe local Delta + checkpoint directories so the test is deterministic.
# 3. Make sure the logs.raw topic exists.
# 4. Produce N fake LogEvent JSON messages into logs.raw (EXTERNAL listener).
# 5. Run spark/kafka_to_delta.py --once --starting-offsets earliest
#    (Trigger.AvailableNow drains Kafka and exits cleanly).
# 6. Run _count_delta.py to validate row counts and assert bronze >= N and
#    silver >= N (Silver should equal Bronze when no duplicates were sent).
#
# Tunables (env vars):
#   N                number of events to produce (default 200)
#   KEEP_DATA=1      do NOT wipe delta/ + checkpoints/ before running

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
N="${N:-200}"
KEEP_DATA="${KEEP_DATA:-0}"

KAFKA_CONTAINER=lac-kafka
SPARK_CONTAINER=lac-spark-master
TOPIC=logs.raw

GREEN=$'\033[32m'
RED=$'\033[31m'
YELLOW=$'\033[33m'
DIM=$'\033[2m'
RESET=$'\033[0m'

step() { printf '\n%s==>%s %s\n' "$YELLOW" "$RESET" "$*"; }
ok()   { printf '%s  ✓%s %s\n' "$GREEN" "$RESET" "$*"; }
fail() { printf '%s  ✗%s %s\n' "$RED" "$RESET" "$*" >&2; exit 1; }
note() { printf '%s    %s%s\n' "$DIM" "$*" "$RESET"; }

# ---------------------------------------------------------------------------
step "Pre-flight: required containers are running"
# ---------------------------------------------------------------------------
docker exec "$KAFKA_CONTAINER" true >/dev/null 2>&1 \
  || fail "container ${KAFKA_CONTAINER} is not running. Run: docker compose up -d kafka"
docker exec "$SPARK_CONTAINER" true >/dev/null 2>&1 \
  || fail "container ${SPARK_CONTAINER} is not running. Run: docker compose up -d spark-master spark-worker"
ok "kafka + spark-master are reachable"

# ---------------------------------------------------------------------------
step "Reset Delta + checkpoint state"
# ---------------------------------------------------------------------------
if [[ "$KEEP_DATA" == "1" ]]; then
  note "KEEP_DATA=1 set, leaving delta/ and checkpoints/ alone"
else
  docker exec "$SPARK_CONTAINER" bash -lc \
    'rm -rf /workspace/delta /workspace/checkpoints && mkdir -p /workspace/delta /workspace/checkpoints'
  ok "wiped /workspace/delta and /workspace/checkpoints"
fi

# ---------------------------------------------------------------------------
step "Ensure topic ${TOPIC} exists"
# ---------------------------------------------------------------------------
if docker exec "$KAFKA_CONTAINER" \
     /opt/kafka/bin/kafka-topics.sh \
     --bootstrap-server localhost:9092 --list 2>/dev/null \
     | grep -qx "$TOPIC"; then
  ok "topic ${TOPIC} present"
else
  docker exec "$KAFKA_CONTAINER" \
    /opt/kafka/bin/kafka-topics.sh \
    --bootstrap-server localhost:9092 \
    --create --topic "$TOPIC" --partitions 3 --replication-factor 1 >/dev/null
  ok "created topic ${TOPIC}"
fi

# ---------------------------------------------------------------------------
step "Produce ${N} fake events to ${TOPIC}"
# ---------------------------------------------------------------------------
python3 "$ROOT/scripts/produce-fake-logs.py" --count "$N" --seed 42 \
  | docker exec -i "$KAFKA_CONTAINER" \
      /opt/kafka/bin/kafka-console-producer.sh \
      --bootstrap-server localhost:9092 \
      --topic "$TOPIC"
ok "wrote ${N} events"

# ---------------------------------------------------------------------------
step "Run streaming job in --once mode (drain Kafka -> Delta and exit)"
note "first run downloads the Delta + Kafka jars; this may take 1-2 minutes"
# ---------------------------------------------------------------------------
"$ROOT/scripts/spark-submit.sh" spark/kafka_to_delta.py --once --starting-offsets earliest
ok "streaming job exited cleanly"

# ---------------------------------------------------------------------------
step "Count rows in Delta tables"
# ---------------------------------------------------------------------------
counts_json="$(
  "$ROOT/scripts/spark-submit.sh" scripts/_count_delta.py \
    | tail -n 1
)"
echo "    raw counter output: ${counts_json}"

bronze=$(echo "$counts_json" | python3 -c 'import json,sys;print(json.load(sys.stdin)["bronze_logs"])')
silver=$(echo "$counts_json" | python3 -c 'import json,sys;print(json.load(sys.stdin)["silver_logs"])')
errors=$(echo "$counts_json" | python3 -c 'import json,sys;print(json.load(sys.stdin)["silver_errors"])')

note "bronze=${bronze} silver=${silver} silver_errors=${errors}"

if [[ -z "$bronze" || "$bronze" == "None" ]] || (( bronze < N )); then
  fail "bronze count (${bronze}) is less than expected (${N})"
fi
if [[ -z "$silver" || "$silver" == "None" ]] || (( silver < N )); then
  fail "silver count (${silver}) is less than expected (${N})"
fi
ok "bronze >= ${N} and silver >= ${N}"

printf '\n%sSpark pipeline smoke test passed.%s\n' "$GREEN" "$RESET"
