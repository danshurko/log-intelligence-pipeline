-- Error sub-fact: subset of `fct_events` where the event is an error,
-- with `error_code` and `message` joined back from staging. Kept separate
-- from `fct_events` so error-only dashboards never scan the full fact.
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
