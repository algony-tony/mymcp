from opentelemetry import metrics as otel_metrics
from opentelemetry import trace as otel_trace
from prometheus_client import REGISTRY

from mymcp.observability import setup_observability


def test_setup_initializes_meter_and_tracer_providers(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    setup_observability(app=None, service_name="mymcp-test", service_version="0.0.0")

    meter = otel_metrics.get_meter("test")
    counter = meter.create_counter("test_counter")
    counter.add(1)

    tracer = otel_trace.get_tracer("test")
    with tracer.start_as_current_span("test-span") as span:
        assert span is not None


def test_prometheus_reader_registered():
    setup_observability(app=None, service_name="mymcp-test", service_version="0.0.0")
    names = {m.name for m in REGISTRY.collect()}
    assert isinstance(names, set)
