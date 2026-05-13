"""Audit log enrichment with request_id / trace_id / span_id."""

import json
import os

import pytest

from mymcp import audit, config
from mymcp.observability import reset_for_tests, setup_observability
from mymcp.observability.request_id import current_request_id


@pytest.fixture
def _audit_dir(tmp_path, monkeypatch):
    log_dir = tmp_path / "audit"
    monkeypatch.setenv("MYMCP_AUDIT_ENABLED", "true")
    monkeypatch.setenv("MYMCP_AUDIT_LOG_DIR", str(log_dir))
    config.reset_settings_cache()
    audit._logger = None
    audit._setup_done = False
    yield log_dir
    audit._logger = None
    audit._setup_done = False
    config.reset_settings_cache()


def test_audit_record_includes_request_id(_audit_dir):
    reset_for_tests()
    setup_observability(app=None, service_name="mymcp-test", service_version="0.0.0")

    token = current_request_id.set("test-req-id-abc")
    try:
        audit.log_tool_call(
            token_name="t",
            role="ro",
            ip="1.2.3.4",
            tool="read_file",
            params={"p": "/tmp/x"},
            result="ok",
        )
    finally:
        current_request_id.reset(token)

    log_path = os.path.join(str(_audit_dir), "audit.log")
    # Flush handlers
    for h in audit._logger.handlers:  # type: ignore[union-attr]
        h.flush()

    with open(log_path) as f:
        line = f.readline().strip()
    entry = json.loads(line)
    assert entry["request_id"] == "test-req-id-abc"
    assert entry["tool"] == "read_file"


def test_audit_record_includes_trace_and_span_ids(_audit_dir):
    from opentelemetry import trace

    reset_for_tests()
    setup_observability(app=None, service_name="mymcp-test", service_version="0.0.0")

    tracer = trace.get_tracer("test")
    with tracer.start_as_current_span("test-span"):
        audit.log_tool_call(
            token_name="t",
            role="rw",
            ip="1.2.3.4",
            tool="bash_execute",
            params={},
            result="ok",
        )

    log_path = os.path.join(str(_audit_dir), "audit.log")
    for h in audit._logger.handlers:  # type: ignore[union-attr]
        h.flush()
    with open(log_path) as f:
        line = f.readline().strip()
    entry = json.loads(line)
    assert "trace_id" in entry and len(entry["trace_id"]) == 32
    assert "span_id" in entry and len(entry["span_id"]) == 16
