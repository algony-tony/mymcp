import json
import logging
import logging.handlers
import os
from datetime import datetime, timezone

from opentelemetry import trace as _otel_trace

from mymcp import config
from mymcp.observability.request_id import current_request_id

_logger: logging.Logger | None = None
_setup_done = False


def _setup() -> logging.Logger | None:
    global _setup_done
    _setup_done = True

    if not config.AUDIT_ENABLED:
        return None

    os.makedirs(config.AUDIT_LOG_DIR, exist_ok=True)
    log_path = os.path.join(config.AUDIT_LOG_DIR, "audit.log")

    logger = logging.getLogger("mymcp.audit")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    # Avoid duplicate handlers on re-init (tests)
    logger.handlers.clear()

    handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=config.AUDIT_MAX_BYTES,
        backupCount=config.AUDIT_BACKUP_COUNT,
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)

    _maybe_attach_otel_handler(logger)
    return logger


def _maybe_attach_otel_handler(logger: logging.Logger) -> None:
    """Attach OTel LoggingHandler when a real LoggerProvider has been configured."""
    try:
        from opentelemetry._logs import get_logger_provider
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    except ImportError:
        return

    provider = get_logger_provider()
    if not isinstance(provider, LoggerProvider):
        return
    logger.addHandler(LoggingHandler(level=logging.INFO, logger_provider=provider))


def log_tool_call(
    *,
    token_name: str,
    role: str,
    ip: str,
    tool: str,
    params: dict,
    result: str,
    reason: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    duration_ms: int | None = None,
    output: dict | None = None,
) -> None:
    global _logger
    if not _setup_done:
        _logger = _setup()
    if _logger is None:
        return

    entry: dict = {
        "ts": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
        "token_name": token_name,
        "role": role,
        "ip": ip,
        "tool": tool,
        "params": params,
        "result": result,
    }
    rid = current_request_id.get()
    if rid is not None:
        entry["request_id"] = rid
    span = _otel_trace.get_current_span()
    ctx = span.get_span_context() if span is not None else None
    if ctx is not None and ctx.is_valid:
        entry["trace_id"] = format(ctx.trace_id, "032x")
        entry["span_id"] = format(ctx.span_id, "016x")
    if reason is not None:
        entry["reason"] = reason
    if error_code is not None:
        entry["error_code"] = error_code
    if error_message is not None:
        entry["error_message"] = error_message
    if duration_ms is not None:
        entry["duration_ms"] = duration_ms
    if output is not None:
        entry["output"] = output

    try:
        _logger.info(json.dumps(entry))
    except Exception:
        from mymcp.observability.instruments import audit_write_failures

        audit_write_failures.add(1)
        raise
