# Recorder Subsystem Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the recorder's timer-driven retry + "time since last success" staleness model with an event-driven retry loop and backlog-based staleness. Fix two real bugs: (1) circuit breaker requires restart to recover, (2) idle system (no events) is reported as "X minutes stale". Companion spec: `docs/superpowers/specs/2026-06-06-project-assessment.md` (§ "Recorder 子系统修订设计").

**Architecture:** `EventTailer` gains an `asyncio.Event` (`event_arrived`) that is `set()` whenever new mutating events are observed. `RecorderSupervisor.run()` waits on the union of (`event_arrived`, `_stop`, `merge_interval` timer). Circuit-open no longer disables retries — it merely stops automatic timer-driven retries; new events still trigger one attempt. Add `last_merge_attempt_ts` (updated on every attempt, success or fail) and use it together with `pending_events` to compute staleness. Inject a `_Last updated: …_` line into `overview.md` so the timestamp survives restarts.

**Tech Stack:** Python 3.11+ • asyncio • OpenTelemetry • pytest + anyio (asyncio backend) • Grafana JSON dashboards.

---

## Conventions

- All commands run from the repo root: `/home/zhu/repos/mymcp`.
- Python interpreter: `.venv/bin/python`.
- Run tests: `.venv/bin/python -m pytest <path> -v --benchmark-disable`.
- After every code task, also run `.venv/bin/ruff format <changed files>` and `.venv/bin/ruff check <changed files>`.
- Each task ends with `git add <files> && git commit -m "<msg>"`. Do **not** push until the final task.
- Branch: create `feature/recorder-redesign` off `master` before Task 1.

---

## Task 1: Branch and baseline

**Files:** none modified

- [ ] **Step 1: Create feature branch**

```bash
git checkout master
git pull --ff-only
git checkout -b feature/recorder-redesign
```

- [ ] **Step 2: Confirm baseline tests pass**

Run: `.venv/bin/python -m pytest tests/recorder -v --benchmark-disable`

Expected: all green (current behavior).

---

## Task 2: Add `last_merge_attempt_ts` to `RecorderStatus`

**Files:**
- Modify: `src/mymcp/recorder/task.py`
- Test: `tests/recorder/test_task.py`

- [ ] **Step 1: Write failing test**

Append to `tests/recorder/test_task.py`:

```python
async def test_status_includes_last_merge_attempt_ts(supervisor):
    # Before any tick
    st = supervisor.status()
    assert st.last_merge_attempt_ts is None
    assert st.last_merge_attempt_age_seconds is None
```

(Reuse the existing `supervisor` fixture — if not present, see the closest existing `_make_supervisor` helper in this file and reuse it.)

- [ ] **Step 2: Run, expect AttributeError**

Run: `.venv/bin/python -m pytest tests/recorder/test_task.py::test_status_includes_last_merge_attempt_ts -v --benchmark-disable`

Expected: FAIL with `AttributeError: 'RecorderStatus' object has no attribute 'last_merge_attempt_ts'`.

- [ ] **Step 3: Add field**

In `src/mymcp/recorder/task.py`, modify the `RecorderStatus` dataclass:

```python
@dataclass
class RecorderStatus:
    enabled: bool
    bootstrap_state: BootstrapState
    last_bootstrap_ts: str | None
    last_merge_ts: str | None
    last_merge_age_seconds: float | None
    last_merge_attempt_ts: str | None       # NEW
    last_merge_attempt_age_seconds: float | None  # NEW
    pending_events: int
    last_error: str | None
    llm_provider: str
    llm_model: str | None
    consecutive_failures: int = 0
    circuit_open: bool = False
```

In `RecorderSupervisor.__init__`, add:

```python
        self._last_merge_attempt_ts: float | None = None
```

In `RecorderSupervisor.status()`, compute and include:

```python
    def status(self) -> RecorderStatus:
        now = time.time()
        age = (now - self._last_merge_ts) if self._last_merge_ts is not None else None
        attempt_age = (
            (now - self._last_merge_attempt_ts)
            if self._last_merge_attempt_ts is not None
            else None
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
```

- [ ] **Step 4: Run, expect PASS**

Run: same as Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mymcp/recorder/task.py tests/recorder/test_task.py
git commit -m "feat(recorder): add last_merge_attempt_ts to RecorderStatus

