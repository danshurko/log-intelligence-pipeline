"""Tests for the Kinesis Lambda handler entry point.

This module checks env vars, boto3 setup, and Kinesis event handling.
"""

import base64
import json

import pytest

from src.ingestion import lambda_handler


def _kinesis_record(payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    return {"kinesis": {"data": base64.b64encode(data).decode("ascii")}}


def _valid_event(event_id: str = "ev-handler-1") -> dict:
    return {
        "event_id": event_id,
        "device_id": "dev-0001",
        "timestamp": "2026-05-19T10:15:30Z",
        "event_type": "info",
        "severity": "info",
        "device_type": "sensor",
        "facility_id": "fac-eu-01",
        "firmware_version": "1.2.0",
        "error_code": None,
        "message": "heartbeat ok",
        "metrics": {"cpu_pct": 12.5},
    }


class _FakeS3:
    def __init__(self) -> None:
        self.puts: list[dict] = []

    def put_object(self, *, Bucket, Key, Body):  # noqa: N803
        self.puts.append({"Bucket": Bucket, "Key": Key, "Body": Body})
        return {}


class _FakeCloudWatch:
    def __init__(self) -> None:
        self.metrics: list[dict] = []

    def put_metric_data(self, *, Namespace, MetricData):  # noqa: N803
        for m in MetricData:
            self.metrics.append({"Namespace": Namespace, **m})
        return {}


def _install_boto3_stub(monkeypatch, s3, cw):
    def fake_client(service: str, *_a, **_kw):
        if service == "s3":
            return s3
        if service == "cloudwatch":
            return cw
        raise AssertionError(f"unexpected boto3 client: {service}")

    monkeypatch.setattr(lambda_handler.boto3, "client", fake_client)


def test_handler_raises_when_raw_bucket_env_missing(monkeypatch):
    monkeypatch.delenv(lambda_handler.RAW_BUCKET_ENV, raising=False)
    with pytest.raises(RuntimeError, match=lambda_handler.RAW_BUCKET_ENV):
        lambda_handler.handler({"Records": []}, None)


def test_handler_routes_kinesis_records_to_s3_and_cloudwatch(monkeypatch):
    s3 = _FakeS3()
    cw = _FakeCloudWatch()
    monkeypatch.setenv(lambda_handler.RAW_BUCKET_ENV, "test-bucket")
    _install_boto3_stub(monkeypatch, s3, cw)

    event = {"Records": [_kinesis_record(_valid_event("ev-a"))]}
    result = lambda_handler.handler(event, None)

    assert result == {"processed": 1, "rejected": 0, "groups": 1}
    assert len(s3.puts) == 1
    assert s3.puts[0]["Bucket"] == "test-bucket"
    assert {m["MetricName"] for m in cw.metrics} == {
        lambda_handler.METRIC_PROCESSED,
        lambda_handler.METRIC_REJECTED,
    }


def test_handler_handles_empty_event_without_crashing(monkeypatch):
    s3 = _FakeS3()
    cw = _FakeCloudWatch()
    monkeypatch.setenv(lambda_handler.RAW_BUCKET_ENV, "test-bucket")
    _install_boto3_stub(monkeypatch, s3, cw)

    result = lambda_handler.handler({}, None)

    assert result == {"processed": 0, "rejected": 0, "groups": 0}
    assert s3.puts == []
    processed = [m for m in cw.metrics if m["MetricName"] == lambda_handler.METRIC_PROCESSED]
    assert processed and processed[0]["Value"] == 0
