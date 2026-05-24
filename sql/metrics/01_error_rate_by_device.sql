WITH window_events AS (
  SELECT device_sk, event_type
  FROM {curated_db}.fct_events
  WHERE event_ts >= current_timestamp - INTERVAL '1' DAY
)
SELECT
  d.device_id,
  COUNT(*) AS total_events,
  SUM(CASE WHEN w.event_type = 'error' THEN 1 ELSE 0 END) AS error_count,
  1.0 * SUM(CASE WHEN w.event_type = 'error' THEN 1 ELSE 0 END) / COUNT(*) AS error_rate
FROM window_events w
JOIN {curated_db}.dim_devices d
  ON d.device_sk = w.device_sk
GROUP BY d.device_id
ORDER BY error_rate DESC
