-- One row per firmware version observed, with the earliest event timestamp
-- that surfaced it. `first_observed` lets analytics correlate firmware
-- rollouts with downstream error trends.
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
