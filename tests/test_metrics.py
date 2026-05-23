from pathlib import Path

import duckdb
import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "sample_events.parquet"
SQL_TRANSFORMS = ROOT / "sql" / "transforms"
SQL_METRICS = ROOT / "sql" / "metrics"

RAW_DB = "device_log_raw"
STAGING_DB = "device_log_staging"
CURATED_DB = "device_log_curated"

TRANSFORM_TARGETS = {
    "stg_events_clean.sql": f"{STAGING_DB}.events_clean",
    "dim_facilities.sql": f"{CURATED_DB}.dim_facilities",
    "dim_firmware.sql": f"{CURATED_DB}.dim_firmware",
    "dim_devices_scd2.sql": f"{CURATED_DB}.dim_devices",
    "fct_events.sql": f"{CURATED_DB}.fct_events",
    "fct_errors.sql": f"{CURATED_DB}.fct_errors",
}

# Mirrors the bootstrap from test_transforms so the first SCD2 run has a
# typed target to read from.
DIM_DEVICES_BOOTSTRAP = f"""
CREATE TABLE {CURATED_DB}.dim_devices (
  device_sk BIGINT,
  device_id VARCHAR,
  firmware_version VARCHAR,
  facility_id VARCHAR,
  valid_from TIMESTAMP,
  valid_to TIMESTAMP,
  is_current BOOLEAN
)
"""


def _sql_text(directory: Path, filename: str) -> str:
    return (
        (directory / filename)
        .read_text()
        .replace("{raw_db}", RAW_DB)
        .replace("{staging_db}", STAGING_DB)
        .replace("{curated_db}", CURATED_DB)
    )


def _materialize_transform(con: duckdb.DuckDBPyConnection, filename: str) -> None:
    sql = _sql_text(SQL_TRANSFORMS, filename)
    target = TRANSFORM_TARGETS[filename]
    con.execute(f"CREATE OR REPLACE TABLE {target} AS {sql}")


def _run_metric(con: duckdb.DuckDBPyConnection, filename: str) -> list[tuple]:
    return con.execute(_sql_text(SQL_METRICS, filename)).fetchall()


@pytest.fixture()
def con():
    c = duckdb.connect()
    for schema in (RAW_DB, STAGING_DB, CURATED_DB):
        c.execute(f"CREATE SCHEMA {schema}")
    # Shift the fixture so the latest "real" event lands at current_timestamp,
    # then cast away the UTC tz to mirror what Spark writes to Parquet (naive
    # UTC). Without that cast, DuckDB's `date_trunc` on TIMESTAMPTZ tries to
    # import pytz and the anomaly query fails.
    c.execute(
        f"""
        CREATE TABLE {RAW_DB}.events AS
        WITH src AS (
          SELECT * FROM read_parquet('{FIXTURE}')
        ),
        shift AS (
          SELECT CAST(current_timestamp AS TIMESTAMP) - max(CAST(timestamp AS TIMESTAMP)) AS delta
          FROM src
          WHERE CAST(timestamp AS TIMESTAMP)
                <= CAST(current_timestamp AS TIMESTAMP) + INTERVAL 1 HOUR
        )
        SELECT
          event_id, device_id,
          CAST(timestamp AS TIMESTAMP) + (SELECT delta FROM shift) AS timestamp,
          event_type, severity, device_type, facility_id, firmware_version,
          error_code, message, metrics_json
        FROM src
        """
    )
    _materialize_transform(c, "stg_events_clean.sql")
    _materialize_transform(c, "dim_facilities.sql")
    _materialize_transform(c, "dim_firmware.sql")
    c.execute(DIM_DEVICES_BOOTSTRAP)
    _materialize_transform(c, "dim_devices_scd2.sql")
    _materialize_transform(c, "fct_events.sql")
    _materialize_transform(c, "fct_errors.sql")
    try:
        yield c
    finally:
        c.close()


