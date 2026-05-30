import asyncio
from unittest.mock import AsyncMock

import pytest

from mymcp.recorder.bootstrap import Bootstrapper, BootstrapState
from mymcp.recorder.events import EventTailer
from mymcp.recorder.llm.base import LLMResponse, Usage
from mymcp.recorder.merge_cycle import MergeCycle
from mymcp.recorder.overview import OverviewStore
from mymcp.recorder.task import RecorderStatus, RecorderSupervisor


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _end(text: str) -> LLMResponse:
    return LLMResponse(
        text=text,
        tool_uses=[],
        stop_reason="end_turn",
        usage=Usage(input_tokens=5, output_tokens=5),
    )


def _build_sup(tmp_path, *, llm_responses: list[LLMResponse], merge_interval=0.05):
    store = OverviewStore(tmp_path / "overview")
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    fake = AsyncMock()
    fake.call = AsyncMock(side_effect=llm_responses)
    bootstrapper = Bootstrapper(
        client=fake,
        store=store,
        max_iterations=10,
        token_budget=100_000,
    )
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
        merge_interval_sec=merge_interval,
        provider="anthropic",
        model="m",
    )
    return sup, store, fake


@pytest.mark.anyio
async def test_supervisor_runs_bootstrap_on_startup_when_overview_missing(tmp_path):
    sup, store, fake = _build_sup(
        tmp_path,
        llm_responses=[_end("# Server Overview\n\n## TL;DR\nok\n")],
    )
    task = asyncio.create_task(sup.run())
    for _ in range(40):
        if store.read_overview() is not None:
            break
        await asyncio.sleep(0.05)
    sup.shutdown()
    await asyncio.wait_for(task, timeout=2)
    assert store.read_overview() is not None
    assert store.read_overview().startswith("# Server Overview")


@pytest.mark.anyio
async def test_supervisor_status_initial_shape(tmp_path):
    sup, _, _ = _build_sup(tmp_path, llm_responses=[])
    status = sup.status()
    assert isinstance(status, RecorderStatus)
    assert status.enabled is True
    assert status.bootstrap_state == BootstrapState.IDLE
    assert status.last_bootstrap_ts is None
    assert status.last_merge_ts is None
    assert status.last_error is None
    assert status.llm_provider == "anthropic"


@pytest.mark.anyio
async def test_supervisor_shutdown_is_graceful(tmp_path):
    sup, _, _ = _build_sup(
        tmp_path,
        llm_responses=[_end("# Overview\n")] * 5,  # plenty of responses
        merge_interval=0.05,
    )
    task = asyncio.create_task(sup.run())
    await asyncio.sleep(0.2)
    sup.shutdown()
    await asyncio.wait_for(task, timeout=2)
    # task should have completed cleanly without raising


@pytest.mark.anyio
async def test_supervisor_records_error_on_failure(tmp_path):
    """When bootstrap fails (e.g. LLM raises), last_error gets set."""
    store = OverviewStore(tmp_path / "overview")
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    fake = AsyncMock()
    fake.call = AsyncMock(side_effect=RuntimeError("api 500"))
    bootstrapper = Bootstrapper(
        client=fake,
        store=store,
        max_iterations=2,
        token_budget=100_000,
    )
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
        merge_interval_sec=0.05,
    )
    task = asyncio.create_task(sup.run())
    for _ in range(60):
        if sup.status().last_error is not None:
            break
        await asyncio.sleep(0.05)
    sup.shutdown()
    await asyncio.wait_for(task, timeout=2)
    err = sup.status().last_error
    assert err is not None
    assert "api 500" in err
