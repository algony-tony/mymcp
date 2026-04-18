# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run all tests
python3 -m pytest tests/ -v --benchmark-disable

# Run a single test
python3 -m pytest tests/test_files.py::test_read_file_basic -v

# Run bats tests for deploy helpers
bats tests/test_install.bats tests/test_upgrade.bats tests/test_upgrade_integration.bats

# Start dev server
python3 main.py

# Install production dependencies
pip install -r requirements.txt

# Install development/test dependencies
pip install -r requirements-dev.txt

# Upgrade an installed mymcp (runs in background by default)
sudo /opt/mymcp/deploy/upgrade.sh v1.1.0
```

### Upgrade flow for MCP clients

When an AI client invokes `deploy/upgrade.sh` via `bash_execute`, the script
detects the process ancestry and automatically detaches. The client receives
a "started in background" message and should advise the user to reconnect in
~2 minutes. `bash_execute` bypasses path protection by design, so upgrade
runs without interference.

## Architecture

Python MCP server exposing Linux system tools over Streamable HTTP (stateless mode). FastAPI app with Bearer token auth, served by uvicorn.

**Request flow:** Client → `main.py` McpAuthMiddleware (token validation, sets contextvar) → `mcp_server.py` call_tool (permission check, dispatch, audit) → `tools/*.py` (actual execution)

### Key files

- `main.py` — FastAPI app, ASGI auth middleware, lifespan. Logging configured here (basicConfig to stderr for journald).
- `mcp_server.py` — MCP Server with tool definitions, permission enforcement, tool dispatch, error handling, and audit logging. `call_tool()` is the central handler: checks permissions, catches exceptions (including unhandled), extracts error details for audit.
- `config.py` — All configuration via `MCP_*` environment variables with defaults. Tool limits, audit settings, protected paths.
- `audit.py` — Rotating file audit logger. Entries include `error_code`/`error_message` on failures.
- `auth.py` — TokenStore (JSON file-backed), admin API router, FastAPI dependencies.
- `tools/files.py` — read_file, write_file, edit_file, glob_files, grep_files. All file tools check `check_protected_path()` before access.
- `tools/bash.py` — run_bash_execute with timeout and output truncation.

### Design patterns

- **Contextvar for auth info**: `_current_audit_info` is set by middleware, read by tool handlers — no parameter threading needed.
- **Permission model**: Tools are split into `READ_TOOLS` and `WRITE_TOOLS` sets. `ro` tokens can only call read tools; `rw` can call all.
- **Protected paths**: `APP_DIR` and `AUDIT_LOG_DIR` are always protected. File tools filter these out; `bash_execute` is NOT protected (use `ro` tokens for untrusted clients).
- **Error handling**: `dispatch_tool` is wrapped in try/except. Tool-level errors return `{"success": False, "error": "...", "message": "..."}`. bash_execute returns `{"exit_code": N, "timed_out": bool}` instead — both patterns are detected in `call_tool()` for audit logging.
- **Stateless transport**: `StreamableHTTPSessionManager(stateless=True)` — no session tracking, each request is independent.

### Tests

Tests use `pytest` with `anyio` (asyncio backend). Async tests use `@pytest.mark.anyio`. Config is patched via `unittest.mock.patch.multiple("config", ...)` in fixtures. No test database or external services needed.
