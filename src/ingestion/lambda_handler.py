import base64
import binascii
import io
import json
import logging
import os
import uuid
from collections.abc import Iterable
from datetime import datetime
from typing import Any

import boto3
import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import ValidationError

from src.common.observability import get_logger, log
from src.common.schemas import Event

LOGGER = get_logger("ingestion.lambda")

CW_NAMESPACE = "DeviceLogPipeline"
METRIC_PROCESSED = "RecordsProcessed"
METRIC_REJECTED = "RecordsRejected"

RAW_BUCKET_ENV = "RAW_BUCKET"

EVENT_ARROW_SCHEMA = pa.schema(
    [
        pa.field("event_id", pa.string()),
        pa.field("device_id", pa.string()),
        pa.field("timestamp", pa.timestamp("ms", tz="UTC")),
        pa.field("event_type", pa.string()),
        pa.field("severity", pa.string()),
        pa.field("device_type", pa.string()),
        pa.field("facility_id", pa.string()),
        pa.field("firmware_version", pa.string()),
        pa.field("error_code", pa.string()),
        pa.field("message", pa.string()),
        pa.field("metrics_json", pa.string()),
    ]
)


def _decode_kinesis_record(raw: dict) -> bytes | None:
    payload = raw.get("kinesis", {}).get("data")
    if payload is None:
        return None
    try:
        return base64.b64decode(payload)
    except (binascii.Error, ValueError):
        return None


def _parse_and_validate(raw_bytes: bytes) -> Event | None:
    try:
        obj = json.loads(raw_bytes)
    except json.JSONDecodeError:
        return None
    try:
        return Event.model_validate(obj)
    except ValidationError:
        return None


def _event_to_row(ev: Event) -> dict[str, Any]:
    return {
        "event_id": ev.event_id,
        "device_id": ev.device_id,
        "timestamp": ev.timestamp,
        "event_type": ev.event_type.value,
        "severity": ev.severity.value,
        "device_type": ev.device_type,
        "facility_id": ev.facility_id,
        "firmware_version": ev.firmware_version,
        "error_code": ev.error_code,
        "message": ev.message,
        "metrics_json": json.dumps(ev.metrics) if ev.metrics is not None else None,
    }


def _partition_key(ts: datetime) -> tuple[str, str]:
    return ts.strftime("%Y-%m-%d"), ts.strftime("%H")


def _rows_to_parquet_bytes(rows: list[dict[str, Any]]) -> bytes:
    columns: dict[str, list[Any]] = {f.name: [] for f in EVENT_ARROW_SCHEMA}
    for row in rows:
        for name in columns:
            columns[name].append(row.get(name))
    table = pa.Table.from_pydict(columns, schema=EVENT_ARROW_SCHEMA)
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="snappy")
    return buf.getvalue()


def _emit_metric(cloudwatch: Any, name: str, value: int) -> None:
    try:
        cloudwatch.put_metric_data(
            Namespace=CW_NAMESPACE,
            MetricData=[{"MetricName": name, "Value": value, "Unit": "Count"}],
        )
    except Exception:  # noqa: BLE001 - metrics must never fail the handler
        log(LOGGER, logging.WARNING, "metric_emit_failed", metric=name, value=value)


def process_records(
    raw_records: Iterable[dict],
    bucket: str,
    s3_client: Any,
    cloudwatch_client: Any,
) -> dict[str, int]:
    """Parse records, remove duplicates, group them, and write to S3."""
    rejected = 0
    by_event_id: dict[str, Event] = {}
    for raw in raw_records:
        decoded = _decode_kinesis_record(raw)
        if decoded is None:
            rejected += 1
            continue
        ev = _parse_and_validate(decoded)
        if ev is None:
            rejected += 1
            continue
        by_event_id[ev.event_id] = ev

    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for ev in by_event_id.values():
        groups.setdefault(_partition_key(ev.timestamp), []).append(_event_to_row(ev))

    written = 0
    for (dt, hour), rows in groups.items():
        body = _rows_to_parquet_bytes(rows)
        key = f"events/dt={dt}/hour={hour}/batch-{uuid.uuid4()}.parquet"
        s3_client.put_object(Bucket=bucket, Key=key, Body=body)
        written += len(rows)
        log(
            LOGGER,
            logging.INFO,
            "batch_written",
            bucket=bucket,
            key=key,
            rows=len(rows),
        )

    _emit_metric(cloudwatch_client, METRIC_PROCESSED, written)
    _emit_metric(cloudwatch_client, METRIC_REJECTED, rejected)

    if rejected:
        log(LOGGER, logging.WARNING, "records_rejected", count=rejected)

    return {"processed": written, "rejected": rejected, "groups": len(groups)}


def handler(event: dict, _context: Any) -> dict[str, int]:
    bucket = os.environ.get(RAW_BUCKET_ENV)
    if not bucket:
        raise RuntimeError(f"{RAW_BUCKET_ENV} env var is required")
    s3 = boto3.client("s3")
    cloudwatch = boto3.client("cloudwatch")
    return process_records(event.get("Records", []), bucket, s3, cloudwatch)
