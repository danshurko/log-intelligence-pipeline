-- Fact table for all events. Joins the cleaned staging snapshot to the
-- current view of `dim_devices` to resolve the surrogate key.
SELECT
  e.event_id,
  d.device_sk,
  e.event_ts,
  e.event_type,
  e.severity,
  e.metrics_json
FROM {staging_db}.events_clean e
JOIN {curated_db}.dim_devices d
  ON d.device_id = e.device_id
 AND d.is_current = true
