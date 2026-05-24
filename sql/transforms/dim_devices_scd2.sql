-- SCD Type 2 merge for device dimension. Produces next-state rows.
-- Keeps history, closes changed currents, and opens new versions.
-- Uses IS NOT DISTINCT FROM so NULLs compare as values.
WITH
  -- Latest state per device in this batch
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

  -- Current rows in the dimension
  existing_current AS (
    SELECT *
    FROM {curated_db}.dim_devices
    WHERE is_current = true
  ),

  -- Historical (non-current) rows
  historical AS (
    SELECT * FROM {curated_db}.dim_devices WHERE is_current = false
  ),

  -- Devices with unchanged attributes: keep current row
  unchanged AS (
    SELECT e.*
    FROM existing_current e
    JOIN current_per_device c
      ON e.device_id = c.device_id
     AND e.firmware_version IS NOT DISTINCT FROM c.firmware_version
     AND e.facility_id IS NOT DISTINCT FROM c.facility_id
  ),

  -- Devices with changed attributes: close current row
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

  -- New devices or changed devices: open new row
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

  -- Generate new surrogate keys above current max
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