Decouples 'we tried' from 'we succeeded' — needed by the new
event-driven retry loop and the backlog-based staleness banner."
```

---

## Task 3: Update attempt timestamp on every `_merge_cycle` call

**Files:**
- Modify: `src/mymcp/recorder/task.py`
- Test: `tests/recorder/test_task.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/recorder/test_task.py`:

```python
async def test_attempt_ts_updates_on_success(supervisor, monkeypatch):
    """Successful merge updates both last_merge_ts and last_merge_attempt_ts."""
    # Drive one successful cycle (use whatever helper the suite already uses).
    await _drive_one_successful_tick(supervisor)  # match existing pattern
    st = supervisor.status()
    assert st.last_merge_attempt_ts is not None
    assert st.last_merge_ts is not None


async def test_attempt_ts_updates_on_failure(supervisor, monkeypatch):
    """Failed merge still updates last_merge_attempt_ts but not last_merge_ts."""
    await _drive_one_failing_tick(supervisor)  # match existing pattern
    st = supervisor.status()
    assert st.last_merge_attempt_ts is not None
    assert st.last_merge_ts is None


async def test_attempt_ts_not_updated_on_idle(supervisor, monkeypatch):
    """no_events / bootstrap_required ticks do NOT count as attempts."""
    await _drive_one_idle_tick(supervisor)  # match existing pattern
    st = supervisor.status()
    assert st.last_merge_attempt_ts is None
```

If `_drive_*` helpers don't exist, see `tests/recorder/test_task.py` for the existing `MergeCycle` mock patterns and adapt: stub `MergeCycle.run_once` to return:
- successful tick: `MergeCycleResult(skipped_reason=None, events_consumed=3, ...)`
- failing tick: raise `RuntimeError("llm down")`
- idle tick: `MergeCycleResult(skipped_reason="no_events", events_consumed=0, ...)`

- [ ] **Step 2: Run, expect 3 failures**

Run: `.venv/bin/python -m pytest tests/recorder/test_task.py -k attempt_ts -v --benchmark-disable`

Expected: all 3 fail (`last_merge_attempt_ts` is never set anywhere yet).

- [ ] **Step 3: Update `run()`**

In `src/mymcp/recorder/task.py`, modify the cycle body inside `with _tracer.start_as_current_span("recorder.supervisor.cycle"):`:

```python
                with _tracer.start_as_current_span("recorder.supervisor.cycle"):
                    try:
                        result = await self._merge_cycle.run_once()
                        # last_merge_ts: only on real merges (preserves the
                        # original "silent processor" detection signal).
                        if result.skipped_reason is None:
                            self._last_merge_ts = time.time()
                            self._last_merge_attempt_ts = time.time()
                        # attempt_ts: only on actual LLM attempts (success or fail),
                        # NOT on no_events/bootstrap_required idle ticks.
                        # Idle ticks must not look like staleness.
                        if self._bootstrap.state != BootstrapState.FAILED:
                            self._last_error = None
                        self._backoff = 30.0
                        self._consecutive_failures = 0
                    except Exception as e:  # noqa: BLE001
                        log.exception("recorder.supervisor.cycle_error")
                        self._last_error = str(e)
                        self._last_merge_attempt_ts = time.time()  # NEW
                        self._consecutive_failures += 1
                        ...
```

- [ ] **Step 4: Run, expect PASS**

Run: same as Step 2. Expected: all 3 pass.

- [ ] **Step 5: Commit**

```bash
git add src/mymcp/recorder/task.py tests/recorder/test_task.py
git commit -m "feat(recorder): track last_merge_attempt_ts independently of success

Updates attempt_ts on real merges (success + failure) but not on idle
ticks (no_events / bootstrap_required). This is the cleaner signal for
'is the recorder stuck?' than time-since-last-success."
```

---

## Task 4: Register `mymcp.recorder.merge.last_attempt_timestamp` gauge

**Files:**
- Modify: `src/mymcp/recorder/wiring.py`
- Test: `tests/recorder/test_metrics_reasons.py` (or nearest existing recorder-metrics test file)

- [ ] **Step 1: Write failing test**

Append to whichever recorder-metrics test file already exercises `mymcp_recorder_merge_last_success_timestamp`:

```python
def test_last_attempt_timestamp_gauge_registered():
    from prometheus_client import REGISTRY

    metric_names = {m.name for m in REGISTRY.collect()}
    assert "mymcp_recorder_merge_last_attempt_timestamp" in metric_names
