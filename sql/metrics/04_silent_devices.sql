-- Devices that have produced zero events in the last 30 minutes. Uses an
-- anti-join from the current dim_devices snapshot against the set of
-- device_sks observed in fct_events within the window.
WITH recent_devices AS (
  SELECT DISTINCT device_sk
  FROM {curated_db}.fct_events
  WHERE event_ts >= current_timestamp - INTERVAL '30' MINUTE
)
SELECT
  d.device_id,
  d.facility_id,
  d.firmware_version
FROM {curated_db}.dim_devices d
LEFT JOIN recent_devices r
  ON r.device_sk = d.device_sk
WHERE d.is_current = true
  AND r.device_sk IS NULL
ORDER BY d.device_id
