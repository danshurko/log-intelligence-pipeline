-- Distinct facilities observed in the current staging snapshot. Region is
-- extracted from the facility_id prefix (`fac-XX-NN` -> `XX`).
CREATE OR REPLACE TABLE curated.dim_facilities AS
WITH distinct_ids AS (
  SELECT DISTINCT facility_id
  FROM staging.events_clean
)
SELECT
  ROW_NUMBER() OVER (ORDER BY facility_id) AS facility_sk,
  facility_id,
  substring(facility_id, 5, 2) AS region
FROM distinct_ids;
