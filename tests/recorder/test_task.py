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
        llm_client=fake,
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
    assert status.last_merge_attempt_ts is None
    assert status.last_merge_attempt_age_seconds is None
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
async def test_supervisor_closes_llm_client_on_exit(tmp_path):
    """The supervisor owns the LLM client's HTTP resources: run() must
    close the client exactly once when the loop exits."""
    sup, _, fake = _build_sup(
        tmp_path,
        llm_responses=[_end("# Overview\n")] * 5,
        merge_interval=0.05,
    )
    task = asyncio.create_task(sup.run())
    await asyncio.sleep(0.2)
    sup.shutdown()
    await asyncio.wait_for(task, timeout=2)
    fake.aclose.assert_awaited_once()


@pytest.mark.anyio
async def test_supervisor_close_failure_does_not_raise(tmp_path):
    """A failing aclose() must not turn a clean shutdown into an error."""
    sup, _, fake = _build_sup(
        tmp_path,
        llm_responses=[_end("# Overview\n")] * 5,
        merge_interval=0.05,
    )
    fake.aclose = AsyncMock(side_effect=RuntimeError("already closed"))
    task = asyncio.create_task(sup.run())
    await asyncio.sleep(0.2)
    sup.shutdown()
    await asyncio.wait_for(task, timeout=2)
    fake.aclose.assert_awaited_once()


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


@pytest.mark.anyio
async def test_supervisor_opens_circuit_after_consecutive_failures(tmp_path):
    """After threshold consecutive merge failures, the supervisor must stop
    calling the LLM. Otherwise a poisoned event batch burns API quota forever."""
    import json

    store = OverviewStore(tmp_path / "overview")
    store.write_overview("# Server Overview\n\n## TL;DR\nok\n")
    audit_entry = json.dumps(
        {
            "ts": "2026-05-29T10:00:00Z",
            "result": "ok",
            "tool": "bash_execute",
            "params": {"command": "ls"},
            "output": {"stdout_head": "x"},
        }
    )
    (tmp_path / "audit.log").write_text(audit_entry + "\n")
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    fake = AsyncMock()
    fake.call = AsyncMock(return_value=_end("not json at all"))
    bootstrapper = Bootstrapper(client=fake, store=store, max_iterations=2)
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
        merge_interval_sec=0.01,
        circuit_breaker_threshold=3,
    )
    sup._backoff = 0.01
    sup._max_backoff = 0.01
    task = asyncio.create_task(sup.run())
    for _ in range(200):
        if sup.status().circuit_open:
            break
        await asyncio.sleep(0.02)
    sup.shutdown()
    await asyncio.wait_for(task, timeout=2)
    status = sup.status()
    assert status.circuit_open is True
    assert status.consecutive_failures >= 3
    calls_when_opened = fake.call.call_count
    await asyncio.sleep(0.05)
    assert fake.call.call_count == calls_when_opened


@pytest.mark.anyio
async def test_supervisor_threshold_zero_disables_circuit(tmp_path):
    """threshold=0 must mean 'never trip', per config docstring."""
    import json

    store = OverviewStore(tmp_path / "overview")
    store.write_overview("# Server Overview\n\n## TL;DR\nok\n")
    audit_entry = json.dumps(
        {
            "ts": "2026-05-29T10:00:00Z",
            "result": "ok",
            "tool": "bash_execute",
            "params": {"command": "ls"},
            "output": {"stdout_head": "x"},
        }
    )
    (tmp_path / "audit.log").write_text(audit_entry + "\n")
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    fake = AsyncMock()
    fake.call = AsyncMock(return_value=_end("not json at all"))
    bootstrapper = Bootstrapper(client=fake, store=store, max_iterations=2)
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
        merge_interval_sec=0.01,
        circuit_breaker_threshold=0,
    )
    sup._backoff = 0.01
    sup._max_backoff = 0.01
    task = asyncio.create_task(sup.run())
    for _ in range(50):
        if sup.status().consecutive_failures >= 5:
            break
        await asyncio.sleep(0.02)
    sup.shutdown()
    await asyncio.wait_for(task, timeout=2)
    status = sup.status()
    assert status.circuit_open is False
    assert status.consecutive_failures >= 5


@pytest.mark.anyio
async def test_supervisor_clears_failure_count_on_success(tmp_path):
    import json

    store = OverviewStore(tmp_path / "overview")
    store.write_overview("# Server Overview\n\n## TL;DR\nok\n")
    audit_entry = json.dumps(
        {
            "ts": "2026-05-29T10:00:00Z",
            "result": "ok",
            "tool": "bash_execute",
            "params": {"command": "ls"},
            "output": {"stdout_head": "x"},
        }
    )
    (tmp_path / "audit.log").write_text(audit_entry + "\n")
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    fake = AsyncMock()
    fake.call = AsyncMock(
        side_effect=[
            _end("not json at all"),
            _end('{"new_changelog_lines": [], "section_updates": {"TL;DR": "ok2"}}'),
        ]
    )
    bootstrapper = Bootstrapper(client=fake, store=store, max_iterations=2)
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
        merge_interval_sec=0.01,
        circuit_breaker_threshold=10,
    )
    sup._backoff = 0.01
    sup._max_backoff = 0.01
    task = asyncio.create_task(sup.run())
    for _ in range(200):
        if "ok2" in (store.read_overview() or ""):
            break
        await asyncio.sleep(0.02)
    sup.shutdown()
    await asyncio.wait_for(task, timeout=2)
    status = sup.status()
    assert status.circuit_open is False
    assert status.consecutive_failures == 0


