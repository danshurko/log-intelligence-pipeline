-- Clean events: remove duplicates by event_id, drop rows missing required
-- fields, and exclude timestamps more than 1 hour in the future. Pure SELECT.
WITH ranked AS (
  SELECT
    event_id,
    device_id,
    timestamp AS event_ts,
    event_type,
    severity,
    device_type,
    facility_id,
    firmware_version,
    error_code,
    message,
    metrics_json,
    ROW_NUMBER() OVER (PARTITION BY event_id ORDER BY timestamp) AS rn
  FROM {raw_db}.events
  WHERE event_id IS NOT NULL
    AND device_id IS NOT NULL
    AND timestamp IS NOT NULL
    AND event_type IS NOT NULL
    AND severity IS NOT NULL
    AND device_type IS NOT NULL
    AND facility_id IS NOT NULL
    AND firmware_version IS NOT NULL
    AND timestamp <= current_timestamp + INTERVAL 1 HOUR
)
SELECT
  event_id,
  device_id,
  event_ts,
  event_type,
  severity,
  device_type,
  facility_id,
  firmware_version,
  error_code,
  message,
  metrics_json
FROM ranked
WHERE rn = 1
