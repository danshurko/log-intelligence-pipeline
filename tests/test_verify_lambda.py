import pytest

from src.orchestration.verify_lambda import VerifyFailedError, verify


class FakeAthena:
    """In-memory Athena double that hands out one count per query.

    Pop order matches the order of queries the lambda issues: staging first,
    curated second.
    """

    def __init__(self, counts: list[int]) -> None:
        self._counts = list(counts)
        self._queries: dict[str, int] = {}

    def start_query_execution(self, *, QueryString: str, **_kwargs):  # noqa: N803
        qid = f"q-{len(self._queries)}"
        self._queries[qid] = self._counts.pop(0)
        return {"QueryExecutionId": qid}

    def get_query_execution(self, *, QueryExecutionId: str):  # noqa: N803
        return {"QueryExecution": {"Status": {"State": "SUCCEEDED"}}}

    def get_query_results(self, *, QueryExecutionId: str):  # noqa: N803
        count = self._queries[QueryExecutionId]
        return {
            "ResultSet": {
                "Rows": [
                    {"Data": [{"VarCharValue": "n"}]},
                    {"Data": [{"VarCharValue": str(count)}]},
                ]
            }
        }


STAGING_DB = "device_log_staging"
CURATED_DB = "device_log_curated"


def test_verify_passes_when_delta_within_threshold():
    athena = FakeAthena(counts=[1000, 970])  # 3% delta
    result = verify(athena, "s3://x/", "primary", 0.05, STAGING_DB, CURATED_DB)
    assert result["ok"] is True
    assert result["delta"] == pytest.approx(0.03)


def test_verify_raises_when_delta_exceeds_threshold():
    athena = FakeAthena(counts=[1000, 800])  # 20% delta
    with pytest.raises(VerifyFailedError):
        verify(athena, "s3://x/", "primary", 0.05, STAGING_DB, CURATED_DB)


def test_verify_treats_empty_staging_as_pass():
    athena = FakeAthena(counts=[0, 0])
    result = verify(athena, "s3://x/", "primary", 0.05, STAGING_DB, CURATED_DB)
    assert result["ok"] is True
    assert result["staging"] == 0


def test_verify_raises_when_athena_query_fails():
    class FailingAthena(FakeAthena):
        def get_query_execution(self, *, QueryExecutionId: str):  # noqa: N803
            return {
                "QueryExecution": {
                    "Status": {"State": "FAILED", "StateChangeReason": "syntax err"}
                }
            }

    with pytest.raises(RuntimeError, match="athena query FAILED"):
        verify(
            FailingAthena(counts=[1000, 1000]),
            "s3://x/",
            "primary",
            0.05,
            STAGING_DB,
            CURATED_DB,
        )
