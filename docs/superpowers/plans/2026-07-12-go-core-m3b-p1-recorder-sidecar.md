# Go Core M3b — Phase 1: Recorder Sidecar Entry + Wiring Decouple

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Python recorder runnable as a standalone `mymcp-recorder` sidecar process (its own console entry + systemd unit) and cut its hardest import coupling to the Python core (`mcp_server.get_recorder_supervisor`), so a later phase can delete the Python core cleanly.

**Architecture:** Add `src/mymcp/recorder/__main__.py` — a thin asyncio runner around the existing `build_supervisor()` + `RecorderSupervisor.run()`/`shutdown()`, with signal-driven graceful stop. It serves no HTTP and does not touch the in-process core. Decouple the recorder's Prometheus gauge callbacks from `mcp_server` by having `build_supervisor` publish the active supervisor to a wiring-local reference the callbacks read (works identically for the in-process core and the sidecar). The `/admin/overview/*` HTTP router is not part of the sidecar (bootstrap auto-runs on start; status is in logs + the overview banner), which removes the `admin.py → auth.require_admin` dependency from the sidecar's import graph.

**Tech Stack:** Python 3.11+, asyncio, existing recorder package (`build_supervisor`, `RecorderSupervisor`), setuptools console entry point, systemd unit template. Tests: pytest + `anyio`.

**Spec:** `docs/superpowers/specs/2026-07-04-go-core-rewrite-design.md` — "Packaging and the Recorder Split" (the `mymcp-recorder` console entry + unit).
**Predecessor:** M3a merged as PR #71 (Go core is feature-complete).
**Branch:** `feat/go-core-m3b-recorder-sidecar` off master (create it).
**Scope:** Phase 1 of M3b only — additive + one clean decouple, a normal mergeable PR. It does **not** remove the Python core, restructure `pyproject` dependencies, build wheels, or touch the VPS. Those are later M3b phases (packaging → core removal + v3.0.0 → ucloud cutover), run collaboratively.
**Reference (read when in doubt):** `src/mymcp/recorder/wiring.py`, `src/mymcp/recorder/task.py` (`run`/`shutdown`/`status`), `src/mymcp/server.py:126-154` (how the in-process core mounts the recorder — the pattern the sidecar mirrors), `src/mymcp/deploy/templates/mymcp.service.in`.

---

## Known, Documented Divergences (intentional)

1. **The sidecar serves no HTTP** and does not expose the recorder's `/admin/overview/*` router or a `/metrics` endpoint. Per the spec, the recorder's own metrics are out of scope for M3b ("the sidecar can expose its own `/metrics` later if wanted"). Bootstrap is automatic (`run()` bootstraps when no `overview.md` exists); operators read state from logs and the `server_overview` banner.
2. **Gauge callbacks now read a wiring-local supervisor reference**, not `mcp_server.get_recorder_supervisor()`. For the still-present in-process core this is behavior-preserving (both paths call `build_supervisor`, which publishes the reference); it removes the `wiring → mcp_server` import so the core can later be deleted.

---

## File Map

```
src/mymcp/recorder/
├── wiring.py                     # MODIFY: publish active supervisor; gauges read local ref (drop mcp_server import)
├── __main__.py                   # CREATE: standalone `mymcp-recorder` entry (main + _run)
src/mymcp/deploy/templates/
├── mymcp-recorder.service.in     # CREATE: systemd unit for the sidecar
tests/recorder/
├── test_wiring_gauges.py         # CREATE: gauges read the local ref; no mcp_server import
├── test_sidecar_entry.py         # CREATE: _run drives + stops the supervisor; main gating
pyproject.toml                    # MODIFY: + [project.scripts] mymcp-recorder; package-data for the new template
CHANGELOG.md                      # MODIFY: Unreleased entry
```

---

## Task 0: Branch

- [ ] **Step 1: Create the branch off master**

```bash
cd /home/zhu/repos/mymcp
git checkout master && git pull
git checkout -b feat/go-core-m3b-recorder-sidecar
```

---

## Task 1: Decouple wiring gauges from mcp_server

**Files:** Modify `src/mymcp/recorder/wiring.py`; create `tests/recorder/test_wiring_gauges.py`

