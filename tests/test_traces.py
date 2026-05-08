import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)


@pytest.fixture
def span_exporter(monkeypatch, tmp_path):
    monkeypatch.setenv("MYMCP_TOKEN_FILE", str(tmp_path / "tokens.json"))
    monkeypatch.setenv("MYMCP_AUDIT_LOG_DIR", str(tmp_path / "audit"))
    from mymcp.config import reset_settings_cache

    reset_settings_cache()

    from mymcp.observability import reset_for_tests, setup_observability

    reset_for_tests()
    setup_observability(app=None, service_name="mymcp-test", service_version="0.0.0")
    exporter = InMemorySpanExporter()
    trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(exporter))
    yield exporter
    exporter.clear()


@pytest.mark.anyio
async def test_dispatch_tool_creates_span_with_attributes(span_exporter):
    from mymcp.mcp_server import _current_audit_info, call_tool

    token = _current_audit_info.set(
        {"token_name": "t", "role": "rw", "ip": "127.0.0.1"}
    )
    try:
        await call_tool("read_file", {"path": "/etc/hostname"})
    finally:
        _current_audit_info.reset(token)

    spans = span_exporter.get_finished_spans()
    assert any(s.name == "mymcp.tool.dispatch" for s in spans)
    s = next(s for s in spans if s.name == "mymcp.tool.dispatch")
    assert s.attributes["tool.name"] == "read_file"
    assert s.attributes["token.role"] == "rw"
    assert s.attributes["tool.result"] in ("success", "error")
