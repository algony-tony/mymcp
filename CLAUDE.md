# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install in editable mode for development (creates a venv first if needed)
pip install -e ".[dev]"

# Run all tests
pytest tests/ -v --benchmark-disable

# Run a single test
pytest tests/test_files.py::test_read_file_basic -v

# Run bats tests for legacy deploy helpers (kept through 2.0.x)
bats tests/test_install.bats tests/test_upgrade.bats tests/test_upgrade_integration.bats

# Start dev server (foreground; prints temp admin+rw tokens to stderr)
mymcp serve

# Start dev server with explicit .env
mymcp serve --env-file ./.env

# Lint and type-check
ruff check . && ruff format --check . && mypy src/mymcp

# Upgrade an installed mymcp (legacy bash flow, kept for 1.x deployments)
sudo /opt/mymcp/deploy/upgrade.sh v1.1.0
```

### Upgrade flow for MCP clients (legacy)

When an AI client invokes `deploy/upgrade.sh` via `bash_execute`, the script
detects the process ancestry and automatically detaches. The client receives
a "started in background" message and should advise the user to reconnect in
~2 minutes. `bash_execute` bypasses path protection by design, so upgrade
runs without interference. The pip-based 2.0 install path replaces this with
`pipx upgrade mymcp && sudo systemctl restart mymcp` (Plan 2/3 work).

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

### Tests

Tests use `pytest` with `anyio` (asyncio backend). Async tests use `@pytest.mark.anyio`. Config is patched via `unittest.mock.patch.multiple("mymcp.config", ...)` in fixtures, or via `monkeypatch.setenv("MYMCP_*")` followed by `mymcp.config.reset_settings_cache()`. No test database or external services needed.
