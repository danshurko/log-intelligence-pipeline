import json
import logging
import sys
from typing import Any


class JsonLineFormatter(logging.Formatter):
    """JSON-line structured logger."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extras = getattr(record, "extras", None)
        if isinstance(extras, dict):
            payload.update(extras)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    if getattr(logger, "_json_configured", False):
        return logger
    logger.setLevel(level)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLineFormatter())
    # AWS Lambda attaches its own root handler; replace it on this logger so
    # our JSON formatter wins and records don't get printed twice.
    logger.handlers = [handler]
    logger.propagate = False
    logger._json_configured = True  # type: ignore[attr-defined]
    return logger


def log(logger: logging.Logger, level: int, message: str, **fields: Any) -> None:
    logger.log(level, message, extra={"extras": fields})