Today the four `_observe_*` gauge callbacks import `mymcp.mcp_server.get_recorder_supervisor` to find the running supervisor. Replace that with a module-local `_active_supervisor` that `build_supervisor` sets, and have the callbacks read it. This severs the last `wiring → mcp_server` edge while keeping the in-process core's gauges working (the core also goes through `build_supervisor`).

- [ ] **Step 1: Write the failing test** — create `tests/recorder/test_wiring_gauges.py`:

```python
import mymcp.recorder.wiring as wiring


def test_gauges_read_local_supervisor(monkeypatch):
    class FakeSup:
        circuit_open = True

        class _tailer:
            @staticmethod
            def pending_count():
                return 7

        class _merge_cycle:
            _tailer = _tailer

        last_merge_ts_unix = 111
        last_merge_attempt_ts_unix = 222

    monkeypatch.setattr(wiring, "_active_supervisor", FakeSup())
    assert wiring._observe_circuit_open()[0].value == 1
    assert wiring._observe_pending_events()[0].value == 7
    assert wiring._observe_last_success_ts()[0].value == 111
    assert wiring._observe_last_attempt_ts()[0].value == 222


def test_gauges_zero_when_no_supervisor(monkeypatch):
    monkeypatch.setattr(wiring, "_active_supervisor", None)
    assert wiring._observe_circuit_open()[0].value == 0
    assert wiring._observe_pending_events()[0].value == 0


def test_wiring_does_not_import_mcp_server():
    import inspect

    src = inspect.getsource(wiring)
    assert "mcp_server" not in src, "recorder wiring must not import the Python core"


def test_set_active_supervisor_publishes_reference():
    sentinel = object()
    wiring.set_active_supervisor(sentinel)
    try:
        assert wiring._active_supervisor is sentinel
    finally:
        wiring.set_active_supervisor(None)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/recorder/test_wiring_gauges.py -q`
Expected: FAIL (`set_active_supervisor` undefined; `test_wiring_does_not_import_mcp_server` fails because the source still contains `mcp_server`).

- [ ] **Step 3: Rewrite the coupling in `wiring.py`**

Add, after the imports (top of module, before `build_supervisor`):

```python
# The active supervisor for this process. Set by build_supervisor so the
# Prometheus gauge callbacks below can report its state without importing the
# Python core (mcp_server). The in-process core and the standalone
# mymcp-recorder sidecar both go through build_supervisor.
_active_supervisor: "RecorderSupervisor | None" = None


def set_active_supervisor(sup: "RecorderSupervisor | None") -> None:
    global _active_supervisor
    _active_supervisor = sup
```

At the end of `build_supervisor`, replace `return RecorderSupervisor(...)` with binding then returning:

```python
    supervisor = RecorderSupervisor(
        merge_cycle=merge,
        bootstrapper=bootstrapper,
        merge_interval_sec=settings.recorder_merge_interval_sec,
        provider=settings.recorder_llm_provider,
        model=settings.recorder_llm_model,
        circuit_breaker_threshold=settings.recorder_circuit_breaker_threshold,
        llm_client=client,
    )
    set_active_supervisor(supervisor)
    return supervisor
```

Replace the four `_observe_*` functions with versions that read `_active_supervisor` (no `mcp_server` import):

```python
def _observe_circuit_open() -> list[Observation]:
    sup = _active_supervisor
    if sup is None:
        return [Observation(0)]
    return [Observation(1 if getattr(sup, "circuit_open", False) else 0)]


def _observe_pending_events() -> list[Observation]:
    """Number of mutating audit events queued past the recorder's cursor.

    A growing value means the recorder is falling behind (LLM failing, circuit
    open, slow merges). Useful as both a saturation gauge and an alert source.
    """
    sup = _active_supervisor
    if sup is None:
        return [Observation(0)]
    try:
        merge = getattr(sup, "_merge_cycle", None)
        tailer = getattr(merge, "_tailer", None)
        n = int(tailer.pending_count()) if tailer is not None else 0
    except Exception:
        n = 0
    return [Observation(n)]


def _observe_last_success_ts() -> list[Observation]:
    """Unix seconds of the last successful merge cycle; 0 if never (informational)."""
    sup = _active_supervisor
    if sup is None:
        return [Observation(0)]
    ts = sup.last_merge_ts_unix
    return [Observation(ts if ts is not None else 0)]


def _observe_last_attempt_ts() -> list[Observation]:
    """Unix seconds of the last merge attempt (success OR failure); 0 if never.

    Advances even when the LLM failed, but not on idle ticks. With
    pending_events it is the canonical 'recorder is stuck' signal:

        ( mymcp_recorder_pending_events > 0
          AND time() - mymcp_recorder_merge_last_attempt_timestamp > 1800 )
        OR mymcp_recorder_circuit_open == 1
    """
    sup = _active_supervisor
    if sup is None:
        return [Observation(0)]
    ts = sup.last_merge_attempt_ts_unix
    return [Observation(ts if ts is not None else 0)]
```

