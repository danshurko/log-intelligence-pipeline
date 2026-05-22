-- Fact table for all events. Joins the cleaned staging snapshot to the
-- current view of `dim_devices` to resolve the surrogate key.
CREATE OR REPLACE TABLE curated.fct_events AS
SELECT
  e.event_id,
  d.device_sk,
  e.event_ts,
  e.event_type,
  e.severity,
  e.metrics_json
FROM staging.events_clean e
JOIN curated.dim_devices d
  ON d.device_id = e.device_id
 AND d.is_current = true;
