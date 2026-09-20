#!/usr/bin/env python3
"""
Emit synthetic LogEvent JSON messages on stdout, one per line.

The schema mirrors proto/logs.proto and the schema used by
spark/kafka_to_delta.py, so output can be piped straight into
kafka-console-producer.sh -- no extra dependencies, stdlib only.

Example
-------
    python3 scripts/produce-fake-logs.py --count 1000 --seed 42 \\
      | docker exec -i lac-kafka /opt/kafka/bin/kafka-console-producer.sh \\
          --bootstrap-server localhost:9092 \\
          --topic logs.raw

Flags
-----
--count        number of events to emit (default 1000)
--services     comma-separated service list (default: 4 services)
--error-rate   probability of forcing level=ERROR (default 0.05)
--rate         events/sec; 0 means "as fast as possible" (default 0)
--seed         optional RNG seed for reproducible output
--duplicate    fraction of events that should be re-emitted with the SAME
               (trace_id, event_id) right after, to exercise Silver dedup
               (default 0.0; e.g. 0.1 means roughly 10% duplicates)
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import uuid
from datetime import datetime, timezone

DEFAULT_SERVICES = [
    "payments-service",
    "auth-service",
    "checkout-service",
    "inventory-service",
]

LEVELS = ["DEBUG", "INFO", "WARN", "ERROR"]
LEVEL_WEIGHTS = [10, 70, 15, 5]

ERROR_MESSAGES = [
    "stripe charge timeout after 30s",
    "db connection reset",
    "redis cache miss avalanche",
    "downstream auth-service 503",
    "kafka producer queue full",
    "ssl handshake failed",
    "circuit breaker opened for billing",
]

INFO_MESSAGES = [
    "request handled in 12ms",
    "cache hit for user_id=42",
    "scheduled job started",
    "graceful shutdown signal received",
    "rotated access token",
    "background reconcile loop tick",
]

HOSTS = [f"host-{i:03d}" for i in range(1, 21)]


def make_event(service: str, error_rate: float) -> dict:
    if random.random() < error_rate:
        level = "ERROR"
    else:
        level = random.choices(LEVELS, weights=LEVEL_WEIGHTS, k=1)[0]
    message = random.choice(
        ERROR_MESSAGES if level == "ERROR" else INFO_MESSAGES
    )
    return {
        "timestamp": datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "service": service,
        "level": level,
        "message": message,
        "trace_id": str(uuid.uuid4()),
        "event_id": str(uuid.uuid4()),
        "host": random.choice(HOSTS),
    }


def emit(event: dict) -> None:
    sys.stdout.write(json.dumps(event, separators=(",", ":")))
    sys.stdout.write("\n")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--count", type=int, default=1000)
    p.add_argument("--services", type=str, default=",".join(DEFAULT_SERVICES))
    p.add_argument("--error-rate", type=float, default=0.05)
    p.add_argument(
        "--rate",
        type=float,
        default=0.0,
        help="approximate events/sec; 0 = unbounded",
    )
    p.add_argument("--seed", type=int, default=None)
    p.add_argument(
        "--duplicate",
        type=float,
        default=0.0,
        help="fraction of events to immediately re-emit (same trace_id + event_id) "
        "to exercise Silver dedup",
    )
    args = p.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    services = [s.strip() for s in args.services.split(",") if s.strip()]
    if not services:
        sys.exit("--services produced an empty list")

    interval = 1.0 / args.rate if args.rate > 0 else 0.0
    written = 0
    try:
        for _ in range(args.count):
            ev = make_event(random.choice(services), args.error_rate)
            emit(ev)
            written += 1
            if args.duplicate > 0 and random.random() < args.duplicate:
                emit(ev)
                written += 1
            if interval:
                time.sleep(interval)
    except BrokenPipeError:
        # Consumer stopped reading; that is a normal shutdown for a pipe.
        pass

    sys.stdout.flush()
    print(f"[produce-fake-logs] wrote {written} events", file=sys.stderr)


if __name__ == "__main__":
    main()
