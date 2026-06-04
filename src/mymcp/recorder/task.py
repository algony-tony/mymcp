"""Asyncio supervisor driving the recorder's merge loop and bootstrap.

The supervisor is meant to be created during FastAPI lifespan startup and
cancelled cleanly during shutdown. It owns periodic merge ticks and ensures
a bootstrap runs when the overview is missing.
"""

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from mymcp.recorder.bootstrap import Bootstrapper, BootstrapState
from mymcp.recorder.merge_cycle import MergeCycle
from mymcp.recorder.overview import OverviewStore

log = logging.getLogger("mymcp.recorder")


@dataclass
class RecorderStatus:
    enabled: bool
    bootstrap_state: BootstrapState
    last_bootstrap_ts: str | None
    last_merge_ts: str | None
    last_merge_age_seconds: float | None
    pending_events: int
    last_error: str | None
    llm_provider: str
    llm_model: str | None
    consecutive_failures: int = 0
    circuit_open: bool = False


class RecorderSupervisor:
    """asyncio task driving bootstrap (when needed) + periodic merge cycles."""

    def __init__(
        self,
        *,
        merge_cycle: MergeCycle,
        bootstrapper: Bootstrapper,
        merge_interval_sec: float = 300.0,
        provider: str = "anthropic",
        model: str | None = None,
        circuit_breaker_threshold: int = 5,
    ):
        self._merge_cycle = merge_cycle
        self._bootstrap = bootstrapper
        self._interval = merge_interval_sec
        self._provider = provider
        self._model = model
        self._stop = asyncio.Event()
        self._force_bootstrap = False
        self._last_merge_ts: float | None = None
        self._last_bootstrap_ts: float | None = None
        self._last_error: str | None = None
        self._backoff = 30.0
        self._max_backoff = 600.0
        self._circuit_threshold = circuit_breaker_threshold
        self._consecutive_failures = 0
        self._circuit_open = False

    @property
    def merge_interval(self) -> float:
        return self._interval

    @property
    def store(self) -> OverviewStore:
        return self._merge_cycle._store

    @property
    def circuit_open(self) -> bool:
        return self._circuit_open

    async def run(self) -> None:
        log.info("recorder.supervisor.start")
        try:
            # Initial bootstrap if no overview yet
            if self.store.read_overview() is None:
                await self._do_bootstrap()

            while not self._stop.is_set():
                # Circuit open: stop calling the LLM entirely. Restart-only recovery.
                if self._circuit_open:
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
                    continue
                # Handle explicit bootstrap requests OR self-heal if overview vanished
                if self._force_bootstrap or self.store.read_overview() is None:
                    self._force_bootstrap = False
                    await self._do_bootstrap()
                try:
                    result = await self._merge_cycle.run_once()
                    self._last_merge_ts = time.time()
                    # Only clear last_error if it didn't originate from a still-failed bootstrap
                    if self._bootstrap.state != BootstrapState.FAILED:
                        self._last_error = None
                    self._backoff = 30.0
                    self._consecutive_failures = 0
                    _ = result  # could record events_consumed if needed
                except Exception as e:  # noqa: BLE001
                    log.exception("recorder.supervisor.cycle_error")
                    self._last_error = str(e)
                    self._consecutive_failures += 1
                    if self._consecutive_failures >= self._circuit_threshold:
                        self._circuit_open = True
                        log.error(
                            "recorder.supervisor.circuit_open",
                            extra={
                                "consecutive_failures": self._consecutive_failures,
                                "threshold": self._circuit_threshold,
                                "last_error": self._last_error,
                            },
                        )
                    self._backoff = min(self._backoff * 2, self._max_backoff)
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(self._stop.wait(), timeout=self._backoff)
                    continue
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
        finally:
            log.info("recorder.supervisor.stop")

    async def _do_bootstrap(self) -> None:
        try:
            result = await self._bootstrap.run_once()
            if result.state == BootstrapState.SUCCEEDED:
                self._last_bootstrap_ts = time.time()
                self._last_error = None
            elif result.state == BootstrapState.FAILED:
                self._last_error = result.error
        except Exception as e:  # noqa: BLE001
            log.exception("recorder.supervisor.bootstrap_error")
            self._last_error = str(e)

    def request_bootstrap(self) -> None:
        """Schedule a bootstrap to run on the next supervisor loop iteration."""
        self._force_bootstrap = True

    def shutdown(self) -> None:
        self._stop.set()

    def status(self) -> RecorderStatus:
        now = time.time()
        age = (now - self._last_merge_ts) if self._last_merge_ts is not None else None
        return RecorderStatus(
            enabled=True,
            bootstrap_state=self._bootstrap.state,
            last_bootstrap_ts=_iso(self._last_bootstrap_ts),
            last_merge_ts=_iso(self._last_merge_ts),
            last_merge_age_seconds=age,
            pending_events=0,  # filled when EventTailer exposes pending count
            last_error=self._last_error,
            llm_provider=self._provider,
            llm_model=self._model,
            consecutive_failures=self._consecutive_failures,
            circuit_open=self._circuit_open,
        )


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=UTC).isoformat()
