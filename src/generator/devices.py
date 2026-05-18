import random
from dataclasses import dataclass

DEVICE_TYPES: tuple[str, ...] = ("sensor", "gateway", "controller")

FACILITIES: tuple[str, ...] = (
    "fac-eu-01",
    "fac-eu-02",
    "fac-us-01",
    "fac-us-02",
    "fac-ap-01",
)

FIRMWARE_VERSIONS: tuple[str, ...] = ("1.2.0", "1.2.1", "1.3.0", "1.3.1")

# Mean events emitted per device per second, by device type. Sensors are
# chatty; gateways aggregate and emit less often; controllers sit in between.
EVENT_RATES_PER_SECOND: dict[str, float] = {
    "sensor": 1.0,
    "gateway": 0.2,
    "controller": 0.5,
}

# Fraction of baseline events that are flagged as errors under the `normal`
# scenario. Scenarios may amplify this for specific devices or cohorts.
BASE_ERROR_RATE: float = 0.005


@dataclass(frozen=True)
class Device:
    device_id: str
    device_type: str
    facility_id: str
    firmware_version: str


def generate_fleet(n_devices: int, seed: int = 42) -> list[Device]:
    rng = random.Random(seed)
    fleet: list[Device] = []
    for i in range(n_devices):
        fleet.append(
            Device(
                device_id=f"dev-{i:04d}",
                device_type=rng.choice(DEVICE_TYPES),
                facility_id=rng.choice(FACILITIES),
                firmware_version=rng.choice(FIRMWARE_VERSIONS),
            )
        )
    return fleet
