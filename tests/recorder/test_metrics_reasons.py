"""Tests for reason-labelled recorder metrics.

merge_cycle scenarios are run end-to-end with an InMemoryMetricReader installed
so we can verify which (reason, result) label values were incremented.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from unittest.mock import AsyncMock

import pytest
from opentelemetry import metrics as otel_metrics
from opentelemetry.metrics import _internal as _otel_metrics_internal
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.util._once import Once

from mymcp.recorder.events import EventTailer
from mymcp.recorder.llm.base import LLMResponse, Usage
from mymcp.recorder.merge_cycle import MergeCycle
from mymcp.recorder.overview import OverviewStore


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def metric_reader() -> Iterator[InMemoryMetricReader]:
    """Install a fresh MeterProvider with an in-memory reader for the test.

    OTel's global meter provider is set-once. We reset its internal latch
    before AND after so neighbouring tests can install their own provider
    (e.g. tests/test_metrics.py's setup_observability call).
    """
    _otel_metrics_internal._METER_PROVIDER_SET_ONCE = Once()
    _otel_metrics_internal._METER_PROVIDER = None
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    otel_metrics.set_meter_provider(provider)
    try:
        yield reader
    finally:
        provider.shutdown()
        _otel_metrics_internal._METER_PROVIDER_SET_ONCE = Once()
        _otel_metrics_internal._METER_PROVIDER = None


def _counter_points(reader: InMemoryMetricReader, name: str) -> list[tuple[dict, int]]:
    """Return [(attributes_dict, value), ...] for the named counter."""
    data = reader.get_metrics_data()
    out: list[tuple[dict, int]] = []
    if data is None:
        return out
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                if m.name != name:
                    continue
                for pt in m.data.data_points:
                    out.append((dict(pt.attributes), pt.value))
    return out


def _attrs_with(points: list[tuple[dict, int]], **subset) -> int:
    """Sum counter values whose attributes are a superset of `subset`."""
    total = 0
    for attrs, value in points:
        if all(attrs.get(k) == v for k, v in subset.items()):
            total += value
    return total


def _audit_line(**fields) -> str:
    base = {
        "ts": "2026-06-05T10:00:00Z",
        "tool": "bash_execute",
        "result": "ok",
        "params": {"command": "true"},
        "output": {"stdout_head": "ok"},
    }
    base.update(fields)
    return json.dumps(base) + "\n"


def _make_cycle(tmp_path, response: LLMResponse, side_effect=None):
    (tmp_path / "audit.log").write_text(_audit_line())
    store = OverviewStore(tmp_path / "overview")
    store.write_overview("# Existing\n## Recent Changes\n")
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    fake = AsyncMock()
    if side_effect is not None:
        fake.call = AsyncMock(side_effect=side_effect)
    else:
        fake.call = AsyncMock(return_value=response)
    return MergeCycle(client=fake, tailer=tailer, store=store, max_events_per_cycle=10)


@pytest.mark.anyio
async def test_success_records_reason_success(metric_reader, tmp_path):
    resp = LLMResponse(
        text=json.dumps({"new_changelog_lines": ["x"], "section_updates": {}}),
        tool_uses=[],
        stop_reason="end_turn",
        usage=Usage(input_tokens=10, output_tokens=20),
    )
    cycle = _make_cycle(tmp_path, resp)
    await cycle.run_once()

    cycles = _counter_points(metric_reader, "mymcp.recorder.merge.cycles")
    assert _attrs_with(cycles, reason="success") == 1
    assert _attrs_with(cycles, reason="unparseable") == 0

    calls = _counter_points(metric_reader, "mymcp.recorder.llm.calls")
    assert _attrs_with(calls, phase="merge", result="success") == 1


@pytest.mark.anyio
async def test_empty_response_records_reason_empty(metric_reader, tmp_path):
    resp = LLMResponse(text="", tool_uses=[], stop_reason="end_turn", usage=Usage(5, 0))
    cycle = _make_cycle(tmp_path, resp)
    with pytest.raises(ValueError):
        await cycle.run_once()

    cycles = _counter_points(metric_reader, "mymcp.recorder.merge.cycles")
    assert _attrs_with(cycles, reason="empty") == 1
    assert _attrs_with(cycles, reason="unparseable") == 0
    # LLM HTTP call itself succeeded; only the response was unusable.
    calls = _counter_points(metric_reader, "mymcp.recorder.llm.calls")
    assert _attrs_with(calls, phase="merge", result="success") == 1


@pytest.mark.anyio
async def test_unparseable_response_records_reason_unparseable(metric_reader, tmp_path):
    resp = LLMResponse(text="{ not json", tool_uses=[], stop_reason="end_turn", usage=Usage(5, 5))
    cycle = _make_cycle(tmp_path, resp)
    with pytest.raises(ValueError):
        await cycle.run_once()

    cycles = _counter_points(metric_reader, "mymcp.recorder.merge.cycles")
    assert _attrs_with(cycles, reason="unparseable") == 1
    assert _attrs_with(cycles, reason="success") == 0


@pytest.mark.anyio
async def test_max_tokens_records_reason_max_tokens(metric_reader, tmp_path):
    resp = LLMResponse(
        text='{"new_changelog_lines": ["truncated...',
        tool_uses=[],
        stop_reason="max_tokens",
        usage=Usage(100, 16384),
    )
    cycle = _make_cycle(tmp_path, resp)
    with pytest.raises(ValueError):
        await cycle.run_once()

    cycles = _counter_points(metric_reader, "mymcp.recorder.merge.cycles")
    assert _attrs_with(cycles, reason="max_tokens") == 1
    # Should NOT also count as unparseable — max_tokens detection runs first.
    assert _attrs_with(cycles, reason="unparseable") == 0


@pytest.mark.anyio
async def test_schema_invalid_records_reason_schema_invalid(metric_reader, tmp_path):
    resp = LLMResponse(
        text=json.dumps({"new_changelog_lines": "not a list", "section_updates": {}}),
        tool_uses=[],
        stop_reason="end_turn",
        usage=Usage(5, 5),
    )
    cycle = _make_cycle(tmp_path, resp)
    with pytest.raises(ValueError):
        await cycle.run_once()

    cycles = _counter_points(metric_reader, "mymcp.recorder.merge.cycles")
    assert _attrs_with(cycles, reason="schema_invalid") == 1
    assert _attrs_with(cycles, reason="unparseable") == 0


@pytest.mark.anyio
async def test_http_error_records_reason_llm_error(metric_reader, tmp_path):
    cycle = _make_cycle(tmp_path, response=None, side_effect=RuntimeError("connection reset"))
    with pytest.raises(RuntimeError):
        await cycle.run_once()

    cycles = _counter_points(metric_reader, "mymcp.recorder.merge.cycles")
    assert _attrs_with(cycles, reason="llm_error") == 1
    # LLM call itself failed — recorded as http_error on llm_calls.
    calls = _counter_points(metric_reader, "mymcp.recorder.llm.calls")
    assert _attrs_with(calls, phase="merge", result="http_error") == 1
    assert _attrs_with(calls, phase="merge", result="success") == 0


@pytest.mark.anyio
async def test_no_events_records_reason_no_events(metric_reader, tmp_path):
    """An idle tick (no events to merge) is a valid 'no_events' outcome."""
    store = OverviewStore(tmp_path / "overview")
    store.write_overview("# Existing\n")
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    fake = AsyncMock()
    cycle = MergeCycle(client=fake, tailer=tailer, store=store, max_events_per_cycle=10)
    await cycle.run_once()

    cycles = _counter_points(metric_reader, "mymcp.recorder.merge.cycles")
    assert _attrs_with(cycles, reason="no_events") == 1
    # Did not call the LLM at all.
    calls = _counter_points(metric_reader, "mymcp.recorder.llm.calls")
    assert calls == []
