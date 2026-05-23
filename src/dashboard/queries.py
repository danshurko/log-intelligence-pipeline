"""Athena query layer for the Streamlit dashboard.

Each public function returns a pandas DataFrame (or simple scalar/dict) so
the dashboard tabs can render results directly. Results are cached with
`st.cache_data(ttl=30)` so a single render reuses queries and the 30-second
auto-refresh interval drives invalidation.

Configuration is read from environment variables (set by `make dashboard`
from `terraform output`):

  ATHENA_OUTPUT_S3       — s3://… prefix for Athena's scratch results
  ATHENA_WORKGROUP       — defaults to "primary"
  AWS_REGION             — defaults to "us-east-1"
  CURATED_DATABASE       — Glue Catalog database holding fct_events etc.
  DLQ_NAME               — optional, enables the DLQ-depth sidebar tile
  STATE_MACHINE_ARN      — optional, enables the last-run sidebar tile
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import boto3
import pandas as pd
import streamlit as st
from pyathena import connect
from pyathena.pandas.cursor import PandasCursor

SQL_METRICS = Path(__file__).resolve().parents[2] / "sql" / "metrics"


def _region() -> str:
    return os.environ.get("AWS_REGION", "us-east-1")


def _curated_db() -> str:
    return os.environ.get("CURATED_DATABASE", "device_log_curated")


def _read_metric_sql(filename: str) -> str:
    return (SQL_METRICS / filename).read_text().replace("{curated_db}", _curated_db())


@st.cache_data(ttl=30, show_spinner=False)
def run_athena_query(sql: str, output_location: str | None = None) -> pd.DataFrame:
    conn = connect(
        s3_staging_dir=output_location or os.environ["ATHENA_OUTPUT_S3"],
        region_name=_region(),
        work_group=os.environ.get("ATHENA_WORKGROUP", "primary"),
        cursor_class=PandasCursor,
    )
    return conn.cursor().execute(sql).as_pandas()


@st.cache_data(ttl=30, show_spinner=False)
def error_rate_by_device() -> pd.DataFrame:
    return run_athena_query(_read_metric_sql("01_error_rate_by_device.sql"))


@st.cache_data(ttl=30, show_spinner=False)
def top_error_codes() -> pd.DataFrame:
    return run_athena_query(_read_metric_sql("02_top_error_codes.sql"))


@st.cache_data(ttl=30, show_spinner=False)
def mtbe_per_device() -> pd.DataFrame:
    return run_athena_query(_read_metric_sql("03_mtbe_per_device.sql"))


@st.cache_data(ttl=30, show_spinner=False)
def silent_devices() -> pd.DataFrame:
    return run_athena_query(_read_metric_sql("04_silent_devices.sql"))


@st.cache_data(ttl=30, show_spinner=False)
def firmware_cohort_errors() -> pd.DataFrame:
    return run_athena_query(_read_metric_sql("05_firmware_cohort_errors.sql"))


@st.cache_data(ttl=30, show_spinner=False)
def anomaly_error_burst() -> pd.DataFrame:
    return run_athena_query(_read_metric_sql("06_anomaly_error_burst.sql"))


@st.cache_data(ttl=30, show_spinner=False)
def overview_totals() -> dict:
    db = _curated_db()
    df = run_athena_query(
        f"""
        SELECT
          COUNT(*) AS total_events,
          SUM(CASE WHEN event_type = 'error' THEN 1 ELSE 0 END) AS error_count,
          COUNT(DISTINCT device_sk) AS active_devices
        FROM {db}.fct_events
        WHERE event_ts >= current_timestamp - INTERVAL '1' DAY
        """
    )
    total = int(df["total_events"].iloc[0] or 0)
    errors = int(df["error_count"].iloc[0] or 0)
    return {
        "total_events": total,
        "active_devices": int(df["active_devices"].iloc[0] or 0),
        "error_rate": (errors / total) if total else 0.0,
    }


@st.cache_data(ttl=30, show_spinner=False)
def events_per_hour_7d() -> pd.DataFrame:
    db = _curated_db()
    return run_athena_query(
        f"""
        SELECT
          date_trunc('hour', event_ts) AS hour_bucket,
          COUNT(*) AS event_count,
          SUM(CASE WHEN event_type = 'error' THEN 1 ELSE 0 END) AS error_count
        FROM {db}.fct_events
        WHERE event_ts >= current_timestamp - INTERVAL '7' DAY
        GROUP BY date_trunc('hour', event_ts)
        ORDER BY hour_bucket
        """
    )


@st.cache_data(ttl=30, show_spinner=False)
def recent_errors(limit: int = 50) -> pd.DataFrame:
    db = _curated_db()
    return run_athena_query(
        f"""
        SELECT
          e.event_ts,
          d.device_id,
          e.error_code,
          e.message
        FROM {db}.fct_errors e
        JOIN {db}.dim_devices d
          ON d.device_sk = e.device_sk
        WHERE e.event_ts >= current_timestamp - INTERVAL '7' DAY
        ORDER BY e.event_ts DESC
        LIMIT {int(limit)}
        """
    )


@st.cache_data(ttl=30, show_spinner=False)
def latest_pipeline_run() -> dict | None:
    db = _curated_db()
    try:
        df = run_athena_query(
            f"""
            SELECT run_id, started_at, ended_at, raw_records_read,
                   clean_records_written, rejected_records, status, failure_reason
            FROM {db}.pipeline_runs
            ORDER BY started_at DESC
            LIMIT 1
            """
        )
    except Exception:
        # Table may not exist before the first Glue job run; surface that
        # as "no data" rather than a dashboard-wide crash.
        return None
    if df.empty:
        return None
    return df.iloc[0].to_dict()


@st.cache_data(ttl=30, show_spinner=False)
def dlq_depth() -> int | None:
    dlq = os.environ.get("DLQ_NAME")
    if not dlq:
        return None
    cw = boto3.client("cloudwatch", region_name=_region())
    now = datetime.now(UTC)
    resp = cw.get_metric_statistics(
        Namespace="AWS/SQS",
        MetricName="ApproximateNumberOfMessagesVisible",
        Dimensions=[{"Name": "QueueName", "Value": dlq}],
        StartTime=now - timedelta(minutes=15),
        EndTime=now,
        Period=60,
        Statistics=["Maximum"],
    )
    pts = resp.get("Datapoints") or []
    if not pts:
        return 0
    return int(max(pts, key=lambda p: p["Timestamp"])["Maximum"])


@st.cache_data(ttl=30, show_spinner=False)
def last_successful_execution() -> dict | None:
    arn = os.environ.get("STATE_MACHINE_ARN")
    if not arn:
        return None
    sfn = boto3.client("stepfunctions", region_name=_region())
    resp = sfn.list_executions(stateMachineArn=arn, statusFilter="SUCCEEDED", maxResults=1)
    items = resp.get("executions") or []
    return items[0] if items else None
