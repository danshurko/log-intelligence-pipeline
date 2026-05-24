SELECT
  error_code,
  COUNT(*) AS error_count
FROM {curated_db}.fct_errors
WHERE event_ts >= current_timestamp - INTERVAL '7' DAY
GROUP BY error_code
ORDER BY error_count DESC
LIMIT 10
