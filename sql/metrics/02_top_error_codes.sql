-- Top 10 error codes by count over the last 7 days. Reads fct_errors
-- directly since it already carries the resolved error_code.
SELECT
  error_code,
  COUNT(*) AS error_count
FROM {curated_db}.fct_errors
WHERE event_ts >= current_timestamp - INTERVAL '7' DAY
GROUP BY error_code
ORDER BY error_count DESC
LIMIT 10