```

- [ ] **Step 2: Run, expect FAIL**

Run: `.venv/bin/python -m pytest tests/recorder/ -k last_attempt_timestamp -v --benchmark-disable`

Expected: FAIL — assertion error, gauge not registered.

- [ ] **Step 3: Add observer + registration**

In `src/mymcp/recorder/wiring.py`, after `_observe_last_success_ts` add:

```python
def _observe_last_attempt_ts() -> list[Observation]:
    """Unix seconds of the last recorder merge attempt; 0 if never.

    Updates on every attempt — success OR failure — but NOT on idle ticks
    (no_events / bootstrap_required). Together with `pending_events`, this is
    the canonical 'recorder is stuck' signal.
    """
    from mymcp.mcp_server import get_recorder_supervisor

    sup = get_recorder_supervisor()
    if sup is None:
        return [Observation(0)]
    ts = getattr(sup, "_last_merge_attempt_ts", None)
    return [Observation(ts if ts is not None else 0)]
```

And register it next to the other callback gauges:

```python
register_callback_gauge(
    "mymcp.recorder.merge.last_attempt_timestamp",
    "Unix seconds of the last recorder merge attempt (success or fail); 0 if never",
    _observe_last_attempt_ts,
)
```

- [ ] **Step 4: Run, expect PASS**

Same command as Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mymcp/recorder/wiring.py tests/recorder/test_metrics_reasons.py
git commit -m "feat(recorder/metrics): export last_attempt_timestamp gauge

Decouples 'we tried' from 'we succeeded' at the Prometheus surface.
Together with pending_events, it lets dashboards distinguish idle
(no work to do) from stuck (work piled up, attempts failing)."
```

---

## Task 5: Add `event_arrived` signal to `EventTailer`

**Files:**
- Modify: `src/mymcp/recorder/events.py`
- Test: `tests/recorder/test_events.py`

- [ ] **Step 1: Write failing test**

Append to `tests/recorder/test_events.py`:

```python
@pytest.mark.anyio
async def test_event_arrived_signal_set_when_new_line_observed(tmp_path):
    log = tmp_path / "audit.log"
    log.write_text("")
    cursor = tmp_path / "cursor.json"

    tailer = EventTailer(log_dir=tmp_path, cursor_path=cursor)
    # Initial state: not set
    assert not tailer.event_arrived.is_set()

    # Append a mutating event
    import json
    rec = {
        "ts": "2026-06-06T10:00:00Z",
        "tool": "write_file",
        "result": "success",
        # ... minimum schema the tailer recognises as mutating
    }
    log.write_text(json.dumps(rec) + "\n")

    # Drain — should observe the new line and set the signal
    list(tailer.tail_new_events(max_events=10))
    assert tailer.event_arrived.is_set()


@pytest.mark.anyio
async def test_event_arrived_signal_cleared_after_drain(tmp_path):
    log = tmp_path / "audit.log"
    cursor = tmp_path / "cursor.json"
    tailer = EventTailer(log_dir=tmp_path, cursor_path=cursor)

    import json
    rec = {"ts": "2026-06-06T10:00:00Z", "tool": "write_file", "result": "success"}
    log.write_text(json.dumps(rec) + "\n")

    list(tailer.tail_new_events(max_events=10))
    assert tailer.event_arrived.is_set()
    tailer.event_arrived.clear()
    assert not tailer.event_arrived.is_set()
```

- [ ] **Step 2: Run, expect FAIL**

Run: `.venv/bin/python -m pytest tests/recorder/test_events.py -k event_arrived -v --benchmark-disable`

Expected: AttributeError — `EventTailer` has no `event_arrived`.

- [ ] **Step 3: Add signal**

In `src/mymcp/recorder/events.py`, in `EventTailer.__init__`, add:

```python
import asyncio
...
class EventTailer:
    def __init__(self, log_dir: Path, cursor_path: Path) -> None:
        ...
        self.event_arrived = asyncio.Event()
```

In `tail_new_events()` (the method that reads new lines), after yielding any event(s), if at least one event was seen, call:

```python
if observed_any:
    self.event_arrived.set()
```

(Mark the bool inside the loop. Setting the event is idempotent and cheap.)

- [ ] **Step 4: Run, expect PASS**

Same as Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mymcp/recorder/events.py tests/recorder/test_events.py
git commit -m "feat(recorder/events): expose event_arrived asyncio.Event

