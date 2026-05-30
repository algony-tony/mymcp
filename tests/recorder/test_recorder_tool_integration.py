import json
from unittest.mock import AsyncMock

import pytest

from mymcp import mcp_server
from mymcp.recorder.bootstrap import Bootstrapper
from mymcp.recorder.events import EventTailer
from mymcp.recorder.llm.base import LLMResponse, Usage
from mymcp.recorder.merge_cycle import MergeCycle
from mymcp.recorder.overview import OverviewStore
from mymcp.recorder.task import RecorderSupervisor


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _reset_supervisor():
    yield
    mcp_server.set_recorder_supervisor(None)


@pytest.mark.anyio
async def test_server_overview_returns_stub_when_disabled():
    mcp_server.set_recorder_supervisor(None)
    raw = await mcp_server.dispatch_tool("server_overview", {})
    data = json.loads(raw)
    assert data["success"] is False
    assert data["error"] == "RecorderDisabled"


@pytest.mark.anyio
async def test_server_overview_returns_stub_when_overview_missing(tmp_path):
    store = OverviewStore(tmp_path / "overview")
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    fake = AsyncMock()
    fake.call = AsyncMock(
        return_value=LLMResponse(
            text="x",
            tool_uses=[],
            stop_reason="end_turn",
            usage=Usage(input_tokens=1, output_tokens=1),
        )
    )
    bootstrapper = Bootstrapper(client=fake, store=store, max_iterations=5, token_budget=1000)
    merge_cycle = MergeCycle(
        client=fake,
        tailer=tailer,
        store=store,
        max_events_per_cycle=10,
        require_bootstrap=True,
    )
    sup = RecorderSupervisor(
        merge_cycle=merge_cycle,
        bootstrapper=bootstrapper,
        merge_interval_sec=60,
        provider="anthropic",
        model="m",
    )
    mcp_server.set_recorder_supervisor(sup)

    raw = await mcp_server.dispatch_tool("server_overview", {})
    data = json.loads(raw)
    assert data["success"] is True
    assert "not initialized" in data["overview"].lower()


@pytest.mark.anyio
async def test_server_overview_returns_overview_when_present(tmp_path):
    store = OverviewStore(tmp_path / "overview")
    store.write_overview("# Server Overview\n\n## TL;DR\nok\n")
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    fake = AsyncMock()
    bootstrapper = Bootstrapper(client=fake, store=store, max_iterations=5, token_budget=1000)
    merge_cycle = MergeCycle(
        client=fake,
        tailer=tailer,
        store=store,
        max_events_per_cycle=10,
        require_bootstrap=True,
    )
    sup = RecorderSupervisor(
        merge_cycle=merge_cycle,
        bootstrapper=bootstrapper,
        merge_interval_sec=60,
    )
    mcp_server.set_recorder_supervisor(sup)
    raw = await mcp_server.dispatch_tool("server_overview", {})
    data = json.loads(raw)
    assert data["success"] is True
    assert "## TL;DR" in data["overview"]


def test_server_overview_in_read_tools():
    assert "server_overview" in mcp_server.READ_TOOLS
