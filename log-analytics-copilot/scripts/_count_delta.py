"""Internal helper: report row counts for the Bronze and Silver Delta tables.

Output is a single JSON object on stdout so the surrounding shell smoke test
can parse it cleanly. Anything else goes to stderr.

Example output:
    {"bronze_logs": 1000, "silver_logs": 1000, "silver_errors": 47,
     "silver_services": ["auth-service","payments-service",...]}
"""

from __future__ import annotations

import json
import os
import sys

from pyspark.sql import SparkSession


DELTA_ROOT = os.environ.get("DELTA_ROOT", "/workspace/delta")
BRONZE_PATH = f"{DELTA_ROOT}/bronze_logs"
SILVER_PATH = f"{DELTA_ROOT}/silver_logs"


def main() -> None:
    spark = (
        SparkSession.builder.appName("count-delta")
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config(
            "spark.sql.catalog.spark_catalog",
            "org.apache.spark.sql.delta.catalog.DeltaCatalog",
        )
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("ERROR")

    out: dict = {}

    if os.path.isdir(f"{BRONZE_PATH}/_delta_log"):
        out["bronze_logs"] = spark.read.format("delta").load(BRONZE_PATH).count()
    else:
        out["bronze_logs"] = None

    if os.path.isdir(f"{SILVER_PATH}/_delta_log"):
        silver = spark.read.format("delta").load(SILVER_PATH)
        out["silver_logs"] = silver.count()
        out["silver_errors"] = silver.filter("level = 'ERROR'").count()
        out["silver_services"] = sorted(
            r["service"] for r in silver.select("service").distinct().collect()
        )
    else:
        out["silver_logs"] = None
        out["silver_errors"] = None
        out["silver_services"] = []

    print(json.dumps(out), flush=True)


if __name__ == "__main__":
    main()