def test_error_rate_by_device_returns_one_row_per_active_device(con):
    rows = _run_metric(con, "01_error_rate_by_device.sql")
    assert len(rows) > 0
    for _device_id, total, errors, rate in rows:
        assert total > 0
        assert 0 <= errors <= total
        assert 0.0 <= rate <= 1.0
    # Sorted descending by error_rate.
    rates = [r[3] for r in rows]
    assert rates == sorted(rates, reverse=True)


def test_top_error_codes_capped_at_10_and_ordered(con):
    rows = _run_metric(con, "02_top_error_codes.sql")
    assert 0 < len(rows) <= 10
    counts = [c for _, c in rows]
    assert counts == sorted(counts, reverse=True)
    assert all(code is not None for code, _ in rows)


def test_mtbe_per_device_only_counts_devices_with_multiple_errors(con):
    rows = _run_metric(con, "03_mtbe_per_device.sql")
    # Devices with a single error in the window have no LAG row and are
    # excluded; rows that come back must report a positive, real gap count.
    for _device_id, gaps, mtbe_seconds in rows:
        assert gaps >= 1
        assert mtbe_seconds is not None
        assert mtbe_seconds >= 0


def test_silent_devices_flags_devices_with_no_recent_events(con):
    # All fixture events land in the last ~60s, so the natural result is
    # empty. Backdate one device's events by an hour and assert it surfaces.
    target_device, target_sk = con.execute(
        f"SELECT device_id, device_sk FROM {CURATED_DB}.dim_devices "
        "WHERE is_current = true ORDER BY device_id LIMIT 1"
    ).fetchone()
    con.execute(
        f"UPDATE {CURATED_DB}.fct_events SET event_ts = event_ts - INTERVAL 1 HOUR "
        "WHERE device_sk = ?",
        [target_sk],
    )
    rows = _run_metric(con, "04_silent_devices.sql")
    assert target_device in {r[0] for r in rows}


def test_firmware_cohort_errors_versions_subset_of_known(con):
    rows = _run_metric(con, "05_firmware_cohort_errors.sql")
    assert len(rows) > 0
    known = {
        r[0]
        for r in con.execute(
            f"SELECT DISTINCT firmware_version FROM {CURATED_DB}.dim_devices"
        ).fetchall()
    }
    for version, total, errors, rate in rows:
        assert version in known
        assert total > 0
        assert 0 <= errors <= total
        assert 0.0 <= rate <= 1.0


def test_anomaly_error_burst_detects_injected_burst(con):
    # Replace one device's error history with a flat 1-per-hour baseline
    # plus a 50-error spike, so the burst clearly exceeds mean + 3*stddev.
    target_sk = con.execute(
        f"SELECT device_sk FROM {CURATED_DB}.fct_errors "
        "GROUP BY device_sk ORDER BY COUNT(*) DESC LIMIT 1"
    ).fetchone()[0]
    con.execute(
        f"DELETE FROM {CURATED_DB}.fct_errors WHERE device_sk = ?", [target_sk]
    )
    con.execute(
        f"""
        INSERT INTO {CURATED_DB}.fct_errors
        SELECT
          'normal-' || CAST(h AS VARCHAR) AS event_id,
          {target_sk} AS device_sk,
          current_timestamp - INTERVAL 1 HOUR * (h + 3) AS event_ts,
          'E_BASELINE' AS error_code,
          NULL AS message
        FROM range(24) t(h)
        """
    )
    con.execute(
        f"""
        INSERT INTO {CURATED_DB}.fct_errors
        SELECT
          'burst-' || CAST(s AS VARCHAR) AS event_id,
          {target_sk} AS device_sk,
          current_timestamp - INTERVAL 2 HOUR AS event_ts,
          'E_BURST' AS error_code,
          NULL AS message
        FROM range(50) t(s)
        """
    )
    rows = _run_metric(con, "06_anomaly_error_burst.sql")
    burst_devices = {r[0] for r in rows}
    target_device = con.execute(
        f"SELECT device_id FROM {CURATED_DB}.dim_devices WHERE device_sk = ?",
        [target_sk],
    ).fetchone()[0]
    assert target_device in burst_devices
    for _device_id, _hour, count, mean, std in rows:
        assert count > mean + 3 * std
