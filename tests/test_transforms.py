from pathlib import Path

import duckdb
import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "sample_events.parquet"
SQL_DIR = ROOT / "sql" / "transforms"

# Match the database names provisioned in `infra/terraform/glue.tf` so the
# `{raw_db}` / `{staging_db}` / `{curated_db}` placeholders in the SQL files
# expand to the same identifiers used in production.
RAW_DB = "device_log_raw"
STAGING_DB = "device_log_staging"
CURATED_DB = "device_log_curated"

# Each transform SQL file is a pure SELECT; the test wrapper materializes
# its result into the named DuckDB table, mirroring what the PySpark job
# does in production (compute -> write -> register).
TRANSFORM_TARGETS = {
    "stg_events_clean.sql": f"{STAGING_DB}.events_clean",
    "dim_facilities.sql": f"{CURATED_DB}.dim_facilities",
    "dim_firmware.sql": f"{CURATED_DB}.dim_firmware",
    "dim_devices_scd2.sql": f"{CURATED_DB}.dim_devices",
    "fct_events.sql": f"{CURATED_DB}.fct_events",
    "fct_errors.sql": f"{CURATED_DB}.fct_errors",
}

# Pre-creating an empty dim_devices before the first SCD2 run gives the merge
# query a table to read from and pins the column types so the UNION ALL
# inside the SQL doesn't mix TIMESTAMPTZ (DuckDB's default for
# `current_timestamp`) with plain TIMESTAMP.
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


def _new_db() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    for schema in (RAW_DB, STAGING_DB, CURATED_DB):
        con.execute(f"CREATE SCHEMA {schema}")
    con.execute(
        f"CREATE TABLE {RAW_DB}.events AS SELECT * FROM read_parquet('{FIXTURE}')"
    )
    return con


def _materialize(con: duckdb.DuckDBPyConnection, filename: str) -> None:
    # `.replace` (not `.format`) matches the production wrapper so any
    # literal `{` / `}` ever introduced into a SQL file behaves identically
    # in tests and in Glue.
    sql = (
        (SQL_DIR / filename).read_text()
        .replace("{raw_db}", RAW_DB)
        .replace("{staging_db}", STAGING_DB)
        .replace("{curated_db}", CURATED_DB)
    )
    target = TRANSFORM_TARGETS[filename]
    con.execute(f"CREATE OR REPLACE TABLE {target} AS {sql}")


@pytest.fixture()
def con():
    c = _new_db()
    try:
        yield c
    finally:
        c.close()


def test_stg_events_clean_dedupes_and_drops_invalid(con):
    raw_count = con.execute("SELECT COUNT(*) FROM device_log_raw.events").fetchone()[0]
    _materialize(con, "stg_events_clean.sql")

    clean_count, distinct_ids = con.execute(
        "SELECT COUNT(*), COUNT(DISTINCT event_id) FROM device_log_staging.events_clean"
    ).fetchone()
    assert clean_count == distinct_ids, "duplicates remain after dedup"
    assert clean_count < raw_count, "expected some rows to be dropped"

    null_count = con.execute(
        """
        SELECT COUNT(*) FROM device_log_staging.events_clean
        WHERE event_id IS NULL OR device_id IS NULL OR event_ts IS NULL
           OR event_type IS NULL OR severity IS NULL OR device_type IS NULL
           OR facility_id IS NULL OR firmware_version IS NULL
        """
    ).fetchone()[0]
    assert null_count == 0

    future_count = con.execute(
        "SELECT COUNT(*) FROM device_log_staging.events_clean "
        "WHERE event_ts > current_timestamp + INTERVAL 1 HOUR"
    ).fetchone()[0]
    assert future_count == 0


def test_dim_facilities_unique_keys_and_known_regions(con):
    _materialize(con, "stg_events_clean.sql")
    _materialize(con, "dim_facilities.sql")

    n_rows, n_keys = con.execute(
        "SELECT COUNT(*), COUNT(DISTINCT facility_sk) FROM device_log_curated.dim_facilities"
    ).fetchone()
    assert n_rows == n_keys and n_rows > 0

    regions = {r[0] for r in con.execute(
        "SELECT DISTINCT region FROM device_log_curated.dim_facilities"
    ).fetchall()}
    assert regions.issubset({"eu", "us", "ap"})


