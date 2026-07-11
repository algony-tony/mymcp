"""Standalone entry point for the recorder sidecar (`mymcp-recorder`).

In v3 the recorder is a separate process from the Go core. It tails the core's
audit.log, folds mutating events into overview.md via an LLM, and exits cleanly
on SIGTERM/SIGINT. It serves no HTTP; bootstrap runs automatically when no
overview exists.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys

from mymcp.config import get_settings
from mymcp.recorder.wiring import build_supervisor

log = logging.getLogger("mymcp.recorder")


async def _run(supervisor, stop: asyncio.Event) -> None:
    """Drive supervisor.run() until stop is set, then shut it down and drain."""
    task = asyncio.create_task(supervisor.run())
    await stop.wait()
    supervisor.shutdown()
    with contextlib.suppress(Exception):
        await asyncio.wait_for(task, timeout=10)


async def _amain(supervisor) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    await _run(supervisor, stop)


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    if not settings.recorder_enabled:
        print(
            "mymcp-recorder: MYMCP_RECORDER_ENABLED is not true; refusing to start.",
            file=sys.stderr,
        )
        return 1
    supervisor = build_supervisor(settings)
    log.info("mymcp-recorder: starting (data_dir=%s)", settings.recorder_data_dir)
    asyncio.run(_amain(supervisor))
    log.info("mymcp-recorder: stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
