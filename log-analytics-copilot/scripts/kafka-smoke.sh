#!/usr/bin/env bash
#
# Kafka KRaft smoke test for the Log Analytics Copilot stack.
#
# Verifies:
#   1. The lac-kafka container is up and healthy.
#   2. Kafka is running in KRaft mode (no Zookeeper).
#   3. The EXTERNAL listener works (produce from host-equivalent path).
#   4. The INTERNAL listener works (consume from a sibling container).
#   5. The expected topic logs.raw exists with the expected partition count.
#
# Exits non-zero on any failure so it can be wired into CI.

set -euo pipefail

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
CONTAINER="${KAFKA_CONTAINER:-lac-kafka}"
TOPIC="${KAFKA_TOPIC:-logs.raw}"
PARTITIONS="${KAFKA_PARTITIONS:-3}"
NETWORK="${KAFKA_NETWORK:-log-analytics-copilot_default}"
EXTERNAL_BOOTSTRAP="${KAFKA_EXTERNAL_BOOTSTRAP:-localhost:9092}"
INTERNAL_BOOTSTRAP="${KAFKA_INTERNAL_BOOTSTRAP:-kafka:29092}"
KAFKA_IMAGE="${KAFKA_IMAGE:-bitnamilegacy/kafka:3.7}"
TEST_PAYLOAD="kafka-smoke-$(date +%s)-$$"

KAFKA_BIN="/opt/kafka/bin"

# ---------------------------------------------------------------------------
# Pretty output
# ---------------------------------------------------------------------------
GREEN=$'\033[32m'
RED=$'\033[31m'
YELLOW=$'\033[33m'
DIM=$'\033[2m'
RESET=$'\033[0m'

step()  { printf '\n%s==>%s %s\n' "$YELLOW" "$RESET" "$*"; }
ok()    { printf '%s  ✓%s %s\n' "$GREEN" "$RESET" "$*"; }
fail()  { printf '%s  ✗%s %s\n' "$RED"   "$RESET" "$*" >&2; exit 1; }
note()  { printf '%s    %s%s\n' "$DIM"   "$*"      "$RESET"; }

# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------
step "Pre-flight: docker is available"
docker version --format '{{.Server.Version}}' >/dev/null 2>&1 \
  || fail "docker daemon not reachable"
ok "docker daemon reachable"

step "Pre-flight: container ${CONTAINER} is healthy"
if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
  fail "container ${CONTAINER} not found. Run: docker compose up -d kafka"
fi
status="$(docker inspect --format '{{.State.Status}}' "$CONTAINER")"
health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$CONTAINER")"
note "status=${status} health=${health}"
[[ "$status" == "running" ]] || fail "container is not running (status=${status})"
if [[ "$health" != "none" && "$health" != "healthy" ]]; then
  fail "container health is ${health}"
fi
ok "container running${health:+ (}${health}${health:+)}"

# ---------------------------------------------------------------------------
# 1. KRaft mode, no Zookeeper
# ---------------------------------------------------------------------------
step "1/5 KRaft mode is active and Zookeeper is absent"
cfg="$(docker exec "$CONTAINER" sh -c 'cat /opt/kafka/config/server.properties 2>/dev/null || true')"
echo "$cfg" | grep -q '^process.roles=' \
  || fail "process.roles missing — broker is not in KRaft mode"
roles="$(echo "$cfg" | awk -F= '/^process.roles=/{print $2}')"
note "process.roles=${roles}"

if echo "$cfg" | grep -Eq '^zookeeper.connect='; then
  fail "zookeeper.connect is set — broker is NOT pure KRaft"
fi
if docker ps --format '{{.Names}}' | grep -qi zookeeper; then
  fail "a Zookeeper container is running on this host"
fi
ok "running in KRaft mode (${roles}) with no Zookeeper"

# ---------------------------------------------------------------------------
# 2. Topic exists (create if missing)
# ---------------------------------------------------------------------------
step "2/5 Topic ${TOPIC} exists with ${PARTITIONS} partitions"
if docker exec "$CONTAINER" "$KAFKA_BIN/kafka-topics.sh" \
      --bootstrap-server "$EXTERNAL_BOOTSTRAP" --list 2>/dev/null \
      | grep -qx "$TOPIC"; then
  note "topic ${TOPIC} already exists, reusing"
else
  note "topic missing, creating"
  docker exec "$CONTAINER" "$KAFKA_BIN/kafka-topics.sh" \
    --bootstrap-server "$EXTERNAL_BOOTSTRAP" \
    --create --topic "$TOPIC" \
    --partitions "$PARTITIONS" --replication-factor 1 \
    >/dev/null
fi
desc="$(docker exec "$CONTAINER" "$KAFKA_BIN/kafka-topics.sh" \
          --bootstrap-server "$EXTERNAL_BOOTSTRAP" \
          --describe --topic "$TOPIC")"
echo "$desc" | sed "s/^/    /"
actual_partitions="$(echo "$desc" | awk -F'PartitionCount: ' '/PartitionCount/ {print $2}' | awk '{print $1}')"
[[ "$actual_partitions" == "$PARTITIONS" ]] \
  || fail "expected ${PARTITIONS} partitions, got ${actual_partitions}"
ok "topic ${TOPIC} has ${actual_partitions} partitions"

# ---------------------------------------------------------------------------
# 3. EXTERNAL listener: produce
# ---------------------------------------------------------------------------
step "3/5 EXTERNAL listener (${EXTERNAL_BOOTSTRAP}) produce"
docker exec -i "$CONTAINER" "$KAFKA_BIN/kafka-console-producer.sh" \
  --bootstrap-server "$EXTERNAL_BOOTSTRAP" \
  --topic "$TOPIC" \
  <<<"$TEST_PAYLOAD" \
  >/dev/null
ok "produced 1 message via EXTERNAL listener"

# ---------------------------------------------------------------------------
# 4. INTERNAL listener: consume from a sibling container
# ---------------------------------------------------------------------------
step "4/5 INTERNAL listener (${INTERNAL_BOOTSTRAP}) consume from sibling container"
got="$(docker run --rm --network "$NETWORK" "$KAFKA_IMAGE" \
        "$KAFKA_BIN/kafka-console-consumer.sh" \
        --bootstrap-server "$INTERNAL_BOOTSTRAP" \
        --topic "$TOPIC" \
        --from-beginning \
        --timeout-ms 15000 2>/dev/null \
        | grep -F "$TEST_PAYLOAD" || true)"

if [[ -z "$got" ]]; then
  fail "did not see test payload via INTERNAL listener"
fi
ok "consumed test payload via INTERNAL listener"
note "payload: ${got}"

# ---------------------------------------------------------------------------
# 5. Host-side TCP reachability (optional but useful for the Go gateway)
# ---------------------------------------------------------------------------
step "5/5 Host TCP reachability on ${EXTERNAL_BOOTSTRAP}"
host_port="${EXTERNAL_BOOTSTRAP##*:}"
host_name="${EXTERNAL_BOOTSTRAP%:*}"
if command -v nc >/dev/null 2>&1; then
  if nc -z -G 2 "$host_name" "$host_port" >/dev/null 2>&1 \
     || nc -z -w 2 "$host_name" "$host_port" >/dev/null 2>&1; then
    ok "host can TCP-connect to ${EXTERNAL_BOOTSTRAP}"
  else
    fail "host cannot TCP-connect to ${EXTERNAL_BOOTSTRAP}"
  fi
else
  note "nc not installed, skipping host TCP check"
fi

printf '\n%sAll Kafka smoke tests passed.%s\n' "$GREEN" "$RESET"
