import random
from datetime import UTC, datetime, timedelta

import pytest

from src.generator.devices import generate_fleet
from src.generator.dirtiness import (
    FUTURE_MAX_SECONDS,
    FUTURE_MIN_SECONDS,
    MALFORMED_MARKER,
    OPTIONAL_FIELDS,
    OUT_OF_ORDER_MAX_SECONDS,
    OUT_OF_ORDER_MIN_SECONDS,
    DirtinessConfig,
    inject_dirtiness,
)
from src.generator.scenarios import SCENARIOS, normal


def test_fleet_stable_across_runs_with_same_seed():
    a = generate_fleet(50, seed=42)
    b = generate_fleet(50, seed=42)
    assert a == b
    assert len({d.device_id for d in a}) == 50


def test_fleet_differs_with_different_seed():
    a = generate_fleet(50, seed=1)
    b = generate_fleet(50, seed=2)
    assert a != b


@pytest.mark.parametrize("scenario_name", list(SCENARIOS.keys()))
def test_scenario_produces_events(scenario_name):
    fleet = generate_fleet(10, seed=42)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(hours=1)
    rng = random.Random(42)
    events = SCENARIOS[scenario_name](fleet, start, end, rng)
    assert events, f"scenario {scenario_name!r} produced no events"
    for ev in events:
        assert ev["event_id"].startswith("ev-")
        assert ev["device_id"].startswith("dev-")
        assert ev["event_type"] in {"info", "warning", "error", "metric"}


def test_silent_device_actually_falls_silent():
    fleet = generate_fleet(10, seed=42)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(hours=1)
    rng = random.Random(42)
    events = SCENARIOS["silent-device"](fleet, start, end, rng)

    per_device_counts: dict[str, int] = {}
    for ev in events:
        per_device_counts[ev["device_id"]] = per_device_counts.get(ev["device_id"], 0) + 1

    min_device = min(per_device_counts, key=lambda d: per_device_counts[d])
    max_device = max(per_device_counts, key=lambda d: per_device_counts[d])
    assert per_device_counts[min_device] < per_device_counts[max_device]


def _generate_base_events(n: int) -> list[dict]:
    fleet = generate_fleet(30, seed=42)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rng = random.Random(0)
    end = start + timedelta(seconds=max(60, n))
    events = normal(fleet, start, end, rng)
    return events[:n]


def test_dirtiness_rates_within_tolerance():
    n = 20000
    base = _generate_base_events(n)
    assert len(base) == n

    original_ts = {r["event_id"]: r["timestamp"] for r in base}
    assert len(original_ts) == n, "test invariant: base event ids must be unique"

    config = DirtinessConfig()
    dirty = inject_dirtiness(base, config, seed=123)

    duplicate_count = len(dirty) - n

    future_count = 0
    out_of_order_count = 0
    for r in dirty:
        orig = original_ts.get(r["event_id"])
        if orig is None:
            continue
        ts = r["timestamp"]
        if ts > orig:
            future_count += 1
        elif ts < orig:
            out_of_order_count += 1

    malformed_count = sum(1 for r in dirty if r.get(MALFORMED_MARKER))
    missing_count = sum(1 for r in dirty if any(field not in r for field in OPTIONAL_FIELDS))

    cases = [
        ("duplicate", duplicate_count, config.duplicate_rate),
        ("future", future_count, config.future_timestamp_rate),
        ("out_of_order", out_of_order_count, config.out_of_order_rate),
        ("malformed", malformed_count, config.malformed_json_rate),
        ("missing", missing_count, config.missing_fields_rate),
    ]
    for label, observed, rate in cases:
        expected = rate * n
        # Allow tolerance for low-rate categories: at least 10 events or 40%.
        margin = max(10, expected * 0.4)
        assert abs(observed - expected) <= margin, (
            f"{label}: observed={observed} expected={expected:.1f} margin={margin:.1f}"
        )


def test_timestamp_drift_within_documented_bounds():
    base = _generate_base_events(5000)
    original_ts = {r["event_id"]: r["timestamp"] for r in base}
    config = DirtinessConfig(
        missing_fields_rate=0.0,
        duplicate_rate=0.0,
        malformed_json_rate=0.0,
        out_of_order_rate=0.3,
        future_timestamp_rate=0.3,
    )
    dirty = inject_dirtiness(base, config, seed=11)

    from datetime import datetime

    def _parse(ts: str) -> datetime:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))

    saw_future = False
    saw_past = False
    for r in dirty:
        orig = _parse(original_ts[r["event_id"]])
        ts = _parse(r["timestamp"])
        delta_s = (ts - orig).total_seconds()
        if delta_s > 0:
            assert FUTURE_MIN_SECONDS <= delta_s <= FUTURE_MAX_SECONDS, (
                f"future drift {delta_s}s outside bounds"
            )
            saw_future = True
        elif delta_s < 0:
            assert -OUT_OF_ORDER_MAX_SECONDS <= delta_s <= -OUT_OF_ORDER_MIN_SECONDS, (
                f"out-of-order drift {delta_s}s outside bounds"
            )
            saw_past = True

    assert saw_future and saw_past, "expected both drift directions at these rates"


def test_dirtiness_is_deterministic_given_seed():
    base = _generate_base_events(500)
    a = inject_dirtiness(base, DirtinessConfig(), seed=7)
    b = inject_dirtiness(base, DirtinessConfig(), seed=7)
    assert a == b