def test_dim_firmware_one_row_per_version(con):
    _materialize(con, "stg_events_clean.sql")
    _materialize(con, "dim_firmware.sql")

    staging_versions = con.execute(
        "SELECT COUNT(DISTINCT firmware_version) FROM device_log_staging.events_clean"
    ).fetchone()[0]
    dim_versions, dim_keys = con.execute(
        "SELECT COUNT(*), COUNT(DISTINCT firmware_sk) FROM device_log_curated.dim_firmware"
    ).fetchone()
    assert dim_versions == staging_versions
    assert dim_keys == dim_versions


def test_dim_devices_scd2_initial_then_firmware_change(con):
    _materialize(con, "stg_events_clean.sql")

    # First run: dim_devices is empty, so every device gets a brand-new row.
    con.execute(DIM_DEVICES_BOOTSTRAP)
    _materialize(con, "dim_devices_scd2.sql")

    distinct_devices = con.execute(
        "SELECT COUNT(DISTINCT device_id) FROM device_log_staging.events_clean"
    ).fetchone()[0]
    total_rows, current_rows = con.execute(
        "SELECT COUNT(*), SUM(CASE WHEN is_current THEN 1 ELSE 0 END) "
        "FROM device_log_curated.dim_devices"
    ).fetchone()
    assert total_rows == distinct_devices
    assert current_rows == distinct_devices

    bad_valid_to = con.execute(
        """
        SELECT COUNT(*) FROM device_log_curated.dim_devices
        WHERE (is_current = true AND valid_to IS NOT NULL)
           OR (is_current = false AND valid_to IS NULL)
        """
    ).fetchone()[0]
    assert bad_valid_to == 0

    # Second run: pick a device and flip its firmware in staging, expecting
    # SCD2 to close the old current row and open a new one for that device.
    target_device = con.execute(
        "SELECT device_id FROM device_log_staging.events_clean GROUP BY device_id "
        "ORDER BY COUNT(*) DESC LIMIT 1"
    ).fetchone()[0]
    new_firmware = "9.9.9"
    con.execute(
        "UPDATE device_log_staging.events_clean SET firmware_version = ? WHERE device_id = ?",
        [new_firmware, target_device],
    )

    _materialize(con, "dim_devices_scd2.sql")

    rows_for_target = con.execute(
        "SELECT is_current, valid_to, firmware_version FROM device_log_curated.dim_devices "
        "WHERE device_id = ? ORDER BY valid_from",
        [target_device],
    ).fetchall()
    assert len(rows_for_target) == 2
    closed, opened = rows_for_target
    assert closed[0] is False and closed[1] is not None
    assert opened[0] is True and opened[1] is None
    assert opened[2] == new_firmware

    new_total = con.execute("SELECT COUNT(*) FROM device_log_curated.dim_devices").fetchone()[0]
    assert new_total == total_rows + 1


def test_fct_events_resolves_every_device_sk(con):
    _materialize(con, "stg_events_clean.sql")
    con.execute(DIM_DEVICES_BOOTSTRAP)
    _materialize(con, "dim_devices_scd2.sql")
    _materialize(con, "fct_events.sql")

    staging_count = con.execute(
        "SELECT COUNT(*) FROM device_log_staging.events_clean"
    ).fetchone()[0]
    fct_count = con.execute("SELECT COUNT(*) FROM device_log_curated.fct_events").fetchone()[0]
    assert fct_count == staging_count

    null_sk = con.execute(
        "SELECT COUNT(*) FROM device_log_curated.fct_events WHERE device_sk IS NULL"
    ).fetchone()[0]
    assert null_sk == 0

    unresolved = con.execute(
        """
        SELECT COUNT(*) FROM device_log_curated.fct_events f
        LEFT JOIN device_log_curated.dim_devices d ON f.device_sk = d.device_sk
        WHERE d.device_sk IS NULL
        """
    ).fetchone()[0]
    assert unresolved == 0


def test_fct_errors_is_subset_of_errors_in_staging(con):
    _materialize(con, "stg_events_clean.sql")
    con.execute(DIM_DEVICES_BOOTSTRAP)
    _materialize(con, "dim_devices_scd2.sql")
    _materialize(con, "fct_events.sql")
    _materialize(con, "fct_errors.sql")

    staging_errors = con.execute(
        "SELECT COUNT(*) FROM device_log_staging.events_clean WHERE event_type = 'error'"
    ).fetchone()[0]
    fct_errors_count = con.execute(
        "SELECT COUNT(*) FROM device_log_curated.fct_errors"
    ).fetchone()[0]
    assert fct_errors_count == staging_errors

    null_codes = con.execute(
        "SELECT COUNT(*) FROM device_log_curated.fct_errors WHERE error_code IS NULL"
    ).fetchone()[0]
    assert null_codes == 0
