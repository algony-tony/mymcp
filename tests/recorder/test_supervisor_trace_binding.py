"""Supervisor error logs should carry the active trace_id/span_id.

Today the supervisor's `recorder.supervisor.cycle_error` log fires outside any
span context (the merge_cycle span has already exited), so Loki sees the
ERROR but can't jump to the matching Tempo trace. We wrap the supervisor's
per-cycle work in its own span so the log filter picks up trace context.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from mymcp.observability.logs import _ContextFilter
from mymcp.recorder.bootstrap import BootstrapState
from mymcp.recorder.task import RecorderSupervisor


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def span_exporter():
    from mymcp.observability import reset_for_tests, setup_observability

    reset_for_tests()
    setup_observability(app=None, service_name="mymcp-test", service_version="0.0.0")
    exporter = InMemorySpanExporter()
    trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(exporter))
    yield exporter
    exporter.clear()
    reset_for_tests()


class _CaptureHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []
        self.addFilter(_ContextFilter())

    def emit(self, record: logging.LogRecord) -> None:
        # Filter has already run by the time emit is called via Logger.handle
        self.records.append(record)


@pytest.mark.anyio
async def test_supervisor_cycle_emits_span_named_supervisor_cycle(span_exporter):
    """A new span 'recorder.supervisor.cycle' wraps each iteration."""
    merge = MagicMock()
    merge.run_once = AsyncMock(side_effect=RuntimeError("boom"))
    bootstrap = MagicMock()
    bootstrap.state = BootstrapState.SUCCEEDED
    sup = RecorderSupervisor(
        merge_cycle=merge,
        bootstrapper=bootstrap,
        merge_interval_sec=0.01,
        provider="anthropic",
        model="x",
        circuit_breaker_threshold=99,
    )
    # Pre-populate "overview exists" by stubbing the store.
    type(merge)._store = property(lambda _: _Store())

    task = asyncio.create_task(sup.run())
    await asyncio.sleep(0.05)
    sup.shutdown()
    await asyncio.wait_for(task, timeout=1.0)

    names = [s.name for s in span_exporter.get_finished_spans()]
    assert "recorder.supervisor.cycle" in names


@pytest.mark.anyio
async def test_supervisor_error_log_has_trace_id(span_exporter):
    """When merge fails, the 'cycle_error' log must carry trace_id/span_id."""
    merge = MagicMock()
    merge.run_once = AsyncMock(side_effect=RuntimeError("boom"))
    bootstrap = MagicMock()
    bootstrap.state = BootstrapState.SUCCEEDED
    type(merge)._store = property(lambda _: _Store())

    sup = RecorderSupervisor(
        merge_cycle=merge,
        bootstrapper=bootstrap,
        merge_interval_sec=0.01,
        provider="anthropic",
        model="x",
        circuit_breaker_threshold=99,
    )

    handler = _CaptureHandler()
    logger = logging.getLogger("mymcp.recorder")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        task = asyncio.create_task(sup.run())
        await asyncio.sleep(0.05)
        sup.shutdown()
        await asyncio.wait_for(task, timeout=1.0)
    finally:
        logger.removeHandler(handler)

    err_records = [
        r for r in handler.records if r.getMessage() == "recorder.supervisor.cycle_error"
    ]
    assert err_records, "expected at least one cycle_error record"
    rec = err_records[0]
    assert getattr(rec, "trace_id", None) is not None
    assert getattr(rec, "span_id", None) is not None


class _Store:
    """Tiny stub that pretends an overview already exists."""

    def read_overview(self) -> str:
        return "# Existing\n"