@pytest.mark.anyio
async def test_idle_supervisor_does_not_call_llm(tmp_path):
    """pending_count == 0 → supervisor must not invoke merge_cycle.

    Previously the supervisor woke every merge_interval_sec and ran a
    cycle that returned no_events. That still cost an asyncio frame and
    pinned the event loop. Now we skip cheaply.
    """
    store = OverviewStore(tmp_path / "overview")
    store.write_overview("# Server Overview\n\n## TL;DR\nok\n")
    # No audit.log at all — pending_count() returns 0.
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    fake = AsyncMock()
    fake.call = AsyncMock(return_value=_end("# never called\n"))
    bootstrapper = Bootstrapper(client=fake, store=store, max_iterations=2)
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
        merge_interval_sec=0.01,
    )
    task = asyncio.create_task(sup.run())
    await asyncio.sleep(0.15)  # ~15 idle ticks worth
    sup.shutdown()
    await asyncio.wait_for(task, timeout=2)
    # The LLM was never asked anything.
    assert fake.call.call_count == 0
    # And no failure was recorded — idle is not failure.
    assert sup.status().consecutive_failures == 0
    assert sup.status().last_error is None


@pytest.mark.anyio
async def test_idle_does_not_advance_last_merge_attempt_ts(tmp_path):
    """No events ⇒ no attempt ⇒ last_merge_attempt_ts stays None.

    The new banner uses this field; idle systems must not look stalled.
    """
    store = OverviewStore(tmp_path / "overview")
    store.write_overview("# Server Overview\n\n## TL;DR\nok\n")
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    fake = AsyncMock()
    bootstrapper = Bootstrapper(client=fake, store=store, max_iterations=2)
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
        merge_interval_sec=0.01,
    )
    task = asyncio.create_task(sup.run())
    await asyncio.sleep(0.15)
    sup.shutdown()
    await asyncio.wait_for(task, timeout=2)
    assert sup.status().last_merge_attempt_ts is None


@pytest.mark.anyio
async def test_circuit_open_retries_when_pending_grows(tmp_path):
    """New events arriving after the breaker tripped must trigger one retry.

    This is the recovery path: previously the breaker required service
    restart. Now success on the retry clears it; failure leaves it open
    until further new work arrives.
    """
    import json

    def _audit_entry(i: int) -> str:
        return json.dumps(
            {
                "ts": f"2026-05-29T10:00:{i:02d}Z",
                "result": "ok",
                "tool": "bash_execute",
                "params": {"command": f"ls {i}"},
                "output": {"stdout_head": "x"},
            }
        )

    store = OverviewStore(tmp_path / "overview")
    store.write_overview("# Server Overview\n\n## TL;DR\nok\n")
    audit_path = tmp_path / "audit.log"
    audit_path.write_text(_audit_entry(0) + "\n")
    tailer = EventTailer(log_dir=tmp_path, cursor_path=tmp_path / "cursor.json")
    fake = AsyncMock()
    # First 3 attempts fail (trips breaker at threshold=3); then succeed.
    fake.call = AsyncMock(
        side_effect=[
            _end("not json"),
            _end("not json"),
            _end("not json"),
            _end('{"new_changelog_lines": [], "section_updates": {"TL;DR": "after"}}'),
        ]
    )
    bootstrapper = Bootstrapper(client=fake, store=store, max_iterations=2)
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
        merge_interval_sec=0.01,
        circuit_breaker_threshold=3,
    )
    sup._backoff = 0.01
    sup._max_backoff = 0.01

    task = asyncio.create_task(sup.run())
    # Wait for breaker to trip
    for _ in range(200):
        if sup.status().circuit_open:
            break
        await asyncio.sleep(0.01)
    assert sup.status().circuit_open is True
    calls_when_opened = fake.call.call_count

    # While breaker is open and no new work arrives, no retries happen.
    await asyncio.sleep(0.05)
    assert fake.call.call_count == calls_when_opened

    # New event arrives — pending grows past the high-water mark.
    with audit_path.open("a") as f:
        f.write(_audit_entry(1) + "\n")

    # Wait for the retry to fire (success path clears the breaker).
    for _ in range(200):
        if not sup.status().circuit_open:
            break
        await asyncio.sleep(0.01)

    sup.shutdown()
    await asyncio.wait_for(task, timeout=2)
    assert sup.status().circuit_open is False
    # Exactly one extra LLM call past the trip point — the recovery retry.
    assert fake.call.call_count == calls_when_opened + 1
