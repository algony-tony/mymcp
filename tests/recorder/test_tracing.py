"""Tests that recorder hot paths emit OTel spans."""

import json
from unittest.mock import AsyncMock

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from mymcp.recorder.bootstrap import Bootstrapper
from mymcp.recorder.events import EventTailer
from mymcp.recorder.llm.base import LLMResponse, ToolUse, Usage
from mymcp.recorder.merge_cycle import MergeCycle
from mymcp.recorder.overview import OverviewStore


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def span_exporter():
    """Install a fresh in-memory span processor on the current tracer provider."""
    from mymcp.observability import reset_for_tests, setup_observability

    reset_for_tests()
    setup_observability(app=None, service_name="mymcp-test", service_version="0.0.0")
    exporter = InMemorySpanExporter()
    trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(exporter))
    yield exporter
    exporter.clear()
    reset_for_tests()


def _audit_line(**fields) -> str:
    base = {"ts": "2026-05-29T10:00:00Z", "result": "ok"}
    base.update(fields)
    return json.dumps(base) + "\n"


@pytest.mark.anyio
async def test_merge_cycle_emits_span(span_exporter, tmp_path):
    (tmp_path / "audit.log").write_text(
        _audit_line(tool="bash_execute", params={"command": "x"}, output={"stdout_head": "x"})
    )
    store = OverviewStore(tmp_path / "overview")
    store.write_overview("# Existing\n")
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    fake = AsyncMock()
    fake.call = AsyncMock(
        return_value=LLMResponse(
            text=json.dumps(
                {
                    "new_changelog_lines": ["x"],
                    "updated_overview_md": "# New\n",
                }
            ),
            tool_uses=[],
            stop_reason="end_turn",
            usage=Usage(input_tokens=10, output_tokens=20),
        )
    )
    cycle = MergeCycle(client=fake, tailer=tailer, store=store, max_events_per_cycle=10)
    await cycle.run_once()
    finished = span_exporter.get_finished_spans()
    span_names = [s.name for s in finished]
    assert "recorder.merge_cycle" in span_names
    mc_span = next(s for s in finished if s.name == "recorder.merge_cycle")
    assert mc_span.attributes.get("events.in") == 1
    assert mc_span.attributes.get("tokens.in") == 10
    assert mc_span.attributes.get("tokens.out") == 20


@pytest.mark.anyio
async def test_merge_cycle_span_no_events(span_exporter, tmp_path):
    """Merge cycle emits a span even when there are no events (early-return path)."""
    store = OverviewStore(tmp_path / "overview")
    store.write_overview("# Existing\n")
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    fake = AsyncMock()
    cycle = MergeCycle(client=fake, tailer=tailer, store=store, max_events_per_cycle=10)
    result = await cycle.run_once()
    assert result.skipped_reason == "no_events"
    finished = span_exporter.get_finished_spans()
    span_names = [s.name for s in finished]
    assert "recorder.merge_cycle" in span_names
    mc_span = next(s for s in finished if s.name == "recorder.merge_cycle")
    assert mc_span.attributes.get("events.in") == 0


@pytest.mark.anyio
async def test_bootstrap_emits_nested_spans(span_exporter, tmp_path):
    store = OverviewStore(tmp_path / "overview")
    fake = AsyncMock()
    fake.call = AsyncMock(
        side_effect=[
            LLMResponse(
                text="",
                tool_uses=[ToolUse(id="t1", name="bash_probe", input={"command": "true"})],
                stop_reason="tool_use",
                usage=Usage(input_tokens=5, output_tokens=5),
            ),
            LLMResponse(
                text="# Server Overview\n",
                tool_uses=[],
                stop_reason="end_turn",
                usage=Usage(input_tokens=5, output_tokens=5),
            ),
        ]
    )
    b = Bootstrapper(client=fake, store=store, max_iterations=10, token_budget=100_000)
    await b.run_once()
    names = [s.name for s in span_exporter.get_finished_spans()]
    assert "recorder.bootstrap" in names
    assert "recorder.agent_iteration" in names
    assert "recorder.llm_call" in names
    # bash_probe span from bootstrap's inline probe dispatch
    assert "recorder.bash_probe" in names


@pytest.mark.anyio
async def test_bootstrap_run_id_attribute(span_exporter, tmp_path):
    store = OverviewStore(tmp_path / "overview")
    fake = AsyncMock()
    fake.call = AsyncMock(
        return_value=LLMResponse(
            text="# Server Overview\n",
            tool_uses=[],
            stop_reason="end_turn",
            usage=Usage(input_tokens=5, output_tokens=5),
        )
    )
    b = Bootstrapper(client=fake, store=store, max_iterations=10, token_budget=100_000)
    result = await b.run_once()
    finished = span_exporter.get_finished_spans()
    bootstrap_spans = [s for s in finished if s.name == "recorder.bootstrap"]
    assert bootstrap_spans
    bs = bootstrap_spans[0]
    assert bs.attributes.get("bootstrap.run_id") == result.run_id
