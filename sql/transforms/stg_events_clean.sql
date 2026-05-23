-- Layer 2 cleanup: dedupe by event_id, drop rows missing required columns,
-- drop wildly-future timestamps (>1h ahead of current_timestamp). Result is
-- a pure SELECT; the orchestrator owns where the rows land.
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
