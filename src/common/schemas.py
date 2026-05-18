from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict


class EventType(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    METRIC = "metric"


class Severity(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    device_id: str
    timestamp: datetime
    event_type: EventType
    severity: Severity
    device_type: str
    facility_id: str
    firmware_version: str
    error_code: str | None = None
    message: str | None = None
    metrics: dict[str, float] | None = None
