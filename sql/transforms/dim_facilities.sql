WITH distinct_ids AS (
  SELECT DISTINCT facility_id
  FROM {staging_db}.events_clean
)
SELECT
  ROW_NUMBER() OVER (ORDER BY facility_id) AS facility_sk,
  facility_id,
  substring(facility_id, 5, 2) AS region
FROM distinct_ids
