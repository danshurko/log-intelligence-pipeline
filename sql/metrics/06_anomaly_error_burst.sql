-- Find error spikes: error count > mean + 3 * standard deviation from last 7 days
WITH hourly_errors AS (
  SELECT
    device_sk,
    date_trunc('hour', event_ts) AS hour_bucket,
    COUNT(*) AS error_count
  FROM {curated_db}.fct_errors
  WHERE event_ts >= current_timestamp - INTERVAL '7' DAY
  GROUP BY device_sk, date_trunc('hour', event_ts)
),
baseline AS (
  SELECT
    device_sk,
    AVG(error_count) AS mean_errors,
    STDDEV(error_count) AS std_errors
  FROM hourly_errors
  GROUP BY device_sk
)
SELECT
  d.device_id,
  h.hour_bucket,
  h.error_count,
  b.mean_errors,
  b.std_errors
FROM hourly_errors h
JOIN baseline b
  ON b.device_sk = h.device_sk
JOIN {curated_db}.dim_devices d
  ON d.device_sk = h.device_sk
WHERE b.std_errors IS NOT NULL
  AND b.std_errors > 0
  AND h.error_count > b.mean_errors + 3 * b.std_errors
ORDER BY h.hour_bucket DESC, h.error_count DESC
