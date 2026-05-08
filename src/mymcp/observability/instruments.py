"""Module-level OTel instrument singletons used across the codebase.

Naming uses OTel dot-style (``mymcp.tool.calls``); when surfaced through
``PrometheusMetricReader`` it is translated to underscore form
(``mymcp_tool_calls_total``), keeping wire-compatibility with existing
Prometheus dashboards.
"""

from __future__ import annotations

from opentelemetry import metrics

_meter = metrics.get_meter("mymcp")

tool_calls = _meter.create_counter(
    "mymcp.tool.calls",
    description="Total MCP tool calls",
    unit="1",
)

tool_duration = _meter.create_histogram(
    "mymcp.tool.duration",
    description="MCP tool call duration",
    unit="s",
)

http_requests = _meter.create_counter(
    "mymcp.http.requests",
    description="Total HTTP requests",
    unit="1",
)

audit_write_failures = _meter.create_counter(
    "mymcp.audit.write_failures",
    description="Audit log write failures",
    unit="1",
)
