#!/usr/bin/env bash
#
# Wrapper around spark-submit that pulls in the Delta Lake + Kafka connectors.
#
# Usage:
#   ./scripts/spark-submit.sh spark/kafka_to_delta.py [--once]
#
# All extra arguments after the script path are passed to the Python program.
#
# Defaults
# --------
# SPARK_MASTER     local[*]                       (driver+executors in the master container)
# SPARK_CONTAINER  lac-spark-master
# DELTA_VERSION    3.2.0     (matches Spark 3.5.x)
# SPARK_VERSION    3.5.0     (used for the kafka connector version)
# SCALA_VERSION    2.12
#
# Override e.g. SPARK_MASTER=spark://lac-spark-master:7077 to exercise the worker.

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <python-file relative to /workspace> [extra args...]" >&2
  exit 2
fi

SCRIPT="$1"
shift

CONTAINER="${SPARK_CONTAINER:-lac-spark-master}"
MASTER_URL="${SPARK_MASTER:-local[*]}"
DELTA_VERSION="${DELTA_VERSION:-3.2.0}"
SPARK_VERSION="${SPARK_VERSION:-3.5.6}"
SCALA_VERSION="${SCALA_VERSION:-2.12}"

PACKAGES="io.delta:delta-spark_${SCALA_VERSION}:${DELTA_VERSION}"
PACKAGES+=",org.apache.spark:spark-sql-kafka-0-10_${SCALA_VERSION}:${SPARK_VERSION}"

# We exec as root and set HADOOP_USER_NAME because the Bitnami image runs as
# UID 1001 with no /etc/passwd entry, which makes Hadoop's UGI UNIX-login fail
# during dependency resolution (NPE in UnixPrincipal). Setting HADOOP_USER_NAME
# tells Hadoop to skip the OS lookup and use that name directly.
#
# spark.jars.ivy points to a writable location for the Ivy package cache.
docker exec \
  -u 0 \
  -e HADOOP_USER_NAME=spark \
  "$CONTAINER" \
  /opt/bitnami/spark/bin/spark-submit \
    --master "$MASTER_URL" \
    --deploy-mode client \
    --packages "$PACKAGES" \
    --conf "spark.jars.ivy=/tmp/.ivy2" \
    --conf "spark.sql.extensions=io.delta.sql.DeltaSparkSessionExtension" \
    --conf "spark.sql.catalog.spark_catalog=org.apache.spark.sql.delta.catalog.DeltaCatalog" \
    "/workspace/${SCRIPT}" "$@"
