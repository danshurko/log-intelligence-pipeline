-- Mean time between errors per device over the last 7 days. LAG produces
-- the previous error timestamp for the same device; `date_diff('second',...)`
-- yields the gap in seconds. Devices with a single error in the window have
-- no gaps and are excluded.
WITH error_gaps AS (
  SELECT
    device_sk,
    event_ts,
    LAG(event_ts) OVER (PARTITION BY device_sk ORDER BY event_ts) AS prev_error_ts
  FROM {curated_db}.fct_errors
  WHERE event_ts >= current_timestamp - INTERVAL '7' DAY
)
SELECT
  d.device_id,
  COUNT(*) AS error_gaps,
  AVG(date_diff('second', g.prev_error_ts, g.event_ts)) AS mtbe_seconds
FROM error_gaps g
JOIN {curated_db}.dim_devices d
  ON d.device_sk = g.device_sk
WHERE g.prev_error_ts IS NOT NULL
GROUP BY d.device_id
ORDER BY mtbe_seconds ASC
