import random
from dataclasses import dataclass
from datetime import datetime, timedelta

# Sentinel keys that mark dirty records produced by `inject_dirtiness`. They
# are not part of the event schema. Callers that write Parquet should filter
# `_malformed` records out and strip the markers; callers that write JSON to a
# stream should serialize the `_raw` payload of `_malformed` records verbatim.
MALFORMED_MARKER: str = "_malformed"
MALFORMED_RAW_PAYLOAD: str = "_raw"
MALFORMED_BYTES: str = "{this is: not_valid_json, "

# Plausible clock drift bounds. Out-of-order looks like a slow clock (backward
# drift up to 5 min); future looks like a fast clock (forward drift up to 1 h).
# Downstream SQL cleanup catches these via `timestamp < window_start` and
# `timestamp > now()` rather than magic sentinel comparisons.
OUT_OF_ORDER_MIN_SECONDS: int = 1
OUT_OF_ORDER_MAX_SECONDS: int = 5 * 60
FUTURE_MIN_SECONDS: int = 1
FUTURE_MAX_SECONDS: int = 60 * 60

OPTIONAL_FIELDS: tuple[str, ...] = ("error_code", "message", "metrics")


@dataclass(frozen=True)
class DirtinessConfig:
    missing_fields_rate: float = 0.02
    duplicate_rate: float = 0.01
    malformed_json_rate: float = 0.003
    out_of_order_rate: float = 0.005
    future_timestamp_rate: float = 0.001


def _shift_iso_timestamp(ts: str, low_s: float, high_s: float, rng: random.Random) -> str:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    shifted = dt + timedelta(seconds=rng.uniform(low_s, high_s))
    return shifted.isoformat().replace("+00:00", "Z")


def inject_dirtiness(
    records: list[dict],
    config: DirtinessConfig,
    seed: int = 0,
) -> list[dict]:
    """Return a new list of records with probabilistic corruptions applied.

    Output length may exceed input length when duplicates are injected.
    Future-shift and out-of-order-shift are mutually exclusive per record so
    opposing drifts never compound on the same row.
    """
    rng = random.Random(seed)
    out: list[dict] = []
    for rec in records:
        mutated = dict(rec)

        if rng.random() < config.missing_fields_rate:
            field = rng.choice(OPTIONAL_FIELDS)
            mutated.pop(field, None)

        ts_roll = rng.random()
        if ts_roll < config.future_timestamp_rate:
            mutated["timestamp"] = _shift_iso_timestamp(
                mutated["timestamp"],
                FUTURE_MIN_SECONDS,
                FUTURE_MAX_SECONDS,
                rng,
            )
        elif ts_roll < config.future_timestamp_rate + config.out_of_order_rate:
            mutated["timestamp"] = _shift_iso_timestamp(
                mutated["timestamp"],
                -OUT_OF_ORDER_MAX_SECONDS,
                -OUT_OF_ORDER_MIN_SECONDS,
                rng,
            )

        if rng.random() < config.malformed_json_rate:
            mutated[MALFORMED_MARKER] = True
            mutated[MALFORMED_RAW_PAYLOAD] = MALFORMED_BYTES

        out.append(mutated)

        if rng.random() < config.duplicate_rate:
            out.append(dict(mutated))

    return out
