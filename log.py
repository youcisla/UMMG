"""Structured logging + per-request latency capture."""
from __future__ import annotations

import json
import logging
import sys
import time
from contextlib import contextmanager
from typing import Any

_LOGGER: logging.Logger | None = None


def setup_logging(level: str = "INFO") -> logging.Logger:
    global _LOGGER
    logger = logging.getLogger("ummg")
    logger.setLevel(level)
    logger.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)

    class JsonFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
            payload: dict[str, Any] = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
                + f".{int(record.msecs):03d}Z",
                "level": record.levelname,
                "msg": record.getMessage(),
            }
            for key, val in record.__dict__.items():
                if key in (
                    "name", "msg", "args", "levelname", "levelno", "pathname",
                    "filename", "module", "exc_info", "exc_text", "stack_info",
                    "lineno", "funcName", "created", "msecs", "relativeCreated",
                    "thread", "threadName", "processName", "process", "message",
                    "taskName",
                ):
                    continue
                try:
                    json.dumps(val)
                    payload[key] = val
                except (TypeError, ValueError):
                    payload[key] = str(val)
            return json.dumps(payload, ensure_ascii=False)

    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.propagate = False
    _LOGGER = logger
    return logger


def get_logger() -> logging.Logger:
    global _LOGGER
    if _LOGGER is None:
        _LOGGER = setup_logging()
    return _LOGGER


@contextmanager
def timer(name: str, **fields: Any):
    """Context manager that logs elapsed_ms on exit."""
    log = get_logger()
    start = time.perf_counter()
    payload: dict[str, Any] = {"phase": name, **fields}
    try:
        yield payload
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        payload["elapsed_ms"] = round(elapsed_ms, 2)
        log.debug("phase_done", extra=payload)