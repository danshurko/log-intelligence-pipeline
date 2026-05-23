-- Error rate per firmware version over the last 7 days. Each fct_events
-- row carries the device_sk that was current at event time, so the join
-- to dim_devices on device_sk (without filtering is_current) attributes
-- the event to whichever firmware the device was running back then.
WITH window_events AS (
  SELECT device_sk, event_type
  FROM {curated_db}.fct_events
  WHERE event_ts >= current_timestamp - INTERVAL '7' DAY
)
SELECT
  d.firmware_version,
  COUNT(*) AS total_events,
  SUM(CASE WHEN w.event_type = 'error' THEN 1 ELSE 0 END) AS error_count,
  1.0 * SUM(CASE WHEN w.event_type = 'error' THEN 1 ELSE 0 END) / COUNT(*) AS error_rate
FROM window_events w
JOIN {curated_db}.dim_devices d
  ON d.device_sk = w.device_sk
GROUP BY d.firmware_version
ORDER BY error_rate DESC
