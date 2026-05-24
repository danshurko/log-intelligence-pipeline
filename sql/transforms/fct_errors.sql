-- Extract error events with code and message
SELECT
  f.event_id,
  f.device_sk,
  f.event_ts,
  s.error_code,
  s.message
FROM {curated_db}.fct_events f
JOIN {staging_db}.events_clean s
  ON f.event_id = s.event_id
WHERE f.event_type = 'error'
