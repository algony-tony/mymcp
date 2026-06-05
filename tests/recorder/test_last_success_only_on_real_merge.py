"""Regression: _last_merge_ts must only advance on real merges.

`mymcp_recorder_merge_last_success_timestamp` powers the stale-merge SLO alert
(`time() - gauge > 3600`). If the supervisor advances the timestamp on idle
ticks (no_events / bootstrap_required), an entirely broken event processor
on a quiet server keeps looking healthy. Reported on PR #48.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from mymcp.recorder.bootstrap import BootstrapState
from mymcp.recorder.merge_cycle import MergeResult
from mymcp.recorder.task import RecorderSupervisor


@pytest.fixture
def anyio_backend():
    return "asyncio"


class _Store:
    def read_overview(self) -> str:
        return "# Existing\n"


def _supervisor(merge_mock: MagicMock) -> RecorderSupervisor:
    bootstrap = MagicMock()
    bootstrap.state = BootstrapState.SUCCEEDED
    type(merge_mock)._store = property(lambda _: _Store())
    return RecorderSupervisor(
        merge_cycle=merge_mock,
        bootstrapper=bootstrap,
        merge_interval_sec=0.01,
        provider="anthropic",
        model="x",
        circuit_breaker_threshold=99,
    )


async def _run_one_tick(sup: RecorderSupervisor) -> None:
    task = asyncio.create_task(sup.run())
    await asyncio.sleep(0.05)
    sup.shutdown()
    await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.anyio
async def test_no_events_does_not_advance_last_merge_ts():
    merge = MagicMock()
    merge.run_once = AsyncMock(
        return_value=MergeResult(events_consumed=0, skipped_reason="no_events")
    )
    sup = _supervisor(merge)
    assert sup._last_merge_ts is None
    await _run_one_tick(sup)
    assert sup._last_merge_ts is None, "idle no_events ticks must not advance the gauge"


@pytest.mark.anyio
async def test_bootstrap_required_does_not_advance_last_merge_ts():
    merge = MagicMock()
    merge.run_once = AsyncMock(
        return_value=MergeResult(events_consumed=0, skipped_reason="bootstrap_required")
    )
    sup = _supervisor(merge)
    await _run_one_tick(sup)
    assert sup._last_merge_ts is None


@pytest.mark.anyio
async def test_successful_merge_advances_last_merge_ts():
    merge = MagicMock()
    merge.run_once = AsyncMock(return_value=MergeResult(events_consumed=3, skipped_reason=None))
    sup = _supervisor(merge)
    await _run_one_tick(sup)
    assert sup._last_merge_ts is not None and sup._last_merge_ts > 0
