WITH first_seen AS (
  SELECT
    firmware_version,
    MIN(event_ts) AS first_observed
  FROM {staging_db}.events_clean
  GROUP BY firmware_version
)
SELECT
  ROW_NUMBER() OVER (ORDER BY firmware_version) AS firmware_sk,
  firmware_version AS version,
  first_observed
FROM first_seen
