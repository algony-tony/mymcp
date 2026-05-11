"""mymcp observability package: OpenTelemetry-based metrics, traces, logs.

Public entry point: ``setup_observability(app, service_name, service_version)``.
Idempotent within a process; safe to call once at startup.

When the optional ``[otlp]`` extra is installed AND
``OTEL_EXPORTER_OTLP_ENDPOINT`` is set, OTLP exporters and FastAPI/logging
auto-instrumentation are wired automatically. Otherwise only the local
Prometheus ``/metrics`` reader and the in-process tracer/meter providers are
configured (spans are recorded but not exported).
"""

from __future__ import annotations

import logging
import os

from opentelemetry import metrics as otel_metrics
from opentelemetry import trace as otel_trace
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider

_log = logging.getLogger(__name__)
_initialized = False


def setup_observability(
    app=None,
    *,
    service_name: str = "mymcp",
    service_version: str = "unknown",
) -> None:
    """Initialize OTel providers, the Prometheus reader, and (optionally) OTLP."""
    global _initialized
    if _initialized:
        return
    _initialized = True

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.namespace": "mymcp",
            "service.version": service_version,
        }
    )

    readers = [PrometheusMetricReader()]
    span_processors = []
    otlp_app_instrumenters = None

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if endpoint:
        otlp_app_instrumenters = _build_otlp(readers, span_processors)

    meter_provider = MeterProvider(resource=resource, metric_readers=readers)
    otel_metrics.set_meter_provider(meter_provider)

    tracer_provider = TracerProvider(resource=resource)
    for sp in span_processors:
        tracer_provider.add_span_processor(sp)
    otel_trace.set_tracer_provider(tracer_provider)

    if otlp_app_instrumenters and app is not None:
        otlp_app_instrumenters(app)


def _build_otlp(readers, span_processors):
    """Append OTLP exporters to readers/span_processors. Returns optional app-instrumenter."""
    try:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
            OTLPMetricExporter,
        )
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        _log.warning(
            "OTEL_EXPORTER_OTLP_ENDPOINT is set but the [otlp] extra is not "
            "installed; OTLP export disabled. Run: pip install algony-mymcp[otlp]"
        )
        return None

    span_processors.append(BatchSpanProcessor(OTLPSpanExporter()))
    readers.append(PeriodicExportingMetricReader(OTLPMetricExporter()))

    def _instrument_app(app):
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
            from opentelemetry.instrumentation.logging import LoggingInstrumentor
        except ImportError:
            _log.warning("instrumentation packages missing despite [otlp] partial install")
            return
        FastAPIInstrumentor.instrument_app(app)
        LoggingInstrumentor().instrument(set_logging_format=False)

    return _instrument_app


def reset_for_tests() -> None:
    """Allow tests to re-initialize. Not for production use."""
    global _initialized
    _initialized = False
