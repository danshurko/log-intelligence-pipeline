"""Rebuild the sample_events.parquet test fixture.

Run: `uv run python -m tests.fixtures._generate_sample_events`
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow.parquet as pq

from src.generator.cli import records_to_table
from src.generator.devices import generate_fleet
from src.generator.dirtiness import (
    MALFORMED_MARKER,
    DirtinessConfig,
    inject_dirtiness,
)
from src.generator.scenarios import normal

FIXTURE_PATH = Path(__file__).parent / "sample_events.parquet"

# A timestamp that is always more than 1 hour in the future.
FAR_FUTURE = "9999-12-31T23:59:00Z"
FAR_FUTURE_COUNT = 5

# 15 devices over 60 seconds gives about 500 records.
SEED = 4242
N_DEVICES = 15
WINDOW_SECONDS = 60


def _strip_markers(records: list[dict]) -> list[dict]:
    return [
        {k: v for k, v in r.items() if not k.startswith("_")}
        for r in records
        if not r.get(MALFORMED_MARKER)
    ]


def build_records() -> list[dict]:
    fleet = generate_fleet(N_DEVICES, seed=SEED)
    rng = random.Random(SEED)
    start = datetime(2026, 5, 19, 12, 0, 0, tzinfo=UTC)
    end = start + timedelta(seconds=WINDOW_SECONDS)

    base = normal(fleet, start, end, rng)
    # Add only duplicates and missing fields here.
    cfg = DirtinessConfig(
        missing_fields_rate=0.02,
        duplicate_rate=0.05,
        malformed_json_rate=0.0,
        out_of_order_rate=0.0,
        future_timestamp_rate=0.0,
    )
    dirty = inject_dirtiness(base, cfg, seed=SEED)
    clean = _strip_markers(dirty)

    # Use the first event as a template for the future rows.
    template = clean[0]
    for i in range(FAR_FUTURE_COUNT):
        clean.append(
            {
                **template,
                "event_id": f"ev-future-{i:02d}",
                "timestamp": FAR_FUTURE,
            }
        )
    return clean


def main() -> None:
    records = build_records()
    table = records_to_table(records)
    pq.write_table(table, FIXTURE_PATH, compression="snappy")
    print(f"wrote {len(records)} rows -> {FIXTURE_PATH}")


if __name__ == "__main__":
    main()
