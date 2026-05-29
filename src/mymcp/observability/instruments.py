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


# --- Saturation gauges (callbacks registered by other modules) ---


recorder_events_consumed = _meter.create_counter(
    "mymcp.recorder.events.consumed",
    description="Audit events consumed by recorder",
    unit="1",
)
recorder_merge_cycles = _meter.create_counter(
    "mymcp.recorder.merge.cycles",
    description="Merge cycles run",
    unit="1",
)
recorder_bootstrap_runs = _meter.create_counter(
    "mymcp.recorder.bootstrap.runs",
    description="Bootstrap runs",
    unit="1",
)
recorder_llm_calls = _meter.create_counter(
    "mymcp.recorder.llm.calls",
    description="LLM API calls",
    unit="1",
)
recorder_llm_tokens = _meter.create_counter(
    "mymcp.recorder.llm.tokens",
    description="LLM tokens (input/output)",
    unit="1",
)
recorder_bash_probe_runs = _meter.create_counter(
    "mymcp.recorder.bash_probe.runs",
    description="Internal bash probe invocations",
    unit="1",
)
recorder_event_loss = _meter.create_counter(
    "mymcp.recorder.event.loss",
    description="Events lost due to rotation past cursor",
    unit="1",
)


# --- Saturation gauges (callbacks registered by other modules) ---


def register_callback_gauge(name: str, description: str, callback) -> None:
    """Create an observable gauge backed by ``callback`` (called on each export).

    ``callback`` receives no arguments and returns an iterable of
    ``opentelemetry.metrics.Observation`` instances.
    """
    _meter.create_observable_gauge(
        name,
        description=description,
        callbacks=[lambda options: callback()],
    )
