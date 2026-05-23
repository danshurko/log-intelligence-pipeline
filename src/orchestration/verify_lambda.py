"""Step Functions VerifyCounts step.

Runs two Athena counts (staging vs curated) after the Glue ETL job and
fails if they diverge by more than `DELTA_THRESHOLD` (default 5%). Step
Functions catches the exception and routes to the SNS notify state.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import boto3

from src.common.observability import get_logger, log

LOGGER = get_logger("orchestration.verify")

DEFAULT_DELTA_THRESHOLD: float = 0.05
POLL_INTERVAL_SECONDS: float = 2.0
MAX_POLL_ATTEMPTS: int = 60

STAGING_QUERY_TEMPLATE: str = "SELECT COUNT(*) AS n FROM {staging_db}.events_clean"
CURATED_QUERY_TEMPLATE: str = "SELECT COUNT(*) AS n FROM {curated_db}.fct_events"


class VerifyFailedError(RuntimeError):
    """Raised when the staging/curated counts diverge beyond threshold."""


def _run_count_query(athena: Any, sql: str, output_s3: str, workgroup: str) -> int:
    start = athena.start_query_execution(
        QueryString=sql,
        ResultConfiguration={"OutputLocation": output_s3},
        WorkGroup=workgroup,
    )
    query_id = start["QueryExecutionId"]
    for _ in range(MAX_POLL_ATTEMPTS):
        info = athena.get_query_execution(QueryExecutionId=query_id)
        state = info["QueryExecution"]["Status"]["State"]
        if state == "SUCCEEDED":
            break
        if state in {"FAILED", "CANCELLED"}:
            reason = info["QueryExecution"]["Status"].get("StateChangeReason", state)
            raise RuntimeError(f"athena query {state}: {reason}")
        time.sleep(POLL_INTERVAL_SECONDS)
    else:
        raise TimeoutError(f"athena query {query_id} did not finish in time")

    rows = athena.get_query_results(QueryExecutionId=query_id)["ResultSet"]["Rows"]
    # Athena returns the header in row 0 and the value in row 1.
    return int(rows[1]["Data"][0]["VarCharValue"])


def verify(
    athena: Any,
    output_s3: str,
    workgroup: str,
    delta_threshold: float,
    staging_db: str,
    curated_db: str,
) -> dict[str, Any]:
    staging_query = STAGING_QUERY_TEMPLATE.format(staging_db=staging_db)
    curated_query = CURATED_QUERY_TEMPLATE.format(curated_db=curated_db)
    staging = _run_count_query(athena, staging_query, output_s3, workgroup)
    curated = _run_count_query(athena, curated_query, output_s3, workgroup)

    # Treat an empty staging snapshot as "no data to verify"; downstream
    # alerting catches sustained zero counts via CloudWatch on the Lambda
    # consumer, not here.
    if staging == 0:
        log(LOGGER, logging.INFO, "verify_skipped_empty_staging", curated=curated)
        return {"staging": staging, "curated": curated, "delta": 0.0, "ok": True}

    delta = abs(staging - curated) / staging
    ok = delta <= delta_threshold
    payload = {
        "staging": staging,
        "curated": curated,
        "delta": delta,
        "threshold": delta_threshold,
        "ok": ok,
    }
    if not ok:
        log(LOGGER, logging.ERROR, "verify_failed", **payload)
        raise VerifyFailedError(
            f"staging={staging} curated={curated} delta={delta:.3f} > {delta_threshold}"
        )
    log(LOGGER, logging.INFO, "verify_passed", **payload)
    return payload


def handler(_event: dict, _context: Any) -> dict[str, Any]:
    output_s3 = os.environ["ATHENA_OUTPUT_S3"]
    workgroup = os.environ.get("ATHENA_WORKGROUP", "primary")
    delta_threshold = float(os.environ.get("DELTA_THRESHOLD", str(DEFAULT_DELTA_THRESHOLD)))
    staging_db = os.environ["STAGING_DATABASE"]
    curated_db = os.environ["CURATED_DATABASE"]
    athena = boto3.client("athena")
    return verify(
        athena,
        output_s3,
        workgroup,
        delta_threshold,
        staging_db,
        curated_db,
    )
