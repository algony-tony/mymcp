import asyncio

import pytest

from mymcp.recorder.__main__ import _run, main


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeSupervisor:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self._ev = asyncio.Event()

    async def run(self) -> None:
        self.started = True
        await self._ev.wait()

    def shutdown(self) -> None:
        self.stopped = True
        self._ev.set()


@pytest.mark.anyio
async def test_run_drives_and_stops_on_signal():
    sup = FakeSupervisor()
    stop = asyncio.Event()

    async def trigger():
        await asyncio.sleep(0.05)
        stop.set()

    await asyncio.gather(_run(sup, stop), trigger())
    assert sup.started, "supervisor.run() must be driven"
    assert sup.stopped, "supervisor.shutdown() must be called on stop"


def test_main_requires_recorder_enabled(monkeypatch):
    # Without MYMCP_RECORDER_ENABLED the sidecar refuses to start (exit 1),
    # so a misconfigured unit fails loudly instead of idling.
    monkeypatch.setenv("MYMCP_RECORDER_ENABLED", "false")
    import mymcp.config as cfg

    cfg.reset_settings_cache()
    assert main() == 1
