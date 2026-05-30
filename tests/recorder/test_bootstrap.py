import asyncio
from unittest.mock import AsyncMock

import pytest

from mymcp.recorder.bootstrap import Bootstrapper, BootstrapState
from mymcp.recorder.llm.base import LLMResponse, ToolUse, Usage
from mymcp.recorder.overview import OverviewStore


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _resp_end(text: str, usage=(5, 5)) -> LLMResponse:
    return LLMResponse(
        text=text,
        tool_uses=[],
        stop_reason="end_turn",
        usage=Usage(input_tokens=usage[0], output_tokens=usage[1]),
    )


def _resp_tool_use(name: str, input: dict, usage=(5, 5), tool_id: str = "t1") -> LLMResponse:
    return LLMResponse(
        text="",
        tool_uses=[ToolUse(id=tool_id, name=name, input=input)],
        stop_reason="tool_use",
        usage=Usage(input_tokens=usage[0], output_tokens=usage[1]),
    )


@pytest.mark.anyio
async def test_bootstrap_simple_two_steps(tmp_path):
    store = OverviewStore(tmp_path / "overview")
    fake = AsyncMock()
    fake.call = AsyncMock(
        side_effect=[
            _resp_tool_use("bash_probe", {"command": "echo hello"}),
            _resp_end("# Server Overview\n\n## TL;DR\nUbuntu host.\n"),
        ]
    )
    b = Bootstrapper(client=fake, store=store, max_iterations=10, token_budget=100_000)
    result = await b.run_once()
    assert result.state == BootstrapState.SUCCEEDED
    overview = store.read_overview() or ""
    assert overview.startswith("# Server Overview")
    tail = store.read_changelog_tail(1)
    assert tail and "initial overview generated" in tail[-1]


@pytest.mark.anyio
async def test_bootstrap_iteration_cap_fails(tmp_path):
    store = OverviewStore(tmp_path / "overview")
    fake = AsyncMock()
    # never end_turn — always tool_use
    fake.call = AsyncMock(return_value=_resp_tool_use("bash_probe", {"command": "true"}))
    b = Bootstrapper(client=fake, store=store, max_iterations=3, token_budget=1_000_000)
    result = await b.run_once()
    assert result.state == BootstrapState.FAILED
    assert "iteration" in (result.error or "").lower()


@pytest.mark.anyio
async def test_bootstrap_token_budget_cap_fails(tmp_path):
    store = OverviewStore(tmp_path / "overview")
    fake = AsyncMock()
    # large per-call usage so budget exhausts quickly
    fake.call = AsyncMock(
        return_value=_resp_tool_use(
            "bash_probe",
            {"command": "true"},
            usage=(60_000, 60_000),
        )
    )
    b = Bootstrapper(client=fake, store=store, max_iterations=100, token_budget=100_000)
    result = await b.run_once()
    assert result.state == BootstrapState.FAILED
    assert "budget" in (result.error or "").lower()


@pytest.mark.anyio
async def test_bootstrap_empty_final_text_fails(tmp_path):
    store = OverviewStore(tmp_path / "overview")
    fake = AsyncMock()
    fake.call = AsyncMock(return_value=_resp_end(""))
    b = Bootstrapper(client=fake, store=store, max_iterations=5, token_budget=100_000)
    result = await b.run_once()
    assert result.state == BootstrapState.FAILED


@pytest.mark.anyio
async def test_bootstrap_unknown_tool_use_returns_error_to_llm(tmp_path):
    store = OverviewStore(tmp_path / "overview")
    fake = AsyncMock()
    fake.call = AsyncMock(
        side_effect=[
            _resp_tool_use("mystery_tool", {}),
            _resp_end("# Server Overview\n\n## TL;DR\nok\n"),
        ]
    )
    b = Bootstrapper(client=fake, store=store, max_iterations=5, token_budget=100_000)
    result = await b.run_once()
    assert result.state == BootstrapState.SUCCEEDED


@pytest.mark.anyio
async def test_bootstrap_concurrent_calls_coalesce(tmp_path):
    store = OverviewStore(tmp_path / "overview")
    fake = AsyncMock()
    call_log: list[int] = []

    async def slow_end(*args, **kwargs):
        call_log.append(1)
        await asyncio.sleep(0.05)
        return _resp_end("# Server Overview\n\n## TL;DR\nx\n")

    fake.call = slow_end
    b = Bootstrapper(client=fake, store=store, max_iterations=10, token_budget=100_000)

    r1, r2 = await asyncio.gather(b.run_once(), b.run_once())
    assert (r1.state, r2.state) == (BootstrapState.SUCCEEDED, BootstrapState.SUCCEEDED)
    # LLM called only once (coalesced)
    assert len(call_log) == 1


@pytest.mark.anyio
async def test_bootstrap_state_transitions(tmp_path):
    store = OverviewStore(tmp_path / "overview")
    fake = AsyncMock()
    fake.call = AsyncMock(return_value=_resp_end("# Server Overview\n"))
    b = Bootstrapper(client=fake, store=store, max_iterations=5, token_budget=100_000)
    assert b.state == BootstrapState.IDLE
    await b.run_once()
    assert b.state == BootstrapState.SUCCEEDED
