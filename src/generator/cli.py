import io
import json
import random
import time
import uuid
from datetime import UTC, datetime, timedelta

import boto3
import click
import pyarrow as pa
import pyarrow.parquet as pq

from src.generator.devices import BASE_ERROR_RATE, generate_fleet
from src.generator.dirtiness import (
    MALFORMED_BYTES,
    MALFORMED_MARKER,
    MALFORMED_RAW_PAYLOAD,
    DirtinessConfig,
    inject_dirtiness,
)
from src.generator.scenarios import SCENARIOS, make_event

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


KINESIS_PUT_RECORDS_BATCH: int = 25
STREAM_TICK_SECONDS: float = 1.0


def _record_to_kinesis_entry(record: dict) -> dict:
    """Serialize one generator record for `kinesis.put_records`.

    Malformed records ride through as their raw (intentionally broken) JSON
    payload so the Lambda's `RecordsRejected` metric has something to count.
    """
    if record.get(MALFORMED_MARKER):
        payload = record.get(MALFORMED_RAW_PAYLOAD, MALFORMED_BYTES)
        data = payload.encode("utf-8") if isinstance(payload, str) else payload
    else:
        clean = {k: v for k, v in record.items() if not k.startswith("_")}
        data = json.dumps(clean).encode("utf-8")
    return {"Data": data, "PartitionKey": record.get("device_id", "unknown")}


def _put_records(kinesis, stream_name: str, records: list[dict]) -> int:
    sent = 0
    for i in range(0, len(records), KINESIS_PUT_RECORDS_BATCH):
        chunk = records[i : i + KINESIS_PUT_RECORDS_BATCH]
        entries = [_record_to_kinesis_entry(r) for r in chunk]
        resp = kinesis.put_records(StreamName=stream_name, Records=entries)
        failed = resp.get("FailedRecordCount", 0)
        sent += len(entries) - failed
        if failed:
            click.echo(f"warn: {failed} record(s) failed in put_records batch", err=True)
    return sent


def _generate_tick(
    fleet,
    rate: float,
    tick_start: datetime,
    rng: random.Random,
) -> list[dict]:
    """Generate ~`rate` events spread evenly across a 1-second tick.

    Devices are picked uniformly at random so the per-tick output rate is
    decoupled from fleet size and per-device-type natural rates. This keeps
    `--rate` a true throttle for the streaming demo.
    """
    n = max(0, int(round(rate * STREAM_TICK_SECONDS)))
    if not fleet or n == 0:
        return []
    out: list[dict] = []
    for i in range(n):
        device = rng.choice(fleet)
        offset = (i + rng.random()) * (STREAM_TICK_SECONDS / max(n, 1))
        ts = tick_start + timedelta(seconds=offset)
        out.append(make_event(device, ts, rng.random() < BASE_ERROR_RATE, rng))
    return out


@cli.command()
@click.option("--rate", type=float, default=10.0, show_default=True,
              help="Target events per second.")
@click.option("--duration", type=int, default=-1, show_default=True,
              help="Seconds to run; -1 streams until interrupted.")
@click.option(
    "--scenario",
    type=click.Choice(list(SCENARIOS), case_sensitive=False),
    default="normal",
    show_default=True,
)
@click.option("--devices", "n_devices", type=int, default=50, show_default=True,
              help="Number of devices in the simulated fleet.")
@click.option("--stream-name", required=True, help="Kinesis data stream name.")
@click.option("--dirty/--no-dirty", default=True, show_default=True,
              help="Inject probabilistic data quality issues.")
@click.option("--seed", type=int, default=42, show_default=True)
def stream(
    rate: float,
    duration: int,
    scenario: str,
    n_devices: int,
    stream_name: str,
    dirty: bool,
    seed: int,
) -> None:
    """Stream events live into a Kinesis data stream at the target rate."""
    # `scenario` is accepted for parity with `backfill` but stream mode uses
    # the baseline error rate; richer scenario shaping over a live stream
    # would need stateful burst timers and is out of scope here.
    del scenario
    fleet = generate_fleet(n_devices, seed=seed)
    rng = random.Random(seed)
    kinesis = boto3.client("kinesis")
    dirtiness_config = DirtinessConfig()

    started = time.monotonic()
    tick_index = 0
    total_sent = 0
    while True:
        if duration > 0 and time.monotonic() - started >= duration:
            break
        tick_start = datetime.now(UTC)
        records = _generate_tick(fleet, rate, tick_start, rng)
        if dirty:
            records = inject_dirtiness(records, dirtiness_config, seed=seed + tick_index)
        if records:
            sent = _put_records(kinesis, stream_name, records)
            total_sent += sent
            click.echo(
                f"{tick_start.isoformat()}  sent={sent:>4}  total={total_sent}"
            )
        tick_index += 1
        sleep_for = started + tick_index * STREAM_TICK_SECONDS - time.monotonic()
        if sleep_for > 0:
            time.sleep(sleep_for)


if __name__ == "__main__":
    cli()
