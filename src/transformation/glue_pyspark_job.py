"""Glue ETL job: runs staging and curated transform SQL sequentially."""

from __future__ import annotations

import sys
import traceback
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

import boto3
from awsglue.context import GlueContext  # type: ignore[import-not-found]
from awsglue.utils import getResolvedOptions  # type: ignore[import-not-found]
from pyspark.context import SparkContext
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql.types import (
    BooleanType,
    LongType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

PIPELINE_RUNS_TABLE_NAME: str = "pipeline_runs"

# Field order must match the pipeline_runs table DDL for insertInto.
PIPELINE_RUNS_SCHEMA: StructType = StructType(
    [
        StructField("run_id", StringType(), nullable=False),
        StructField("started_at", TimestampType(), nullable=False),
        StructField("ended_at", TimestampType(), nullable=False),
        StructField("raw_records_read", LongType(), nullable=True),
        StructField("clean_records_written", LongType(), nullable=True),
        StructField("rejected_records", LongType(), nullable=True),
        StructField("status", StringType(), nullable=False),
        StructField("failure_reason", StringType(), nullable=True),
    ]
)

# device_sk is BIGINT to match SCD2 key allocation.
DIM_DEVICES_BOOTSTRAP_DDL: str = """
    device_sk BIGINT,
    device_id STRING,
    firmware_version STRING,
    facility_id STRING,
    valid_from TIMESTAMP,
    valid_to TIMESTAMP,
    is_current BOOLEAN
"""

DIM_DEVICES_SPARK_SCHEMA: StructType = StructType(
    [
        StructField("device_sk", LongType(), nullable=True),
        StructField("device_id", StringType(), nullable=True),
        StructField("firmware_version", StringType(), nullable=True),
        StructField("facility_id", StringType(), nullable=True),
        StructField("valid_from", TimestampType(), nullable=True),
        StructField("valid_to", TimestampType(), nullable=True),
        StructField("is_current", BooleanType(), nullable=True),
    ]
)


@dataclass(frozen=True)
class Transform:
    sql_file: str
    # zone is 'staging' or 'curated'.
    zone: str
    table_name: str


TRANSFORMS: tuple[Transform, ...] = (
    Transform("stg_events_clean.sql", "staging", "events_clean"),
    Transform("dim_facilities.sql", "curated", "dim_facilities"),
    Transform("dim_firmware.sql", "curated", "dim_firmware"),
    Transform("dim_devices_scd2.sql", "curated", "dim_devices"),
    Transform("fct_events.sql", "curated", "fct_events"),
    Transform("fct_errors.sql", "curated", "fct_errors"),
)


def _read_sql_from_s3(s3_client, s3_uri: str) -> str:
    parsed = urlparse(s3_uri)
    obj = s3_client.get_object(Bucket=parsed.netloc, Key=parsed.path.lstrip("/"))
    return obj["Body"].read().decode("utf-8")


def _substitute_db_markers(sql_text: str, db_map: dict[str, str]) -> str:
    # Use replace to avoid altering literal { } in SQL.
    return (
        sql_text.replace("{raw_db}", db_map["raw"])
        .replace("{staging_db}", db_map["staging"])
        .replace("{curated_db}", db_map["curated"])
    )


def _table_location(zone: str, table_name: str, staging_loc: str, curated_loc: str) -> str:
    base = staging_loc if zone == "staging" else curated_loc
    return f"{base.rstrip('/')}/{table_name}/"


def _table_exists(spark: SparkSession, table: str) -> bool:
    try:
        spark.sql(f"DESCRIBE TABLE {table}").collect()
    except Exception:  # noqa: BLE001 - missing table is the documented signal
        return False
    return True


def _get_table_location(spark: SparkSession, table: str) -> str | None:
    """Return the table LOCATION URI, or None if not available."""
    try:
        rows = spark.sql(f"DESCRIBE EXTENDED {table}").collect()
    except Exception:  # noqa: BLE001 - missing table is the documented signal
        return None
    for row in rows:
        if row["col_name"] == "Location":
            return row["data_type"]
    return None


def _bootstrap_dim_devices(spark: SparkSession, curated_db: str, primary_path: str) -> None:
    """Create empty dim_devices table if missing."""
    table = f"{curated_db}.dim_devices"
    if _table_exists(spark, table):
        return
    spark.sql(
        f"CREATE TABLE {table} ({DIM_DEVICES_BOOTSTRAP_DDL}) "
        f"USING PARQUET LOCATION '{primary_path}'"
    )
    spark.createDataFrame([], DIM_DEVICES_SPARK_SCHEMA).write.mode("overwrite").parquet(
        primary_path
    )


def _materialize_to_table(
    spark: SparkSession,
    sql_text: str,
    target_table: str,
    s3_path: str,
) -> int:
    df: DataFrame = spark.sql(sql_text)
    df.write.mode("overwrite").parquet(s3_path)
    spark.sql(f"CREATE TABLE IF NOT EXISTS {target_table} USING PARQUET LOCATION '{s3_path}'")
    spark.catalog.refreshTable(target_table)
    return spark.sql(f"SELECT COUNT(*) AS n FROM {target_table}").collect()[0]["n"]


def _materialize_dim_devices(
    spark: SparkSession,
    sql_text: str,
    curated_db: str,
    curated_loc: str,
) -> int:
    """Write dim_devices using alternating paths to avoid read-write race."""
    table = f"{curated_db}.dim_devices"
    primary = f"{curated_loc.rstrip('/')}/dim_devices/"
    secondary = f"{curated_loc.rstrip('/')}/dim_devices_next/"
    current = _get_table_location(spark, table)
    next_path = (
        secondary if current is not None and current.rstrip("/") == primary.rstrip("/") else primary
    )

    df: DataFrame = spark.sql(sql_text)
    df.write.mode("overwrite").parquet(next_path)

    spark.sql(f"DROP TABLE IF EXISTS {table}")
    spark.sql(
        f"CREATE TABLE {table} ({DIM_DEVICES_BOOTSTRAP_DDL}) USING PARQUET LOCATION '{next_path}'"
    )
    spark.catalog.refreshTable(table)
    return spark.sql(f"SELECT COUNT(*) AS n FROM {table}").collect()[0]["n"]


def _run_transforms(
    spark: SparkSession,
    s3_client,
    sql_base_uri: str,
    staging_loc: str,
    curated_loc: str,
    db_map: dict[str, str],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for transform in TRANSFORMS:
        target_table = f"{db_map[transform.zone]}.{transform.table_name}"
        sql_text = _substitute_db_markers(
            _read_sql_from_s3(s3_client, f"{sql_base_uri.rstrip('/')}/{transform.sql_file}"),
            db_map,
        )
        if transform.sql_file == "dim_devices_scd2.sql":
            count = _materialize_dim_devices(spark, sql_text, db_map["curated"], curated_loc)
        else:
            s3_path = _table_location(
                transform.zone, transform.table_name, staging_loc, curated_loc
            )
            count = _materialize_to_table(spark, sql_text, target_table, s3_path)
        counts[target_table] = count
    return counts


def _ensure_pipeline_runs_table(spark: SparkSession, curated_db: str, curated_location: str) -> str:
    table = f"{curated_db}.{PIPELINE_RUNS_TABLE_NAME}"
    spark.sql(
        f"""
        CREATE TABLE IF NOT EXISTS {table} (
            run_id STRING,
            started_at TIMESTAMP,
            ended_at TIMESTAMP,
            raw_records_read BIGINT,
            clean_records_written BIGINT,
            rejected_records BIGINT,
            status STRING,
            failure_reason STRING
        )
        USING PARQUET
        LOCATION '{curated_location.rstrip("/")}/pipeline_runs/'
        """
    )
    return table


def _to_naive_utc(dt: datetime) -> datetime:
    """Convert datetime to naive UTC for Spark TIMESTAMP."""
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _write_pipeline_run(
    spark: SparkSession,
    table: str,
    *,
    run_id: str,
    started_at: datetime,
    ended_at: datetime,
    raw_records_read: int,
    clean_records_written: int,
    rejected_records: int,
    status: str,
    failure_reason: str | None,
) -> None:
    row = (
        run_id,
        _to_naive_utc(started_at),
        _to_naive_utc(ended_at),
        int(raw_records_read),
        int(clean_records_written),
        int(rejected_records),
        status,
        failure_reason,
    )
    df = spark.createDataFrame([row], schema=PIPELINE_RUNS_SCHEMA)
    df.write.insertInto(table)


def _required_args() -> Iterable[str]:
    return (
        "JOB_NAME",
        "sql_base_uri",
        "raw_database",
        "staging_database",
        "curated_database",
        "staging_location",
        "curated_location",
    )


def main() -> None:
    args = getResolvedOptions(sys.argv, list(_required_args()))
    db_map = {
        "raw": args["raw_database"],
        "staging": args["staging_database"],
        "curated": args["curated_database"],
    }
    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)

    sc = SparkContext.getOrCreate()
    glue_context = GlueContext(sc)
    spark: SparkSession = glue_context.spark_session
    s3_client = boto3.client("s3")

    pipeline_runs_table = _ensure_pipeline_runs_table(
        spark, db_map["curated"], args["curated_location"]
    )
    # Bootstrap dim_devices once at job start.
    primary_dim_devices_path = f"{args['curated_location'].rstrip('/')}/dim_devices/"
    _bootstrap_dim_devices(spark, db_map["curated"], primary_dim_devices_path)

    raw_records_read = 0
    clean_records_written = 0
    status = "success"
    failure_reason: str | None = None

    try:
        raw_records_read = spark.sql(f"SELECT COUNT(*) AS n FROM {db_map['raw']}.events").collect()[
            0
        ]["n"]

        counts = _run_transforms(
            spark,
            s3_client,
            args["sql_base_uri"],
            args["staging_location"],
            args["curated_location"],
            db_map,
        )
        clean_records_written = counts.get(f"{db_map['staging']}.events_clean", 0)
    except Exception as exc:  # noqa: BLE001 - write any failure to the audit row
        status = "failed"
        failure_reason = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
        _write_pipeline_run(
            spark,
            pipeline_runs_table,
            run_id=run_id,
            started_at=started_at,
            ended_at=datetime.now(timezone.utc),
            raw_records_read=raw_records_read,
            clean_records_written=clean_records_written,
            rejected_records=max(raw_records_read - clean_records_written, 0),
            status=status,
            failure_reason=failure_reason,
        )
        raise

    _write_pipeline_run(
        spark,
        pipeline_runs_table,
        run_id=run_id,
        started_at=started_at,
        ended_at=datetime.now(timezone.utc),
        raw_records_read=raw_records_read,
        clean_records_written=clean_records_written,
        rejected_records=max(raw_records_read - clean_records_written, 0),
        status=status,
        failure_reason=failure_reason,
    )


if __name__ == "__main__":
    main()
