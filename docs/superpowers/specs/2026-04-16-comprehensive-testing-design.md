# Comprehensive Testing Design for mymcp

**Date**: 2026-04-16
**Status**: Approved

## Overview

Systematically improve the mymcp project's test suite from 91% line coverage / 103 tests to a comprehensive multi-dimensional testing strategy covering: dependency separation, unit test gap filling, branch/condition coverage, integration testing, boundary value analysis, performance benchmarking, load testing, and mutation testing.

## 1. Dependency Separation

**Problem**: Test frameworks (pytest, pytest-asyncio) are in `requirements.txt` alongside production dependencies. End users shouldn't need to install test tools.

**Solution**: Split into two files:

- `requirements.txt` — production only:
  ```
  mcp>=1.0.0
  fastapi>=0.115.0
  uvicorn[standard]>=0.30.0
  python-multipart>=0.0.9
  httpx>=0.27.0
  anyio>=4.0.0
  ```

- `requirements-dev.txt` — development/testing:
  ```
  -r requirements.txt
  pytest>=8.0.0
  pytest-asyncio>=0.23.0
  pytest-cov>=5.0.0
  pytest-benchmark>=4.0.0
  mutmut>=2.4.0
  locust>=2.20.0
  ```

**Updates needed**: CLAUDE.md install commands, README install section, CI workflow.

## 2. Unit Test Gap Filling

**Goal**: Line coverage 97%+

### 2.1 `main.py` (0% → ~95%) — New file: `tests/test_main.py`

Test using `httpx.AsyncClient` + `ASGITransport`:

- `McpAuthMiddleware`:
  - No token on `/mcp` → 401
  - Invalid token on `/mcp` → 401
  - Valid token on `/mcp` → request forwarded, contextvar set correctly
  - Non-`/mcp` path → middleware passes through to FastAPI
- `_validate_token()`:
  - Missing "Bearer " prefix → error response
  - Invalid token → error response
  - Valid token → None error, correct info
- `/health` endpoint → 200 `{"status": "ok"}`
- `lifespan`: starts and shuts down without error

### 2.2 `auth.py` (84% → ~98%) — Extend `tests/test_auth.py` or `tests/test_admin.py`

- `get_store()`: first call creates singleton; `ADMIN_TOKEN=""` raises `RuntimeError`
- `require_auth()`: missing Bearer → 401, invalid token → 401, valid token → returns info dict
- `require_admin()`: missing Bearer → 401, wrong token → 403, correct admin token → passes

### 2.3 `tools/files.py` (78% → ~97%) — Extend `tests/test_files.py`

- `glob_files` exception path (lines 195-196): trigger Exception in glob to hit except branch
- `_grep_rg()` (lines 221-257):
  - All parameter combinations: case_insensitive, context_lines, glob_pattern
  - All output_modes: content, files, count
  - Timeout → returns error
  - Protected path filtering in results
- `_grep_python()` (lines 260-292):
  - Invalid regex → error response
  - Single file search (path is a file)
  - Permission/OS errors during file read → skipped silently
  - files/count/content output modes
  - glob_pattern filtering

### 2.4 `mcp_server.py` (95% → ~99%)

- `list_tools()` (lines 180-182): ro role returns only READ_TOOLS, rw returns ALL_TOOLS
- `call_tool` JSON decode error path (lines 259-260): tool returns non-JSON → result_status = "ok"

## 3. Branch/Condition Coverage

- Enable `--cov-branch` in pytest configuration
- Target: track and report branch coverage alongside line coverage
- Specific branch targets:
  - `config.py`: `_extra.strip()` true/false paths
  - `mcp_server.py`: all `if/elif/else` chains in `call_tool()` result status detection
  - `tools/files.py`: `shutil.which("rg")` true/false paths in `grep_files()`
  - `auth.py`: all early-return paths in `require_auth`/`require_admin`

## 4. Integration Tests — New file: `tests/test_integration.py`

Full request chain via `httpx.AsyncClient` + `ASGITransport(app=app)`:

### 4.1 Auth → Tool → Audit Chain
- Create token → call `/mcp` with `read_file` → verify correct content → verify audit log entry
- Create ro token → call `bash_execute` → verify denied → verify audit records "denied"
- Create rw token → call `write_file` + `edit_file` → verify file changes persisted

### 4.2 Permission Boundaries
- ro token × all READ_TOOLS → all succeed
- ro token × all WRITE_TOOLS → all denied
- rw token × all tools → all succeed
- Invalid token / no token → 401

### 4.3 Protected Path Chain
- Via MCP protocol: read_file/write_file/edit_file/glob/grep targeting protected paths → all blocked