Leave the four `register_callback_gauge(...)` calls unchanged.

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/recorder/test_wiring_gauges.py -q`
Expected: PASS.

- [ ] **Step 5: Verify the in-process core still wires the supervisor.** Confirm `src/mymcp/server.py` lifespan calls `build_supervisor(settings)` (it does, line ~137); since `build_supervisor` now also calls `set_active_supervisor`, the core's gauges keep working. No server.py change needed.

Run: `.venv/bin/pytest tests/recorder/ -q`
Expected: PASS (no regressions).

- [ ] **Step 6: Commit**

```bash
git add src/mymcp/recorder/wiring.py tests/recorder/test_wiring_gauges.py
git commit -m "refactor(recorder): gauges read a wiring-local supervisor ref, not mcp_server"
```

---

## Task 2: Standalone `mymcp-recorder` entry

**Files:** Create `src/mymcp/recorder/__main__.py`, `tests/recorder/test_sidecar_entry.py`

A thin asyncio runner: build the supervisor, run it, stop it on SIGTERM/SIGINT. `_run(supervisor, stop)` is factored out so it is testable without real signals.

- [ ] **Step 1: Write the failing test** — create `tests/recorder/test_sidecar_entry.py`:

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/recorder/test_sidecar_entry.py -q`
Expected: FAIL (module `mymcp.recorder.__main__` does not exist).

- [ ] **Step 3: Implement `src/mymcp/recorder/__main__.py`**

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/recorder/test_sidecar_entry.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mymcp/recorder/__main__.py tests/recorder/test_sidecar_entry.py
git commit -m "feat(recorder): standalone mymcp-recorder asyncio entry with graceful stop"
```

---

## Task 3: systemd unit + console entry point

**Files:** Create `src/mymcp/deploy/templates/mymcp-recorder.service.in`; modify `pyproject.toml`; create test in `tests/recorder/test_sidecar_packaging.py`

- [ ] **Step 1: Create the unit template** `src/mymcp/deploy/templates/mymcp-recorder.service.in`:

```ini
[Unit]
Description=MyMCP Recorder (overview sidecar)
After=network.target mymcp.service
Wants=mymcp.service

[Service]
Type=simple
User={service_user}
WorkingDirectory={working_directory}
EnvironmentFile={env_file}
ExecStart={exec_start}
Restart=on-failure
RestartSec=10

# The recorder only tails audit.log and writes the overview directory; it never
# executes tools. NoNewPrivileges is always safe here.
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Add the console entry + package the template** in `pyproject.toml`.

Under `[project.scripts]` (currently only `mymcp = "mymcp.cli:main"`), add:

```toml
mymcp-recorder = "mymcp.recorder.__main__:main"
```

The existing `[tool.setuptools.package-data]` already globs `deploy/templates/*.in`, so the new template ships automatically — no change needed there.

- [ ] **Step 3: Write the packaging test** — create `tests/recorder/test_sidecar_packaging.py`:

```python
from importlib import resources


def test_recorder_service_template_is_packaged_and_formats():
    text = resources.files("mymcp.deploy.templates").joinpath("mymcp-recorder.service.in").read_text()
    rendered = text.format(
        service_user="mymcp",
        working_directory="/etc/mymcp",
        env_file="/etc/mymcp/.env",
        exec_start="/usr/local/bin/mymcp-recorder",
    )
    assert "ExecStart=/usr/local/bin/mymcp-recorder" in rendered
    assert "Description=MyMCP Recorder" in rendered


def test_recorder_entry_point_declared():
    import tomllib

    with open("pyproject.toml", "rb") as f:
        data = tomllib.load(f)
    scripts = data["project"]["scripts"]
    assert scripts["mymcp-recorder"] == "mymcp.recorder.__main__:main"
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/recorder/test_sidecar_packaging.py -q`
Expected: PASS.

