import io
import json
import random
import uuid
from datetime import UTC, datetime, timedelta

import boto3
import click
import pyarrow as pa
import pyarrow.parquet as pq

from src.generator.devices import generate_fleet
from src.generator.dirtiness import (
    MALFORMED_MARKER,
    DirtinessConfig,
    inject_dirtiness,
)
from src.generator.scenarios import SCENARIOS

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


def _parse_iso(ts: str | datetime | None) -> datetime | None:
    if ts is None or isinstance(ts, datetime):
        return ts
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def records_to_table(records: list[dict]) -> pa.Table:
    columns: dict[str, list] = {f.name: [] for f in EVENT_ARROW_SCHEMA}
    for r in records:
        columns["event_id"].append(r.get("event_id"))
        columns["device_id"].append(r.get("device_id"))
        columns["timestamp"].append(_parse_iso(r.get("timestamp")))
        columns["event_type"].append(r.get("event_type"))
        columns["severity"].append(r.get("severity"))
        columns["device_type"].append(r.get("device_type"))
        columns["facility_id"].append(r.get("facility_id"))
        columns["firmware_version"].append(r.get("firmware_version"))
        columns["error_code"].append(r.get("error_code"))
        columns["message"].append(r.get("message"))
        metrics = r.get("metrics")
        columns["metrics_json"].append(json.dumps(metrics) if metrics is not None else None)
    return pa.Table.from_pydict(columns, schema=EVENT_ARROW_SCHEMA)


def _strip_markers(records: list[dict]) -> list[dict]:
    return [
        {k: v for k, v in r.items() if not k.startswith("_")}
        for r in records
        if not r.get(MALFORMED_MARKER)
    ]


def _hour_floor_utc(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


@click.group()
def cli() -> None:
    """Device log event generator."""


@cli.command()
@click.option("--days", type=int, default=7, show_default=True,
              help="Number of days of history to generate.")
@click.option("--devices", "n_devices", type=int, default=50, show_default=True,
              help="Number of devices in the simulated fleet.")
@click.option(
    "--scenario",
    type=click.Choice(list(SCENARIOS), case_sensitive=False),
    default="normal",
    show_default=True,
)
@click.option("--bucket", required=True, help="S3 bucket name for the raw zone.")
@click.option("--dirty/--no-dirty", default=True, show_default=True,
              help="Inject probabilistic data quality issues.")
@click.option("--seed", type=int, default=42, show_default=True,
              help="Seed for the fleet and per-hour generators.")
def backfill(
    days: int,
    n_devices: int,
    scenario: str,
    bucket: str,
    dirty: bool,
    seed: int,
) -> None:
    """Generate `days` of historical events and write hourly Parquet to S3 raw."""
    s3 = boto3.client("s3")
    fleet = generate_fleet(n_devices, seed=seed)
    scenario_fn = SCENARIOS[scenario]
    dirtiness_config = DirtinessConfig()

    end = _hour_floor_utc(datetime.now(UTC))
    start = end - timedelta(days=days)

    hour_cursor = start
    hour_index = 0
    while hour_cursor < end:
        hour_end = hour_cursor + timedelta(hours=1)
        rng = random.Random(seed + hour_index)
        records = scenario_fn(fleet, hour_cursor, hour_end, rng)
        if dirty:
            records = inject_dirtiness(records, dirtiness_config, seed=seed + hour_index)
        records = _strip_markers(records)

        if records:
            table = records_to_table(records)
            buf = io.BytesIO()
            pq.write_table(table, buf, compression="snappy")
            key = (
                f"dt={hour_cursor.strftime('%Y-%m-%d')}/"
                f"hour={hour_cursor.strftime('%H')}/"
                f"batch-{uuid.uuid4()}.parquet"
            )
            s3.put_object(Bucket=bucket, Key=key, Body=buf.getvalue())
            click.echo(
                f"{hour_cursor.isoformat()}  events={len(records):>6}  s3://{bucket}/{key}"
            )
        else:
            click.echo(f"{hour_cursor.isoformat()}  events=     0  (no data)")

        hour_cursor = hour_end
        hour_index += 1


if __name__ == "__main__":
    cli()