### 4.4 Error Propagation
- Tool raises unexpected exception → InternalError returned → audit records error
- bash_execute timeout → timed_out in response → audit records TimeoutError
- bash_execute non-zero exit → audit records ExitCode:N

## 5. Boundary Value & Exception Analysis — New file: `tests/test_boundary.py`

### 5.1 `bash_execute`
- timeout: 0, negative, >600, exactly 600
- command: empty string, very long command
- working_dir: empty string, nonexistent, file-not-directory
- max_output_bytes: 0, negative, exceeds hard cap

### 5.2 `read_file`
- offset: 0, -1, very large (beyond file lines)
- limit: 0, -1, exactly MAX_LIMIT, exceeds MAX_LIMIT
- file_path: empty string, symlink, binary file, empty file, very long path

### 5.3 `write_file`
- content: empty string, exactly 10MB, exceeds 10MB
- file_path: deeply nested nonexistent dirs, existing directory path, symlink

### 5.4 `edit_file`
- old_string/new_string: empty string, exactly 1MB, exceeds 1MB
- old_string == new_string (no-op replacement)
- replace_all with only one match

### 5.5 `glob_files`
- pattern: empty string, `**/*` (huge match), invalid glob characters
- path: nonexistent directory, file-not-directory

### 5.6 `grep_files`
- pattern: empty string, invalid regex, catastrophic backtracking pattern
- max_results: 0, negative, exceeds GREP_MAX_RESULTS
- context_lines: negative
- output_mode: invalid value

## 6. Performance Testing

### 6.1 Benchmark Tests — `tests/test_benchmark.py`

Using `pytest-benchmark`:

- **read_file**: small file (~10 lines), medium file (~1000 lines), large file with offset/limit
- **write_file**: small file, medium file
- **edit_file**: single replacement, replace_all with multiple matches
- **glob_files**: few matches, many matches
- **grep_files**: small directory search; rg vs python fallback comparison
- **bash_execute**: simple echo command, command with output

Run: `pytest tests/test_benchmark.py --benchmark-only`
Save baseline: `pytest tests/test_benchmark.py --benchmark-save=baseline`

### 6.2 Load Tests — `tests/loadtest/locustfile.py`

Using `locust`, independent from pytest:

- **Auth stress**: concurrent requests with valid/invalid tokens
- **Read concurrency**: multiple users doing read_file, glob, grep simultaneously
- **Write concurrency**: multiple users doing write_file, edit_file (file lock contention)
- **Mixed load**: 70% read + 30% write
- **Health baseline**: high-concurrency `/health` requests

Run: `locust -f tests/loadtest/locustfile.py --host http://localhost:8765`

Requires manually starting the server first.

## 7. Mutation Testing

Using `mutmut`:

- **Target modules**: `auth.py`, `tools/files.py`, `tools/bash.py`, `mcp_server.py`, `audit.py`, `config.py`
- **Exclude**: `main.py` (ASGI layer, covered by integration tests)
- **Configuration**: `pyproject.toml` `[tool.mutmut]` section
- **Target score**: 80%+ mutants killed
- Run: `mutmut run` then `mutmut results`

## 8. Badges & README

### New badges (added to existing badge row):
- **Branch Coverage** — shields.io endpoint badge, data pushed to gist from CI
- **Mutation Score** — shields.io endpoint badge, data pushed to gist from CI

### New README section: `## Performance`
- How to run benchmark tests (`pytest tests/test_benchmark.py --benchmark-only`)
- How to run load tests (`locust -f tests/loadtest/locustfile.py`)
- Reference performance numbers (to be filled after first run)

## 9. CI Updates (`ci.yml`)

- Install from `requirements-dev.txt` instead of `requirements.txt`
- Coverage step: add `--cov-branch`, report both line and branch coverage
- New step: generate branch coverage badge JSON → push to gist
- New job (manual trigger or separate schedule): run mutmut → generate badge JSON → push to gist

## 10. File Summary

| Action | File |
|--------|------|
| Modify | `requirements.txt` (remove test deps) |
| Create | `requirements-dev.txt` |
| Modify | `pytest.ini` (add cov-branch config) |
| Create | `pyproject.toml` (mutmut config) |
| Create | `tests/test_main.py` |
| Modify | `tests/test_auth.py` or `tests/test_admin.py` |
| Modify | `tests/test_files.py` |
| Modify | `tests/test_mcp.py` |
| Create | `tests/test_integration.py` |
| Create | `tests/test_boundary.py` |
| Create | `tests/test_benchmark.py` |
| Create | `tests/loadtest/__init__.py` |
| Create | `tests/loadtest/locustfile.py` |
| Modify | `.github/workflows/ci.yml` |
| Modify | `README.md` (badges + performance section) |
| Modify | `CLAUDE.md` (install commands) |
