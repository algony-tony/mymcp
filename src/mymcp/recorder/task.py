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

from mymcp.observability.tracing import get_tracer
from mymcp.recorder.bootstrap import Bootstrapper, BootstrapState
from mymcp.recorder.merge_cycle import MergeCycle
from mymcp.recorder.overview import OverviewStore

log = logging.getLogger("mymcp.recorder")
_tracer = get_tracer(__name__)


@dataclass
class RecorderStatus:
    enabled: bool
    bootstrap_state: BootstrapState
    last_bootstrap_ts: str | None
    last_merge_ts: str | None
    last_merge_age_seconds: float | None
    last_merge_attempt_ts: str | None
    last_merge_attempt_age_seconds: float | None
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
        self._last_merge_attempt_ts: float | None = None
        self._last_bootstrap_ts: float | None = None
        self._last_error: str | None = None
        # When the circuit is open, this records pending_count at the moment
        # the breaker tripped. The supervisor only retries when pending grows
        # past it — i.e. genuinely new work has arrived.
        self._circuit_open_pending_high_water: int = 0
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

    @property
    def last_merge_ts_unix(self) -> float | None:
        """Public read of the last successful merge timestamp.

        Use this from observability callbacks instead of the private
        ``_last_merge_ts`` attribute — a future rename then fails mypy
        loudly rather than silently emitting `0` forever from the gauge.
        """
        return self._last_merge_ts

    @property
    def last_merge_attempt_ts_unix(self) -> float | None:
        """Public read of the last merge attempt timestamp (success OR fail).

        Counterpart to ``last_merge_ts_unix``; ``None`` until the first
        non-idle tick has run.
        """
        return self._last_merge_attempt_ts

    def _pending_count_safe(self) -> int:
        """Cheap, exception-swallowing read of the audit-log backlog size."""
        try:
            return int(self._merge_cycle._tailer.pending_count())
        except Exception:
            return 0

    async def run(self) -> None:
        log.info("recorder.supervisor.start")
        try:
            # Initial bootstrap if no overview yet
            if self.store.read_overview() is None:
                await self._do_bootstrap()

            while not self._stop.is_set():
                # Handle explicit bootstrap requests OR self-heal if overview vanished
                if self._force_bootstrap or self.store.read_overview() is None:
                    self._force_bootstrap = False
                    await self._do_bootstrap()

                pending = self._pending_count_safe()

                # Idle: nothing to record. Don't call the LLM (saves cost and
                # tokens) and don't count as a failure — being quiet is not
                # broken. last_merge_attempt_ts deliberately stays as-is so the
                # banner doesn't report idle as "stalled".
                if pending == 0:
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
                    continue

                # Circuit open: only retry when genuinely new work has arrived
                # since the breaker tripped. Avoids hammering a still-broken
                # LLM but does NOT require a service restart to recover.
                if self._circuit_open and pending <= self._circuit_open_pending_high_water:
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
                    continue

                # Wrap each tick in its own span so the error log below picks
                # up trace_id/span_id via _ContextFilter, and so the merge_cycle
                # span attaches as a child for trace-view correlation.
                with _tracer.start_as_current_span("recorder.supervisor.cycle"):
                    try:
                        # Attempt timestamp updates on every real attempt
                        # (success OR failure) but NOT on the idle/skip paths
                        # above. This is the cleaner staleness signal than
                        # last_merge_ts (which only moves on success).
                        self._last_merge_attempt_ts = time.time()
                        result = await self._merge_cycle.run_once()
                        # Only advance "last successful merge" when a real merge happened.
                        # Idle ticks (no_events / bootstrap_required) would otherwise mask
                        # a silently broken event processor from the stale-merge SLO alert.
                        if result.skipped_reason is None:
                            self._last_merge_ts = time.time()
                        # Only clear last_error if it didn't originate from a still-failed bootstrap
                        if self._bootstrap.state != BootstrapState.FAILED:
                            self._last_error = None
                        self._backoff = 30.0
                        self._consecutive_failures = 0
                        # Success clears the breaker — event-driven recovery.
                        # No service restart needed when the LLM comes back.
                        self._circuit_open = False
                        self._circuit_open_pending_high_water = 0
                        _ = result  # could record events_consumed if needed
                    except Exception as e:  # noqa: BLE001
                        log.exception("recorder.supervisor.cycle_error")
                        self._last_error = str(e)
                        self._consecutive_failures += 1
                        # MergeCycle.run_once() may have advanced (or rolled
                        # back) the tailer cursor before raising, so the
                        # backlog after the failure can differ from the value
                        # we read at the top of the loop. Read it again so
                        # the high-water reflects post-failure reality;
                        # otherwise the next-tick guard `pending <= high_water`
                        # could livelock until N more events arrive.
                        post_failure_pending = self._pending_count_safe()
                        if (
                            self._circuit_threshold > 0
                            and self._consecutive_failures >= self._circuit_threshold
                            and not self._circuit_open
                        ):
                            self._circuit_open = True
                            self._circuit_open_pending_high_water = post_failure_pending
                            log.error(
                                "recorder.supervisor.circuit_open",
                                extra={
                                    "consecutive_failures": self._consecutive_failures,
                                    "threshold": self._circuit_threshold,
                                    "last_error": self._last_error,
                                },
                            )
                        elif self._circuit_open:
                            # Already open and we just retried because new
                            # work arrived; bump the high-water so the next
                            # retry waits for further new events.
                            self._circuit_open_pending_high_water = post_failure_pending
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
        attempt_age = (
            (now - self._last_merge_attempt_ts) if self._last_merge_attempt_ts is not None else None
        )
        try:
            pending = int(self._merge_cycle._tailer.pending_count())
        except Exception:
            pending = 0
        return RecorderStatus(
            enabled=True,
            bootstrap_state=self._bootstrap.state,
            last_bootstrap_ts=_iso(self._last_bootstrap_ts),
            last_merge_ts=_iso(self._last_merge_ts),
            last_merge_age_seconds=age,
            last_merge_attempt_ts=_iso(self._last_merge_attempt_ts),
            last_merge_attempt_age_seconds=attempt_age,
            pending_events=pending,
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
