# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install in editable mode for development (creates a venv first if needed).
# requirements-dev.txt is a pip-compile lockfile used as a constraints file so
# local + CI installs match exactly; pyproject.toml stays the source of truth.
pip install -e ".[dev]" -c requirements-dev.txt

# Regenerate the lockfile after changing dependencies in pyproject.toml
pip-compile --extra dev --strip-extras \
  --unsafe-package algony-mymcp --unsafe-package pip --unsafe-package setuptools \
  --output-file requirements-dev.txt pyproject.toml

# Run all tests
pytest tests/ -v --benchmark-disable

# Run a single test
pytest tests/test_files.py::test_read_file_basic -v

# Start dev server (foreground; prints temp admin+rw tokens to stderr)
mymcp serve

# Start dev server with explicit .env
mymcp serve --env-file ./.env

# Lint and type-check
ruff check . && ruff format --check . && mypy src/mymcp

# Upgrade an installed mymcp
pipx upgrade algony-mymcp && sudo systemctl restart mymcp
```

## Architecture

Python MCP server exposing Linux system tools over Streamable HTTP (stateless mode). FastAPI app with Bearer token auth, served by uvicorn.

**Request flow:** Client → `mymcp.server` McpAuthMiddleware (token validation, sets contextvar) → `mymcp.mcp_server` call_tool (permission check, dispatch, audit) → `mymcp.tools.*` (actual execution)

### Key files

- `src/mymcp/cli.py` — argparse entry, logging configuration, signal handlers
- `src/mymcp/server.py` — FastAPI app factory (`create_app()`), middlewares, routes; no module-level side effects
- `src/mymcp/mcp_server.py` — MCP Server with tool definitions, permission enforcement, dispatch, error handling, and audit logging. `call_tool()` is the central handler: checks permissions, catches exceptions (including unhandled), extracts error details for audit.
- `src/mymcp/config.py` — pydantic-settings `Settings`; reads `MYMCP_*` env vars + optional .env file. `get_settings()` returns a cached singleton; `reset_settings_cache()` is a test helper.
- `src/mymcp/audit.py` — Rotating file audit logger. Entries include `error_code`/`error_message` on failures.
- `src/mymcp/auth.py` — TokenStore (JSON file-backed), admin API router, FastAPI dependencies.
- `src/mymcp/tools/files.py` — read_file, write_file, edit_file, glob_files, grep_files. All file tools check `check_protected_path()` before access.
- `src/mymcp/tools/bash.py` — `run_bash_execute` with timeout, output truncation, and SIGTERM-safe subprocess tracking via `_track_process` / `shutdown_inflight_processes`.

### Design patterns

- **Contextvar for auth info**: `_current_audit_info` is set by middleware, read by tool handlers — no parameter threading needed.
- **Permission model**: Tools are split into `READ_TOOLS` and `WRITE_TOOLS` sets. `ro` tokens can only call read tools; `rw` can call all.
- **Protected paths**: The audit log dir is always protected, plus any `MYMCP_PROTECTED_PATHS` extras. File tools filter these out; `bash_execute` is NOT protected (use `ro` tokens for untrusted clients).
- **Error handling**: `dispatch_tool` is wrapped in try/except. Tool-level errors return `{"success": False, "error": "...", "message": "..."}`. bash_execute returns `{"exit_code": N, "timed_out": bool}` instead — both patterns are detected in `call_tool()` for audit logging.
- **Stateless transport**: `StreamableHTTPSessionManager(stateless=True)` — no session tracking, each request is independent.
- **Subprocess cleanup**: bash_execute spawns children with `start_new_session=True` and tracks them in a thread-safe weakref set. The CLI installs SIGTERM/SIGINT handlers that call `shutdown_inflight_processes()` to TERM/KILL the process group with a configurable grace period (`MYMCP_SHUTDOWN_GRACE_SEC`).

### Optional: llm-recorder

When installed (`pip install algony-mymcp[recorder]`, or `[recorder-anthropic]` /
`[recorder-openai]` for a single provider) and enabled
(`MYMCP_RECORDER_ENABLED=true`), `mymcp.recorder` runs an asyncio background
task that:

- Consumes successful mutating events from `audit.log` via a persistent cursor.
- Periodically (every `MYMCP_RECORDER_MERGE_INTERVAL_SEC`, default 300s) calls
  an LLM to fold them into `/var/lib/mymcp/recorder/overview/overview.md` and
  append effect-level summaries to `changelog.md`.
- Auto-bootstraps the initial overview via a self-built agent loop using
  internal `bash_probe` / `read_file_probe` tools.

The MCP tool `server_overview` returns the current overview. The changelog is
read by external LLMs via the existing `read_file` tool — the overview
directory is registered as write-protected so writes are refused.

LLM provider is `MYMCP_RECORDER_LLM_PROVIDER ∈ {anthropic, openai}`. The
OpenAI adapter supports OpenAI-compatible endpoints via
`MYMCP_RECORDER_LLM_BASE_URL` (e.g. DeepSeek).

**Recorder observability** (all surfaced through `/metrics`):

- `mymcp_recorder_merge_cycles_total{reason}` — one outcome per cycle. Reason ∈
  `success / no_events / bootstrap_required / llm_error / max_tokens / empty /
  unparseable / schema_invalid / apply_error`. The success ratio query is
  `rate(...{reason="success"}) / rate(...{reason!~"no_events|bootstrap_required"})`.
- `mymcp_recorder_merge_duration_seconds` — histogram of wall-clock per cycle,
  labelled with the same `reason`. Use `histogram_quantile(0.95, …)` for p95.
- `mymcp_recorder_llm_calls_total{phase,result}` — phase is `merge` or
  `bootstrap`; result is `success` or `http_error`. Only the HTTP layer here;
  response-quality failures are accounted via `merge_cycles{reason=…}`.
- `mymcp_recorder_llm_tokens_total{phase,direction}` — direction ∈
  `input / output`.
- `mymcp_recorder_pending_events` — gauge (callback in `wiring.py`) backed by
  `EventTailer.pending_count()`; the unconsumed mutating-event backlog.
- `mymcp_recorder_merge_last_attempt_timestamp` — gauge in Unix seconds of the
  last merge attempt (success OR failure); `0` means "never". Does NOT advance
  on idle ticks. Together with `pending_events` this is the canonical "stuck"
  signal.
- `mymcp_recorder_merge_last_success_timestamp` — gauge in Unix seconds; `0`
  means "never". **Informational only** (kept for health trending); previously
  the SLO alert source, now superseded by the composite below.
- `mymcp_recorder_circuit_open` — gauge, 1 when the breaker has tripped.
  Recovery is event-driven: the next mutating audit event past the high-water
  mark triggers one retry; success clears the breaker. No restart required.

Recommended stale-recorder PromQL (project ships no alert rules — recipe only):

```
( mymcp_recorder_pending_events > 0
  AND time() - mymcp_recorder_merge_last_attempt_timestamp > 1800 )