- [ ] **Step 5: Verify the entry point installs.** Re-install editable so the new script is generated, then smoke it:

Run:
```bash
.venv/bin/pip install -e ".[dev]" -c requirements-dev.txt >/dev/null
MYMCP_RECORDER_ENABLED=false .venv/bin/mymcp-recorder; echo "exit=$?"
```
Expected: prints the "refusing to start" line and `exit=1` (proves the console script resolves and gates correctly).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/mymcp/deploy/templates/mymcp-recorder.service.in tests/recorder/test_sidecar_packaging.py
git commit -m "feat(recorder): mymcp-recorder console entry + systemd unit template"
```

---

## Task 4: Full test suite + CHANGELOG + PR

- [ ] **Step 1: Run the whole Python suite + lint**

Run:
```bash
.venv/bin/pytest tests/ -q --benchmark-disable
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy src/mymcp
```
Expected: all green. (The Go core is untouched this phase.)

- [ ] **Step 2: CHANGELOG entry** — under `## [Unreleased]` → `### Added`:

```markdown
- Go core M3b (phase 1): `mymcp-recorder` console entry + `mymcp-recorder.service`
  unit — the recorder can now run as a standalone sidecar around
  `build_supervisor()`. Its Prometheus gauges no longer import the Python core
  (`mcp_server`), a step toward retiring the Python core in v3.0.0.
```

- [ ] **Step 3: Push + PR**

```bash
git add CHANGELOG.md
git commit -m "docs: changelog for M3b phase 1 (recorder sidecar entry)"
git push -u origin feat/go-core-m3b-recorder-sidecar
gh pr create --title "Go core M3b (phase 1) — mymcp-recorder sidecar entry + wiring decouple" \
  --body "$(cat <<'EOF'
First phase of **M3b** (spec: docs/superpowers/specs/2026-07-04-go-core-rewrite-design.md — "Packaging and the Recorder Split").

- `mymcp-recorder` console entry: a standalone asyncio runner around the existing `build_supervisor()`, graceful SIGTERM/SIGINT stop, no HTTP.
- `mymcp-recorder.service` systemd unit template.
- Recorder Prometheus gauges now read a wiring-local supervisor reference instead of importing `mymcp.mcp_server` — removing the last `wiring → core` edge so the Python core can be deleted in a later phase.

Additive and behavior-preserving for the in-process core (still mounts the recorder via `build_supervisor`). No dependency restructure, no wheel changes, no VPS impact — those are later M3b phases.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: Confirm CI green** — `test (3.11/3.12/3.13)`, `lint`, `compat-python`, `compat-go`, `go`, `security-audit`, `build`, `mutation-smoke`. (compat/go jobs are unaffected; they must stay green.)

---

## Self-Review

**1. Spec coverage (this phase):** `mymcp-recorder` console entry — Task 2/3. Systemd unit — Task 3. Recorder decoupled from core (the piece severable now) — Task 1. Deferred to later M3b phases (explicitly out of scope here): `[recorder]` extra + zero base deps + platform wheels (packaging phase); removing the Python core + `register_protected_path`/overview protection moving to the Go core + v3.0.0 (core-removal phase); ucloud cutover + RSS verification (ops runbook).

**2. Placeholder scan:** every code step has complete source; the systemd template and pyproject edit are shown in full.

**3. Type consistency:** `set_active_supervisor(RecorderSupervisor | None)` / `_active_supervisor` used consistently in Task 1; `_run(supervisor, stop: asyncio.Event)` and `main() -> int` consistent between Task 2's implementation and its tests; entry point string `mymcp.recorder.__main__:main` matches the module/function created in Task 2 and asserted in Task 3.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-12-go-core-m3b-p1-recorder-sidecar.md`. Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task, two-stage review.

**2. Inline Execution** — execute in this session with checkpoints (how M2/M3a shipped).

Which approach?