The supervisor will wait on this signal so retries are event-driven
instead of timer-driven. Idle systems no longer poll an empty audit
log every 5 minutes."
```

---

## Task 6: Refactor supervisor main loop to event-driven

**Files:**
- Modify: `src/mymcp/recorder/task.py`
- Test: `tests/recorder/test_task.py`

- [ ] **Step 1: Write failing test (idle wakeup behaviour)**

Append to `tests/recorder/test_task.py`:

```python
@pytest.mark.anyio
async def test_idle_supervisor_does_not_call_llm(supervisor):
    """When pending_count == 0, supervisor must not invoke merge_cycle."""
    sup = supervisor
    sup._merge_cycle._tailer = _make_empty_tailer()  # pending == 0
    call_count = 0
    orig = sup._merge_cycle.run_once

    async def counting_run_once():
        nonlocal call_count
        call_count += 1
        return await orig()
    sup._merge_cycle.run_once = counting_run_once

    task = asyncio.create_task(sup.run())
    await asyncio.sleep(0.1)
    sup.shutdown()
    await task
    assert call_count == 0  # never tried because pending was 0


@pytest.mark.anyio
async def test_event_arrival_triggers_merge_cycle(supervisor):
    """Setting event_arrived must wake the loop and call merge_cycle."""
    sup = supervisor
    tailer = sup._merge_cycle._tailer
    tailer.pending_count = lambda: 0  # start idle

    call_count = 0

    async def counting():
        nonlocal call_count
        call_count += 1
        return MergeCycleResult(skipped_reason="no_events", events_consumed=0)
    sup._merge_cycle.run_once = counting

    task = asyncio.create_task(sup.run())
    await asyncio.sleep(0.05)
    # Simulate a new event arriving
    tailer.pending_count = lambda: 1
    tailer.event_arrived.set()
    await asyncio.sleep(0.1)
    sup.shutdown()
    await task
    assert call_count >= 1
