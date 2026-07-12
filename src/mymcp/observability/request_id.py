"""Request-id contextvar shared by the logging filter.

v3: the ASGI ``RequestIdMiddleware`` that set this per HTTP request lived in the
Python server, which is gone (the Go binary is the server). The contextvar is
kept because ``observability.logs._ContextFilter`` reads it — in the recorder
sidecar there are no inbound HTTP requests, so it stays at its ``None`` default
and log records simply carry ``request_id=null`` (trace_id/span_id still come
from the active OTel span).
"""

from __future__ import annotations

from contextvars import ContextVar

current_request_id: ContextVar[str | None] = ContextVar("current_request_id", default=None)