OR mymcp_recorder_circuit_open == 1
```

The two terms together avoid the historical false positive where an idle
server (no events to process) appeared "stale" because `last_success_timestamp`
hadn't moved.

The `recorder.supervisor.cycle` span wraps each tick so the
`recorder.supervisor.cycle_error` log line carries the merge_cycle's
`trace_id`/`span_id` for Loki↔Tempo correlation.

Dashboards (`deploy/grafana/`) include a **Recorder Health** row with the
above queries. The `server_overview` banner surfaces circuit/stale/error
state in priority order.

Spec: `docs/superpowers/specs/2026-05-29-llm-recorder-design.md`.
Plan: `docs/superpowers/plans/2026-05-29-llm-recorder.md`.

### Audit log integrity

- `mymcp_audit_write_failures_total` — counter, incremented whenever the
  audit log writer fails (disk full, permission denied, rotation race).
  Tool calls in this state return `InternalError` to the client. Silent
  audit loss is a SOC red line.

The project ships an `Audit Log Integrity` row in the Grafana dashboard
(cumulative + rate panels). It does NOT ship alert rules — recommended
PromQL for operators:

```
rate(mymcp_audit_write_failures_total[5m]) > 0
```

### Tests

Tests use `pytest` with `anyio` (asyncio backend). Async tests use `@pytest.mark.anyio`. Config is patched via `unittest.mock.patch.multiple("mymcp.config", ...)` in fixtures, or via `monkeypatch.setenv("MYMCP_*")` followed by `mymcp.config.reset_settings_cache()`. No test database or external services needed.
