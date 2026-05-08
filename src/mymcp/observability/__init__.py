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

    prom_reader = PrometheusMetricReader()
    meter_provider = MeterProvider(resource=resource, metric_readers=[prom_reader])
    otel_metrics.set_meter_provider(meter_provider)

    tracer_provider = TracerProvider(resource=resource)
    otel_trace.set_tracer_provider(tracer_provider)

    _maybe_setup_otlp(app, tracer_provider, meter_provider, prom_reader, resource)


def _maybe_setup_otlp(app, tracer_provider, meter_provider, prom_reader, resource) -> None:
    """If [otlp] extra is installed and an endpoint is configured, wire OTLP."""
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return

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
        return

    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))

    otlp_reader = PeriodicExportingMetricReader(OTLPMetricExporter())
    new_meter_provider = type(meter_provider)(
        resource=resource,
        metric_readers=[prom_reader, otlp_reader],
    )
    otel_metrics.set_meter_provider(new_meter_provider)

    if app is not None:
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
            from opentelemetry.instrumentation.logging import LoggingInstrumentor
        except ImportError:
            _log.warning("instrumentation packages missing despite [otlp] partial install")
            return

        FastAPIInstrumentor.instrument_app(app)
        LoggingInstrumentor().instrument(set_logging_format=False)


def reset_for_tests() -> None:
    """Allow tests to re-initialize. Not for production use."""
    global _initialized
    _initialized = False
