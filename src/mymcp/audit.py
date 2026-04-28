import json
import logging
import logging.handlers
import os
from datetime import datetime, timezone

from mymcp import config

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
    return logger


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
) -> None:
    global _logger
    if not _setup_done:
        _logger = _setup()
    if _logger is None:
        return

    entry: dict = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "token_name": token_name,
        "role": role,
        "ip": ip,
        "tool": tool,
        "params": params,
        "result": result,
    }
    if reason is not None:
        entry["reason"] = reason
    if error_code is not None:
        entry["error_code"] = error_code
    if error_message is not None:
        entry["error_message"] = error_message
    if duration_ms is not None:
        entry["duration_ms"] = duration_ms

    _logger.info(json.dumps(entry))
