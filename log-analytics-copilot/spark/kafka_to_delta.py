"""
Spark Structured Streaming: Kafka logs.raw  ->  Delta Bronze + Silver.

Bronze
------
* Append-only.
* Stores the raw Kafka value as a STRING plus Kafka metadata
  (topic, partition, offset, kafka_timestamp) and an ingest_time / ingest_date.
* Partitioned by ingest_date.
* This is the "we never lose a byte" layer: if the Silver schema changes, we
  can replay Bronze through a new parser without re-reading Kafka.

Silver
------
* Parsed schema mirroring proto/logs.proto.
* Watermarked at 10 minutes on event_time.
* Deduplicated on (trace_id, event_id) within the watermark window
  -- the canonical Spark "exactly-once-ish" pattern.
* Partitioned by service / date / hour for efficient pruning.

Run modes
---------
Default (continuous):
    spark-submit kafka_to_delta.py
        Uses processingTime=5 seconds and runs forever.

One-shot (used by the smoke test and by tests that want a deterministic exit):
    spark-submit kafka_to_delta.py --once
        Uses Trigger.AvailableNow: drains everything currently in Kafka in
        micro-batches, then exits 0.

Environment overrides
---------------------
    KAFKA_BOOTSTRAP   default kafka:29092          (internal listener)
    KAFKA_TOPIC       default logs.raw
    DELTA_ROOT        default /workspace/delta
    CHECKPOINT_ROOT   default /workspace/checkpoints
    TRIGGER_INTERVAL  default "5 seconds"
    STARTING_OFFSETS  default "latest" (override to "earliest" for replay)
"""

from __future__ import annotations

import argparse
import os

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql.functions import col, current_timestamp, from_json, hour, to_timestamp
from pyspark.sql.types import StringType, StructField, StructType


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP", "kafka:29092")
KAFKA_TOPIC = os.environ.get("KAFKA_TOPIC", "logs.raw")
DELTA_ROOT = os.environ.get("DELTA_ROOT", "/workspace/delta")
CHECKPOINT_ROOT = os.environ.get("CHECKPOINT_ROOT", "/workspace/checkpoints")
TRIGGER_INTERVAL = os.environ.get("TRIGGER_INTERVAL", "5 seconds")

BRONZE_PATH = f"{DELTA_ROOT}/bronze_logs"
SILVER_PATH = f"{DELTA_ROOT}/silver_logs"
BRONZE_CKPT = f"{CHECKPOINT_ROOT}/bronze_logs"
SILVER_CKPT = f"{CHECKPOINT_ROOT}/silver_logs"


# Mirrors proto/logs.proto. Strings here because the producer emits ISO-8601
# timestamps; we cast to TIMESTAMP downstream.
LOG_SCHEMA = StructType(
    [
        StructField("timestamp", StringType(), True),
        StructField("service", StringType(), True),
        StructField("level", StringType(), True),
        StructField("message", StringType(), True),
        StructField("trace_id", StringType(), True),
        StructField("event_id", StringType(), True),
        StructField("host", StringType(), True),
    ]
)


# ---------------------------------------------------------------------------
# Spark session
# ---------------------------------------------------------------------------


def build_session() -> SparkSession:
    """Build a SparkSession wired up with the Delta Lake SQL extensions."""
    return (
        SparkSession.builder.appName("log-analytics-kafka-to-delta")
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config(
            "spark.sql.catalog.spark_catalog",
            "org.apache.spark.sql.delta.catalog.DeltaCatalog",
        )
        # Local-dev: keep shuffle partitions small so micro-batches are fast.
        .config("spark.sql.shuffle.partitions", "4")
        .getOrCreate()
    )


# ---------------------------------------------------------------------------
# DataFrame builders
# ---------------------------------------------------------------------------


def read_kafka(spark: SparkSession, starting_offsets: str) -> DataFrame:
    """Open the streaming Kafka source."""
    return (
        spark.readStream.format("kafka")
        .option("kafka.bootstrap.servers", KAFKA_BOOTSTRAP)
        .option("subscribe", KAFKA_TOPIC)
        .option("startingOffsets", starting_offsets)
        # If retention deletes data we missed, keep going rather than crash.
        .option("failOnDataLoss", "false")
        .load()
    )


def to_bronze(raw: DataFrame) -> DataFrame:
    """Bronze: raw value + Kafka metadata + ingest stamp."""
    return (
        raw.selectExpr(
            "CAST(value AS STRING) AS raw_value",
            "topic",
            "partition",
            "offset",
            "timestamp AS kafka_timestamp",
        )
        .withColumn("ingest_time", current_timestamp())
        .withColumn("ingest_date", col("ingest_time").cast("date"))
    )


def to_silver(raw: DataFrame) -> DataFrame:
    """Silver: parsed, watermarked, deduplicated, partition columns added."""
    parsed = (
        raw.selectExpr("CAST(value AS STRING) AS json")
        .select(from_json(col("json"), LOG_SCHEMA).alias("e"))
        .select("e.*")
        .withColumn("event_time", to_timestamp(col("timestamp")))
        .withColumn("date", col("event_time").cast("date"))
        .withColumn("hour", hour(col("event_time")))
    )

    return parsed.withWatermark("event_time", "10 minutes").dropDuplicates(
        ["trace_id", "event_id"]
    )


# ---------------------------------------------------------------------------
# Sinks
# ---------------------------------------------------------------------------


def start_bronze(df: DataFrame, trigger_kwargs: dict) -> "StreamingQuery":
    return (
        df.writeStream.format("delta")
        .outputMode("append")
        .option("checkpointLocation", BRONZE_CKPT)
        .option("path", BRONZE_PATH)
        .partitionBy("ingest_date")
        .queryName("bronze-sink")
        .trigger(**trigger_kwargs)
        .start()
    )


def start_silver(df: DataFrame, trigger_kwargs: dict) -> "StreamingQuery":
    return (
        df.writeStream.format("delta")
        .outputMode("append")
        .option("checkpointLocation", SILVER_CKPT)
        .option("path", SILVER_PATH)
        .partitionBy("service", "date", "hour")
        .queryName("silver-sink")
        .trigger(**trigger_kwargs)
        .start()
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--once",
        action="store_true",
        help="Use Trigger.AvailableNow: drain Kafka and exit. Useful for tests.",
    )
    parser.add_argument(
        "--starting-offsets",
        default=os.environ.get("STARTING_OFFSETS", "latest"),
        help="Kafka startingOffsets when no checkpoint exists (default: latest).",
    )
    args = parser.parse_args()

    spark = build_session()
    spark.sparkContext.setLogLevel("WARN")

    print(
        f"[kafka_to_delta] bootstrap={KAFKA_BOOTSTRAP} topic={KAFKA_TOPIC} "
        f"bronze={BRONZE_PATH} silver={SILVER_PATH} once={args.once} "
        f"startingOffsets={args.starting_offsets}",
        flush=True,
    )

    raw = read_kafka(spark, args.starting_offsets)

    # In --once mode, Trigger.AvailableNow drains all currently-available
    # offsets in micro-batches and then exits the query.
    trigger_kwargs = (
        {"availableNow": True} if args.once else {"processingTime": TRIGGER_INTERVAL}
    )

    bronze_q = start_bronze(to_bronze(raw), trigger_kwargs)
    silver_q = start_silver(to_silver(raw), trigger_kwargs)

    if args.once:
        # Wait for both queries to finish draining, then exit.
        bronze_q.awaitTermination()
        silver_q.awaitTermination()
    else:
        spark.streams.awaitAnyTermination()


if __name__ == "__main__":
    main()
