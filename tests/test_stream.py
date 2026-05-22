import json
import random
from datetime import UTC, datetime

from src.generator.cli import _generate_tick, _record_to_kinesis_entry
from src.generator.devices import generate_fleet
from src.generator.dirtiness import MALFORMED_BYTES, MALFORMED_MARKER, MALFORMED_RAW_PAYLOAD


def test_generate_tick_produces_target_rate():
    fleet = generate_fleet(20, seed=42)
    rng = random.Random(0)
    tick = _generate_tick(fleet, rate=50.0, tick_start=datetime(2026, 5, 19, tzinfo=UTC), rng=rng)
    assert len(tick) == 50
    for ev in tick:
        assert ev["event_id"].startswith("ev-")
        assert ev["device_id"].startswith("dev-")


def test_generate_tick_empty_for_zero_rate():
    fleet = generate_fleet(5, seed=1)
    rng = random.Random(0)
    assert _generate_tick(fleet, 0.0, datetime(2026, 5, 19, tzinfo=UTC), rng) == []


def test_record_to_kinesis_entry_clean_record_serializes_json():
    record = {
        "event_id": "ev-1",
        "device_id": "dev-42",
        "timestamp": "2026-05-19T10:00:00Z",
        "event_type": "info",
    }
    entry = _record_to_kinesis_entry(record)
    assert entry["PartitionKey"] == "dev-42"
    payload = json.loads(entry["Data"])
    assert payload == record


def test_record_to_kinesis_entry_malformed_record_passes_raw_bytes():
    record = {
        "event_id": "ev-1",
        "device_id": "dev-42",
        MALFORMED_MARKER: True,
        MALFORMED_RAW_PAYLOAD: MALFORMED_BYTES,
    }
    entry = _record_to_kinesis_entry(record)
    assert entry["Data"] == MALFORMED_BYTES.encode("utf-8")
    assert entry["PartitionKey"] == "dev-42"
