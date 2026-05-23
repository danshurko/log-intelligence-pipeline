-- SCD Type 2 merge for the device dimension. Returns the full next-state of
-- the table — historical rows preserved, changed currents closed, brand-new
-- versions opened. The orchestrator is responsible for cache+materialize
-- before overwriting the underlying path (the SELECT reads from the same
-- table it's about to replace).
--
-- Attribute comparisons use `IS NOT DISTINCT FROM` so that a NULL facility
-- is treated as a real value (matches NULL, differs from any non-NULL),
-- rather than swallowed by SQL's three-valued logic.
WITH
  -- Latest observed state per device in this batch.
  current_per_device AS (
    SELECT device_id, firmware_version, facility_id
    FROM (
      SELECT
        device_id,
        firmware_version,
        facility_id,
        ROW_NUMBER() OVER (PARTITION BY device_id ORDER BY event_ts DESC) AS rn
      FROM {staging_db}.events_clean
    ) t
    WHERE rn = 1
  ),

  existing_current AS (
    SELECT *
    FROM {curated_db}.dim_devices
    WHERE is_current = true
  ),

  historical AS (
    SELECT *
    FROM {curated_db}.dim_devices
    WHERE is_current = false
  ),

  -- Devices whose attributes are unchanged: keep the existing current row.
  unchanged AS (
    SELECT e.*
    FROM existing_current e
    JOIN current_per_device c
      ON e.device_id = c.device_id
     AND e.firmware_version IS NOT DISTINCT FROM c.firmware_version
     AND e.facility_id IS NOT DISTINCT FROM c.facility_id
  ),

  -- Devices whose attributes changed: close the existing current row.
  changed_closed AS (
    SELECT
      e.device_sk,
      e.device_id,
      e.firmware_version,
      e.facility_id,
      e.valid_from,
      CAST(current_timestamp AS TIMESTAMP) AS valid_to,
      false AS is_current
    FROM existing_current e
    JOIN current_per_device c
      ON e.device_id = c.device_id
    WHERE NOT (
      e.firmware_version IS NOT DISTINCT FROM c.firmware_version
      AND e.facility_id IS NOT DISTINCT FROM c.facility_id
    )
  ),

  -- Brand-new devices, plus devices whose attributes changed: open a row.
  needs_new_row AS (
    SELECT c.device_id, c.firmware_version, c.facility_id
    FROM current_per_device c
    LEFT JOIN existing_current e
      ON c.device_id = e.device_id
    WHERE e.device_id IS NULL
       OR NOT (
            e.firmware_version IS NOT DISTINCT FROM c.firmware_version
        AND e.facility_id IS NOT DISTINCT FROM c.facility_id
       )
  ),

  -- Surrogate keys start above the current max, so re-running this query
  -- never reuses keys for distinct device versions.
  new_versions AS (
    SELECT
      (SELECT COALESCE(MAX(device_sk), 0) FROM {curated_db}.dim_devices)
        + ROW_NUMBER() OVER (ORDER BY device_id) AS device_sk,
      device_id,
      firmware_version,
      facility_id,
      CAST(current_timestamp AS TIMESTAMP) AS valid_from,
      CAST(NULL AS TIMESTAMP) AS valid_to,
      true AS is_current
    FROM needs_new_row
  )

SELECT * FROM historical
UNION ALL SELECT * FROM unchanged
UNION ALL SELECT * FROM changed_closed
UNION ALL SELECT * FROM new_versions
