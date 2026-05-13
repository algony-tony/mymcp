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


def test_otlp_setup_skipped_when_endpoint_unset(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    from mymcp.observability import reset_for_tests, setup_observability

    reset_for_tests()
    setup_observability(app=None, service_name="mymcp-t", service_version="0.0.0")
    from opentelemetry import trace

    tr = trace.get_tracer("t")
    with tr.start_as_current_span("noop"):
        pass


def test_otlp_setup_warns_when_extra_missing(monkeypatch, caplog):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    import sys

    sys.modules["opentelemetry.exporter.otlp.proto.http.trace_exporter"] = None  # type: ignore[assignment]
    from mymcp.observability import reset_for_tests, setup_observability

    reset_for_tests()
    with caplog.at_level("WARNING", logger="mymcp.observability"):
        setup_observability(app=None, service_name="mymcp-t", service_version="0.0.0")
    assert any("[otlp] extra is not installed" in r.message for r in caplog.records)
    sys.modules.pop("opentelemetry.exporter.otlp.proto.http.trace_exporter", None)


def test_otlp_setup_succeeds_when_extra_present(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    from mymcp.observability import reset_for_tests, setup_observability

    reset_for_tests()
    setup_observability(app=None, service_name="mymcp-t", service_version="0.0.0")
