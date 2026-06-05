"""Tests for the merge_duration_seconds histogram."""

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


def _hist_points(reader: InMemoryMetricReader, name: str) -> list[tuple[dict, int, float]]:
    """Return [(attributes, count, sum), ...] for the named histogram."""
    data = reader.get_metrics_data()
    out: list[tuple[dict, int, float]] = []
    if data is None:
        return out
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                if m.name != name:
                    continue
                for pt in m.data.data_points:
                    out.append((dict(pt.attributes), pt.count, pt.sum))
    return out


def _line(**fields) -> str:
    base = {
        "ts": "2026-06-05T10:00:00Z",
        "tool": "bash_execute",
        "result": "ok",
        "params": {"command": "x"},
        "output": {"stdout_head": "y"},
    }
    base.update(fields)
    return json.dumps(base) + "\n"


def _build(tmp_path, response: LLMResponse | None = None, side_effect=None) -> MergeCycle:
    (tmp_path / "audit.log").write_text(_line())
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
async def test_histogram_records_successful_cycle(metric_reader, tmp_path):
    resp = LLMResponse(
        text=json.dumps({"new_changelog_lines": ["x"], "section_updates": {}}),
        tool_uses=[],
        stop_reason="end_turn",
        usage=Usage(input_tokens=5, output_tokens=5),
    )
    cycle = _build(tmp_path, resp)
    await cycle.run_once()

    points = _hist_points(metric_reader, "mymcp.recorder.merge.duration")
    success = [p for p in points if p[0].get("reason") == "success"]
    assert len(success) == 1
    attrs, count, total = success[0]
    assert count == 1
    assert total > 0


@pytest.mark.anyio
async def test_histogram_records_failed_cycle_with_reason(metric_reader, tmp_path):
    """A merge that fails on unparseable JSON should still be timed."""
    resp = LLMResponse(text="{ bad", tool_uses=[], stop_reason="end_turn", usage=Usage(5, 5))
    cycle = _build(tmp_path, resp)
    with pytest.raises(ValueError):
        await cycle.run_once()

    points = _hist_points(metric_reader, "mymcp.recorder.merge.duration")
    unparseable = [p for p in points if p[0].get("reason") == "unparseable"]
    assert len(unparseable) == 1
    assert unparseable[0][1] == 1  # count


@pytest.mark.anyio
async def test_histogram_records_no_events_cycle(metric_reader, tmp_path):
    """An idle (no_events) tick is fast but still timed — measures tailer scan cost."""
    store = OverviewStore(tmp_path / "overview")
    store.write_overview("# Existing\n")
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    fake = AsyncMock()
    cycle = MergeCycle(client=fake, tailer=tailer, store=store, max_events_per_cycle=10)
    await cycle.run_once()

    points = _hist_points(metric_reader, "mymcp.recorder.merge.duration")
    no_events = [p for p in points if p[0].get("reason") == "no_events"]
    assert len(no_events) == 1


@pytest.mark.anyio
async def test_histogram_records_llm_error(metric_reader, tmp_path):
    cycle = _build(tmp_path, response=None, side_effect=RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        await cycle.run_once()

    points = _hist_points(metric_reader, "mymcp.recorder.merge.duration")
    llm_err = [p for p in points if p[0].get("reason") == "llm_error"]
    assert len(llm_err) == 1