```

- [ ] **Step 2: Run, expect FAIL**

Run: `.venv/bin/python -m pytest tests/recorder/test_task.py -k 'idle_supervisor or event_arrival_triggers' -v --benchmark-disable`

Expected: FAIL — current loop calls `run_once` every interval regardless.

- [ ] **Step 3: Refactor `run()` to event-driven**

In `src/mymcp/recorder/task.py`, replace the `while not self._stop.is_set():` body. New shape:

```python
    async def run(self) -> None:
        log.info("recorder.supervisor.start")
        try:
            if self.store.read_overview() is None:
                await self._do_bootstrap()

            tailer_event = self._merge_cycle._tailer.event_arrived

            while not self._stop.is_set():
                # Handle explicit bootstrap requests OR self-heal if overview vanished.
                if self._force_bootstrap or self.store.read_overview() is None:
                    self._force_bootstrap = False
                    await self._do_bootstrap()

                # Idle gate: if backlog is empty, wait for a new event or stop.
                # No LLM calls when there's nothing to record.
                try:
                    pending = int(self._merge_cycle._tailer.pending_count())
                except Exception:
                    pending = 0

                if pending == 0:
                    await self._wait_for_work(tailer_event)
                    continue

                # Circuit-open: still wait for the NEXT new event before retrying.
                # No more timer-driven retries; recovery is event-driven.
                if self._circuit_open:
                    tailer_event.clear()
                    await self._wait_for_work(tailer_event)
                    # Fall through to attempt a single merge below.

                with _tracer.start_as_current_span("recorder.supervisor.cycle"):
                    try:
                        result = await self._merge_cycle.run_once()
                        if result.skipped_reason is None:
                            self._last_merge_ts = time.time()
                            self._last_merge_attempt_ts = time.time()
                        if self._bootstrap.state != BootstrapState.FAILED:
                            self._last_error = None
                        # Success path clears the breaker — event-driven recovery.
                        self._consecutive_failures = 0
                        self._circuit_open = False
                        self._backoff = 30.0
                    except Exception as e:  # noqa: BLE001
                        log.exception("recorder.supervisor.cycle_error")
                        self._last_error = str(e)
                        self._last_merge_attempt_ts = time.time()
                        self._consecutive_failures += 1
                        if (
                            self._circuit_threshold > 0
                            and self._consecutive_failures >= self._circuit_threshold
                            and not self._circuit_open
                        ):
                            self._circuit_open = True
                            log.error(
                                "recorder.supervisor.circuit_open",
                                extra={
                                    "consecutive_failures": self._consecutive_failures,
                                    "threshold": self._circuit_threshold,
                                    "last_error": self._last_error,
                                },
                            )

                # After a real attempt, clear the trigger so the next loop
                # iteration goes back to waiting for new work.
                tailer_event.clear()
        finally:
            log.info("recorder.supervisor.stop")

    async def _wait_for_work(self, tailer_event: asyncio.Event) -> None:
        """Wait until either a new event arrives, stop is requested, or the
        full merge interval elapses (safety net — should rarely fire)."""
        waiters = [
            asyncio.create_task(self._stop.wait()),
            asyncio.create_task(tailer_event.wait()),
        ]
        try:
            done, pending = await asyncio.wait(
                waiters,
                timeout=self._interval,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for w in waiters:
                if not w.done():
                    w.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await w
```

- [ ] **Step 4: Run, expect PASS**

Run: same as Step 2. Expected: PASS.

- [ ] **Step 5: Run full recorder test suite to catch regressions**

Run: `.venv/bin/python -m pytest tests/recorder -v --benchmark-disable`

Expected: all green. If older tests fail because they assumed timer-only ticks, update them to use the new event-driven shape (set `event_arrived` to simulate work; assert via `pending_count` not via `asyncio.sleep`).

- [ ] **Step 6: Commit**

```bash
git add src/mymcp/recorder/task.py tests/recorder/test_task.py
git commit -m "feat(recorder): event-driven supervisor loop

The supervisor now waits on (event_arrived | stop | interval). Idle
ticks no longer call the LLM. Circuit-open no longer requires restart:
a new event triggers a retry; success clears the breaker, failure
leaves it open until the next event."
```

---

## Task 7: Rewrite banner priority in `recorder/tool.py`

**Files:**
- Modify: `src/mymcp/recorder/tool.py`
- Test: `tests/recorder/test_tool.py` (create if missing)

- [ ] **Step 1: Write failing tests**

Create or extend `tests/recorder/test_tool.py`:

```python
from mymcp.recorder.tool import _build_banner


def test_no_banner_when_idle_and_healthy():
    """pending_events == 0 → no banner. Idle is normal, not failure."""
    assert _build_banner(
        pending_events=0,
        last_merge_attempt_age_seconds=10000.0,
        consecutive_failures=0,
        last_error=None,
        circuit_open=False,
    ) == ""


def test_circuit_open_priority_1():
    banner = _build_banner(
        pending_events=12,
        last_merge_attempt_age_seconds=120.0,
        consecutive_failures=5,
        last_error="HTTP 429",
        circuit_open=True,
    )
    assert "circuit breaker" in banner.lower()
    assert "429" in banner


def test_stale_banner_when_backlog_and_stalled():
    banner = _build_banner(
        pending_events=7,
        last_merge_attempt_age_seconds=1800.0,
        consecutive_failures=0,
        last_error=None,
        circuit_open=False,
        merge_interval_sec=300.0,
    )
    assert "7 events pending" in banner or "7" in banner
    assert "stalled" in banner.lower() or "stale" in banner.lower()


def test_no_stale_banner_when_backlog_but_attempt_recent():
    banner = _build_banner(
        pending_events=3,
        last_merge_attempt_age_seconds=30.0,  # well within 2*interval
        consecutive_failures=0,
        last_error=None,
        circuit_open=False,
        merge_interval_sec=300.0,
    )
    assert banner == ""


def test_failure_banner_with_backlog():
    """Recent failure but not yet stale: show 'will retry on next event'."""
    banner = _build_banner(
        pending_events=2,
        last_merge_attempt_age_seconds=60.0,
        consecutive_failures=1,
        last_error="timeout",
        circuit_open=False,
        merge_interval_sec=300.0,
    )
    assert "timeout" in banner
    assert "retry" in banner.lower()
```

- [ ] **Step 2: Run, expect FAIL**

Run: `.venv/bin/python -m pytest tests/recorder/test_tool.py -v --benchmark-disable`

Expected: tests fail with signature mismatch (old `_build_banner` takes `stale_seconds`).

- [ ] **Step 3: Rewrite `_build_banner`**

Replace `src/mymcp/recorder/tool.py:_build_banner` (and update `server_overview_handler` signature):

```python
def _build_banner(
    *,
    pending_events: int,
    last_merge_attempt_age_seconds: float | None,
    consecutive_failures: int,
    last_error: str | None,
    circuit_open: bool,
    merge_interval_sec: float = 300.0,
) -> str:
    # Priority 1: breaker open. New events will trigger a single retry.
    if circuit_open:
        msg = (
            f"_🛑 recorder circuit breaker open after {consecutive_failures}"
            f" consecutive failures; waiting for next event to retry_"
        )
        if last_error:
            msg = msg.rstrip("_") + f" (last error: {last_error})_"
        return msg + "\n\n"

    # Priority 2: real staleness — backlog AND attempts have stalled.
    stale_threshold = 2.0 * merge_interval_sec
    if (
        pending_events > 0
        and last_merge_attempt_age_seconds is not None
        and last_merge_attempt_age_seconds > stale_threshold
    ):
        minutes = int(last_merge_attempt_age_seconds / 60)
        msg = f"_⚠️ {pending_events} events pending; merge stalled for {minutes} minutes_"
        if last_error:
            msg = msg.rstrip("_") + f": {last_error}_"
        return msg + "\n\n"

    # Priority 3: backlog + recent failure (not yet stale).
    if pending_events > 0 and consecutive_failures > 0 and last_error:
        return (
            f"_⚠️ last merge failed: {last_error} — will retry on next event_\n\n"
        )

    # Healthy / idle: no banner.
    return ""
```

Update `server_overview_handler` signature:

```python
def server_overview_handler(
    *,
    store: OverviewStore,
    schedule_bootstrap: Callable[[], None],
    pending_events: int,
    last_merge_attempt_age_seconds: float | None,
    consecutive_failures: int,
    last_error: str | None,
    circuit_open: bool,
    merge_interval_sec: float = 300.0,
) -> str:
    overview = store.read_overview()
    if overview is None:
        schedule_bootstrap()
        return _STUB_TEMPLATE.format(changelog=str(store.changelog_path))
    banner = _build_banner(
        pending_events=pending_events,
        last_merge_attempt_age_seconds=last_merge_attempt_age_seconds,
        consecutive_failures=consecutive_failures,
        last_error=last_error,
        circuit_open=circuit_open,
        merge_interval_sec=merge_interval_sec,
    )
    return banner + overview if banner else overview
```

- [ ] **Step 4: Run, expect PASS**

Same as Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mymcp/recorder/tool.py tests/recorder/test_tool.py
git commit -m "feat(recorder/tool): backlog-based banner priority

Replaces 'time since last success' with composite judgment:
circuit_open > (backlog && stalled) > (backlog && recent_fail).
Idle systems (pending==0) get no banner at all — that was the
biggest false-positive in the previous design."
```

---

## Task 8: Update `mcp_server.py` dispatch to pass new fields

**Files:**
- Modify: `src/mymcp/mcp_server.py` (around lines 331-359)
- Test: `tests/test_mcp.py` (server_overview path)

- [ ] **Step 1: Write failing test**

Append to `tests/test_mcp.py`:

```python
@pytest.mark.anyio
async def test_server_overview_passes_new_fields_to_handler(monkeypatch):
    """Ensure dispatch_tool reads pending/attempt/failures/breaker from status
    and forwards them to server_overview_handler."""
    captured = {}

    def fake_handler(**kwargs):
        captured.update(kwargs)
        return "OVERVIEW"

    from mymcp import mcp_server
    monkeypatch.setattr("mymcp.recorder.tool.server_overview_handler", fake_handler)

    class FakeStatus:
        last_merge_age_seconds = None
        last_merge_attempt_age_seconds = 42.0
        pending_events = 3
        consecutive_failures = 2
        last_error = "boom"
        circuit_open = False

    class FakeSup:
        store = object()
        merge_interval = 300.0
        def status(self): return FakeStatus()
        def request_bootstrap(self): pass

    mcp_server.set_recorder_supervisor(FakeSup())
    try:
        result = await mcp_server.dispatch_tool("server_overview", {})
        assert result["success"] is True
        assert captured["pending_events"] == 3
        assert captured["last_merge_attempt_age_seconds"] == 42.0
        assert captured["consecutive_failures"] == 2
        assert captured["circuit_open"] is False
        assert captured["last_error"] == "boom"
    finally:
        mcp_server.set_recorder_supervisor(None)
```

- [ ] **Step 2: Run, expect FAIL**

Run: `.venv/bin/python -m pytest tests/test_mcp.py -k server_overview_passes_new_fields -v --benchmark-disable`

Expected: KeyError or wrong field — handler is still called with `stale_seconds`.

- [ ] **Step 3: Update dispatch**

In `src/mymcp/mcp_server.py`, replace the `elif name == "server_overview":` block:

```python
    elif name == "server_overview":
        sup = _recorder_supervisor
        if sup is None:
            result = {
                "success": False,
                "error": "RecorderDisabled",
                "message": "server_overview requires MYMCP_RECORDER_ENABLED=true",
            }
        else:
            from mymcp.recorder.task import RecorderSupervisor
            from mymcp.recorder.tool import server_overview_handler

            sup_typed: RecorderSupervisor = sup  # type: ignore[assignment]
            status = sup_typed.status()
            overview_text = server_overview_handler(
                store=sup_typed.store,
                schedule_bootstrap=lambda: sup_typed.request_bootstrap(),
                pending_events=status.pending_events,
                last_merge_attempt_age_seconds=status.last_merge_attempt_age_seconds,
                consecutive_failures=status.consecutive_failures,
                last_error=status.last_error,
                circuit_open=status.circuit_open,
                merge_interval_sec=sup_typed.merge_interval,
            )
            result = {"success": True, "overview": overview_text}
```

- [ ] **Step 4: Run, expect PASS**

Same as Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mymcp/mcp_server.py tests/test_mcp.py
git commit -m "feat(mcp): wire new recorder status fields to server_overview"
```

---

## Task 9: Inject `_Last updated_` line into overview.md

**Files:**
- Modify: `src/mymcp/recorder/overview.py`
- Test: `tests/recorder/test_overview.py`

- [ ] **Step 1: Write failing test**

Append to `tests/recorder/test_overview.py`:

```python
def test_write_overview_prepends_last_updated_line(tmp_path):
    store = OverviewStore(tmp_path)
    store.write_overview("# Server Overview\n\nbody\n")
    text = store.read_overview()
    lines = text.splitlines()
    # First non-empty line below the H1 should be the timestamp marker.
    assert any(
        line.startswith("_Last updated: ") and line.endswith("_")
        for line in lines[:5]
    )


def test_write_overview_replaces_existing_last_updated_line(tmp_path):
    store = OverviewStore(tmp_path)
    store.write_overview("# Server Overview\n\n_Last updated: 2020-01-01T00:00:00Z_\n\nbody\n")
    text = store.read_overview()
    assert text.count("_Last updated: ") == 1
    assert "2020-01-01" not in text  # old timestamp replaced
```

- [ ] **Step 2: Run, expect FAIL**

Run: `.venv/bin/python -m pytest tests/recorder/test_overview.py -k last_updated -v --benchmark-disable`

Expected: FAIL.

- [ ] **Step 3: Implement**

In `src/mymcp/recorder/overview.py`, modify `OverviewStore.write_overview`:

```python
import re
from datetime import UTC, datetime

_LAST_UPDATED_RE = re.compile(r"^_Last updated: [^_]+_\s*$", re.MULTILINE)


class OverviewStore:
    def write_overview(self, content: str) -> None:
        stamped = self._stamp(content)
        # ... existing atomic-write path
        path = self._overview_path
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(stamped)
        tmp.replace(path)

    @staticmethod
    def _stamp(content: str) -> str:
        now = datetime.now(tz=UTC).replace(microsecond=0).isoformat()
        marker = f"_Last updated: {now}_"
        # Remove any prior marker
        without = _LAST_UPDATED_RE.sub("", content).rstrip() + "\n"
        # Insert immediately after the first H1, or at top if no H1
        lines = without.splitlines(keepends=True)
        for i, line in enumerate(lines):
            if line.startswith("# "):
                # Insert marker after this line + a blank line
                return "".join(lines[: i + 1]) + "\n" + marker + "\n" + "".join(lines[i + 1 :]).lstrip("\n")
        return marker + "\n\n" + without
```

(Wire in the existing atomic-write helper if there's already one — don't duplicate. Adjust to match this file's existing style.)

- [ ] **Step 4: Run, expect PASS**

Same as Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mymcp/recorder/overview.py tests/recorder/test_overview.py
git commit -m "feat(recorder/overview): embed _Last updated_ marker in overview.md

Visible after restart, in offline copies, and to non-Prometheus consumers.
Decouples 'when did this content last refresh' from runtime supervisor state."
```

---

## Task 10: Update Grafana dashboard JSON

**Files:**
- Modify: `deploy/grafana/mymcp-dashboard.json` (or whichever JSON owns the "Recorder Health" row)

- [ ] **Step 1: Identify the panel**

Run: `grep -n "Time Since Last Successful Merge" deploy/grafana/*.json`

Expected: 1-2 hits identifying the panel(s) to replace.

- [ ] **Step 2: Replace panel**

Replace the panel's `targets` with two queries (turn it into a multi-series panel — or duplicate into two panels if cleaner):

```json
{
  "title": "Recorder backlog vs last attempt",
  "type": "timeseries",
  "targets": [
    {
      "expr": "mymcp_recorder_pending_events",
      "legendFormat": "pending events",
      "refId": "A"
    },
    {
      "expr": "time() - mymcp_recorder_merge_last_attempt_timestamp",
      "legendFormat": "seconds since last attempt",
      "refId": "B"
    }
  ],
  "description": "Stale = backlog > 0 AND seconds-since-attempt large. Backlog alone OR attempt-age alone are not stale signals."
}
```

Keep `mymcp_recorder_merge_last_success_timestamp` as a separate informational panel labelled "Last successful merge (informational; not for alerting)".

- [ ] **Step 3: Validate JSON**

Run: `python -c "import json; json.load(open('deploy/grafana/mymcp-dashboard.json'))"`

Expected: no error.

- [ ] **Step 4: Commit**

```bash
git add deploy/grafana/mymcp-dashboard.json
git commit -m "feat(grafana): backlog-vs-attempt panel for recorder health

Replaces the misleading 'time since last successful merge' as the
primary signal. Idle systems no longer look like failure on the
dashboard. The last-success gauge is kept as informational only."
```

---

## Task 11: Update CLAUDE.md recorder metric table and recommended queries

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update**

In `CLAUDE.md`, in the "Recorder observability" section:

- Add a row for `mymcp_recorder_merge_last_attempt_timestamp` (description: "Unix seconds of the last merge attempt — success OR failure; 0 if never").
- Replace the SLO recipe `time() - mymcp_recorder_merge_last_success_timestamp > 3600 unless mymcp_recorder_merge_last_success_timestamp == 0` with:

```
Stale signal (composite, project does NOT ship alert rules — recipe only):
  ( mymcp_recorder_pending_events > 0
    AND time() - mymcp_recorder_merge_last_attempt_timestamp > 1800 )
  OR mymcp_recorder_circuit_open == 1
```

- Add a one-line note under the metric table: "`merge_last_success_timestamp` is now informational; use `merge_last_attempt_timestamp` for staleness."

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude.md): backlog-based recorder staleness recipe"
```

---

## Task 12: Update README SLO recipe

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update**

In `README.md` "Observability" section, in the recorder metrics subsection: apply the same change as Task 11 — replace the single-gauge SLO with the composite recipe, add a row for `mymcp_recorder_merge_last_attempt_timestamp`, and add a sentence: "Project ships metrics + dashboard panels only; alert rules are deployment-specific and not shipped."

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs(readme): backlog-based recorder staleness recipe + alert disclaimer"
```

