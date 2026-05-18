import random
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta

from src.generator.devices import BASE_ERROR_RATE, EVENT_RATES_PER_SECOND, Device

ERROR_CODES: tuple[str, ...] = (
    "ERR_001",
    "ERR_002",
    "ERR_003",
    "ERR_004",
    "ERR_005",
    "ERR_006",
    "ERR_007",
    "ERR_008",
    "ERR_009",
    "ERR_010",
)

INFO_MESSAGES: tuple[str, ...] = (
    "heartbeat ok",
    "telemetry reported",
    "buffer flushed",
    "remote ping ok",
)

ERROR_MESSAGES: tuple[str, ...] = (
    "connection timeout to upstream",
    "sensor read failed",
    "buffer overflow",
    "auth token expired",
    "checksum mismatch",
)

ErrorRateFn = Callable[[Device], float]


def _make_event(
    device: Device,
    ts: datetime,
    is_error: bool,
    rng: random.Random,
) -> dict:
    event_id = f"ev-{uuid.UUID(int=rng.getrandbits(128)).hex[:12]}"
    base: dict = {
        "event_id": event_id,
        "device_id": device.device_id,
        "timestamp": ts.isoformat().replace("+00:00", "Z"),
        "device_type": device.device_type,
        "facility_id": device.facility_id,
        "firmware_version": device.firmware_version,
    }
    if is_error:
        base["event_type"] = "error"
        base["severity"] = rng.choice(("error", "critical"))
        base["error_code"] = rng.choice(ERROR_CODES)
        base["message"] = rng.choice(ERROR_MESSAGES)
        base["metrics"] = None
    else:
        base["event_type"] = rng.choice(("info", "metric"))
        base["severity"] = rng.choice(("info", "debug"))
        base["error_code"] = None
        base["message"] = rng.choice(INFO_MESSAGES)
        base["metrics"] = {
            "cpu_pct": round(rng.uniform(10.0, 60.0), 2),
            "mem_pct": round(rng.uniform(20.0, 80.0), 2),
        }
    return base


def _emit_for_device(
    device: Device,
    start: datetime,
    end: datetime,
    error_rate: float,
    rng: random.Random,
    out: list[dict],
) -> None:
    rate = EVENT_RATES_PER_SECOND[device.device_type]
    duration_s = (end - start).total_seconds()
    n_events = max(0, int(rate * duration_s))
    for _ in range(n_events):
        ts = start + timedelta(seconds=rng.uniform(0.0, duration_s))
        out.append(_make_event(device, ts, rng.random() < error_rate, rng))


def _baseline(
    fleet: list[Device],
    start: datetime,
    end: datetime,
    rng: random.Random,
    error_rate_fn: ErrorRateFn | None = None,
) -> list[dict]:
    if error_rate_fn is None:
        def _default_rate(_d: Device) -> float:
            return BASE_ERROR_RATE

        error_rate_fn = _default_rate

    out: list[dict] = []
    for device in fleet:
        _emit_for_device(device, start, end, error_rate_fn(device), rng, out)
    return out


def normal(
    fleet: list[Device], start: datetime, end: datetime, rng: random.Random
) -> list[dict]:
    return _baseline(fleet, start, end, rng)


def error_burst(
    fleet: list[Device], start: datetime, end: datetime, rng: random.Random
) -> list[dict]:
    out = _baseline(fleet, start, end, rng)
    duration_s = (end - start).total_seconds()
    if duration_s <= 0 or not fleet:
        return out

    target = rng.choice(fleet)
    burst_window_s = min(300.0, duration_s)
    burst_start_offset = rng.uniform(0.0, max(0.0, duration_s - burst_window_s))
    burst_start = start + timedelta(seconds=burst_start_offset)

    # Burst: 50× the target's normal emit rate, all errors.
    burst_rate = EVENT_RATES_PER_SECOND[target.device_type] * 50.0
    n_burst = max(1, int(burst_rate * burst_window_s))
    for _ in range(n_burst):
        ts = burst_start + timedelta(seconds=rng.uniform(0.0, burst_window_s))
        out.append(_make_event(target, ts, is_error=True, rng=rng))
    return out


def silent_device(
    fleet: list[Device], start: datetime, end: datetime, rng: random.Random
) -> list[dict]:
    if not fleet:
        return []
    duration_s = (end - start).total_seconds()
    target = rng.choice(fleet)
    # Silence kicks in somewhere in the middle 60% of the window so the
    # device is observably present at start and observably absent at end.
    silent_after = start + timedelta(seconds=rng.uniform(duration_s * 0.2, duration_s * 0.8))

    out: list[dict] = []
    for device in fleet:
        rate = EVENT_RATES_PER_SECOND[device.device_type]
        n_events = max(0, int(rate * duration_s))
        for _ in range(n_events):
            ts = start + timedelta(seconds=rng.uniform(0.0, duration_s))
            if device.device_id == target.device_id and ts >= silent_after:
                continue
            out.append(_make_event(device, ts, rng.random() < BASE_ERROR_RATE, rng))
    return out


# Multiplier applied to BASE_ERROR_RATE for devices on a flagged firmware line.
FIRMWARE_ISSUE_MULTIPLIER: float = 5.0
FIRMWARE_ISSUE_PREFIX: str = "1.3"


def firmware_issue(
    fleet: list[Device], start: datetime, end: datetime, rng: random.Random
) -> list[dict]:
    def rate_fn(device: Device) -> float:
        if device.firmware_version.startswith(FIRMWARE_ISSUE_PREFIX):
            return BASE_ERROR_RATE * FIRMWARE_ISSUE_MULTIPLIER
        return BASE_ERROR_RATE

    return _baseline(fleet, start, end, rng, rate_fn)


SCENARIOS: dict[str, Callable[[list[Device], datetime, datetime, random.Random], list[dict]]] = {
    "normal": normal,
    "error-burst": error_burst,
    "silent-device": silent_device,
    "firmware-issue": firmware_issue,
}
