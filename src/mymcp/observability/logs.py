"""JSON logging configuration with request_id / trace_id / span_id injection."""

from __future__ import annotations

import logging
import sys
from typing import IO

from pythonjsonlogger import jsonlogger

from mymcp.observability.request_id import current_request_id


class _ContextFilter(logging.Filter):
    """Inject contextvar-derived fields into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = current_request_id.get()
        if not hasattr(record, "trace_id"):
            record.trace_id = None
        if not hasattr(record, "span_id"):
            record.span_id = None
        return True


_JSON_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def configure_logging(level: str = "INFO", stream: IO[str] | None = None) -> None:
    """Configure the root logger to emit JSON to ``stream`` (default: stderr).

    Idempotent: re-running replaces existing handlers.
    """
    target = stream if stream is not None else sys.stderr
    handler = logging.StreamHandler(target)
    formatter = jsonlogger.JsonFormatter(
        _JSON_FORMAT,
        rename_fields={"asctime": "timestamp"},
    )
    handler.setFormatter(formatter)
    handler.addFilter(_ContextFilter())

    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(level.upper())

    logging.getLogger("mymcp.audit").propagate = False