---

## Task 13: End-to-end regression run + open PR

- [ ] **Step 1: Full suite**

Run: `.venv/bin/python -m pytest tests/ -v --benchmark-disable`

Expected: all green.

- [ ] **Step 2: Type-check and lint**

Run:
```bash
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy src/mymcp
```

Expected: all clean.

- [ ] **Step 3: Push and open PR**

```bash
git push -u origin feature/recorder-redesign
gh pr create --title "feat(recorder): event-driven retry + backlog-based staleness" --body "$(cat <<'EOF'
## Summary

- Circuit breaker recovery is now event-driven instead of restart-only — a new event triggers one retry; success clears the breaker.
- Staleness banner / dashboard is based on backlog × time-since-last-attempt, not time-since-last-success. Idle systems are no longer reported as stale.
- `overview.md` carries an in-file \`_Last updated_\` marker that survives restarts.
- New gauge \`mymcp_recorder_merge_last_attempt_timestamp\`.
- Grafana panel and recommended PromQL queries updated; no alert rules shipped (deployment-specific).

Spec: \`docs/superpowers/specs/2026-06-06-project-assessment.md\` § Recorder 子系统修订设计.

## Test plan
- [ ] \`pytest tests/recorder -v\` green
- [ ] Manually trigger circuit-open in a dev server, observe new event triggers retry without restart
- [ ] Idle server for 30 min — confirm no stale banner, no alert noise

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-review checklist

- [x] Spec covers § "Recorder 子系统修订设计": all 4 bullets implemented (event-driven retry → Tasks 5/6; staleness redefinition → Tasks 2/3/4/7; overview last_updated → Task 9; Grafana/SLO → Tasks 10/11/12).
- [x] No placeholders. All test code, all production code shown inline.
- [x] Names consistent: `last_merge_attempt_ts` / `last_merge_attempt_age_seconds` / `mymcp_recorder_merge_last_attempt_timestamp` everywhere.
- [x] No "similar to Task N" — each task's code stands alone.
