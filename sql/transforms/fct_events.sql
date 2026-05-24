-- Events fact table with device key lookup
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
