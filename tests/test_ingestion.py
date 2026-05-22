import base64
import io
import json

import pyarrow.parquet as pq

from src.ingestion.lambda_handler import (
    METRIC_PROCESSED,
    METRIC_REJECTED,
    process_records,
)


def _kinesis_record(payload: dict | str | bytes) -> dict:
    if isinstance(payload, dict):
        data = json.dumps(payload).encode("utf-8")
    elif isinstance(payload, str):
        data = payload.encode("utf-8")
    else:
        data = payload
    return {"kinesis": {"data": base64.b64encode(data).decode("ascii")}}


def _valid_event(event_id: str = "ev-1", ts: str = "2026-05-19T10:15:30Z") -> dict:
    return {
        "event_id": event_id,
        "device_id": "dev-0001",
        "timestamp": ts,
        "event_type": "info",
        "severity": "info",
        "device_type": "sensor",
        "facility_id": "fac-eu-01",
        "firmware_version": "1.2.0",
        "error_code": None,
        "message": "heartbeat ok",
        "metrics": {"cpu_pct": 12.5},
    }


class FakeS3:
    def __init__(self) -> None:
        self.puts: list[dict] = []

    def put_object(self, *, Bucket: str, Key: str, Body: bytes) -> dict:  # noqa: N803
        self.puts.append({"Bucket": Bucket, "Key": Key, "Body": Body})
        return {}


class FakeCloudWatch:
    def __init__(self) -> None:
        self.metrics: list[dict] = []

    def put_metric_data(self, *, Namespace: str, MetricData: list[dict]) -> dict:  # noqa: N803
        for m in MetricData:
            self.metrics.append({"Namespace": Namespace, **m})
        return {}


def test_process_records_writes_grouped_parquet_to_s3():
    s3 = FakeS3()
    cw = FakeCloudWatch()
    records = [
        _kinesis_record(_valid_event("ev-1", "2026-05-19T10:15:30Z")),
        _kinesis_record(_valid_event("ev-2", "2026-05-19T10:45:00Z")),
        _kinesis_record(_valid_event("ev-3", "2026-05-19T11:05:00Z")),
    ]

    result = process_records(records, "test-raw", s3, cw)

    assert result == {"processed": 3, "rejected": 0, "groups": 2}
    assert len(s3.puts) == 2
    keys = sorted(p["Key"] for p in s3.puts)
    assert keys[0].startswith("dt=2026-05-19/hour=10/")
    assert keys[1].startswith("dt=2026-05-19/hour=11/")

    # Parquet content for hour=10 should hold both events.
    h10 = next(p for p in s3.puts if "hour=10" in p["Key"])
    table = pq.read_table(io.BytesIO(h10["Body"]))
    assert sorted(table.column("event_id").to_pylist()) == ["ev-1", "ev-2"]


def test_process_records_soft_skips_invalid_json_and_validation_errors():
    s3 = FakeS3()
    cw = FakeCloudWatch()
    records = [
        _kinesis_record(_valid_event("ev-1")),
        _kinesis_record("{not valid json"),
        _kinesis_record({"event_id": "missing-fields"}),
    ]

    result = process_records(records, "test-raw", s3, cw)

    assert result["processed"] == 1
    assert result["rejected"] == 2
    rejected = [m for m in cw.metrics if m["MetricName"] == METRIC_REJECTED]
    processed = [m for m in cw.metrics if m["MetricName"] == METRIC_PROCESSED]
    assert rejected and rejected[0]["Value"] == 2
    assert processed and processed[0]["Value"] == 1


def test_process_records_dedupes_within_batch():
    s3 = FakeS3()
    cw = FakeCloudWatch()
    records = [
        _kinesis_record(_valid_event("ev-1")),
        _kinesis_record(_valid_event("ev-1")),
        _kinesis_record(_valid_event("ev-1")),
    ]

    result = process_records(records, "test-raw", s3, cw)

    assert result["processed"] == 1
    assert len(s3.puts) == 1


def test_process_records_propagates_s3_failure():
    class BoomS3:
        def put_object(self, **_kw):
            raise RuntimeError("s3 is down")

    cw = FakeCloudWatch()
    records = [_kinesis_record(_valid_event("ev-1"))]

    try:
        process_records(records, "test-raw", BoomS3(), cw)
    except RuntimeError as exc:
        assert "s3 is down" in str(exc)
    else:
        raise AssertionError("expected RuntimeError to propagate")
