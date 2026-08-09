# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

As of v3 the **server is the Go core in `go/`**. The Python package (`src/mymcp`)
is a **recorder-only sidecar**; a base install has zero dependencies.

```bash
# --- Go core (the server) ---
cd go && go build -o /tmp/mymcp ./cmd/mymcp      # build
/tmp/mymcp serve                                  # run (prints temp tokens to stderr)
cd go && go test ./... && go vet ./... && gofmt -l .

# --- Python recorder sidecar + repo tooling ---
# Editable install with dev + recorder deps (dev now includes [recorder] + the
# mcp client for the compat suite). requirements-dev.txt is a pip-compile
# lockfile used as a constraints file; pyproject.toml stays the source of truth.
pip install -e ".[dev]" -c requirements-dev.txt

# Regenerate the lockfile after changing dependencies in pyproject.toml
pip-compile --extra dev --strip-extras \
  --unsafe-package algony-mymcp --unsafe-package pip --unsafe-package setuptools \
  --output-file requirements-dev.txt pyproject.toml

pytest tests/ -v --benchmark-disable             # Python (recorder) tests
ruff check . && ruff format --check . && mypy src/mymcp
mymcp-recorder                                    # run the sidecar (MYMCP_RECORDER_ENABLED=true)

# --- Compat suite (black-box, run against the Go server) ---
cd go && go build -o /tmp/mymcp ./cmd/mymcp
# boot /tmp/mymcp serve with MYMCP_* env, then:
#   MYMCP_COMPAT_URL=http://127.0.0.1:PORT ... pytest tests/compat/ -v

# --- Package + upgrade (platform wheel bundles the Go binary AS `mymcp`) ---
python -m build --wheel -n                        # pure wheel (assembler input)
python scripts/assemble_wheel.py dist/*.whl <go-binary> manylinux2014_x86_64 platform-dist/
pipx upgrade algony-mymcp && sudo systemctl restart mymcp
```

## Architecture

The server is a **Go** binary (`go/`, module `github.com/algony-tony/mymcp/go`)
exposing Linux system tools over MCP Streamable HTTP (stateless) with Bearer
token auth. It ships as a platform wheel where the `mymcp` command IS the Go
binary (`scripts/assemble_wheel.py` injects it into `<name>.data/scripts/`). The
Python package is a **recorder-only sidecar** — zero base deps; recorder
features need the `[recorder]` extra; the sidecar entry is `mymcp-recorder`.

**Request flow (Go):** Client → `httpserver` McpAuth middleware (token
validation) → `mcpserver.callTool` (permission check, dispatch, audit) →
`tools.*` (execution).

### Key files (Go core)

- `go/cmd/mymcp/main.go` — CLI entry: `serve`, `version`, `token list/add/revoke`.
- `go/internal/httpserver/httpserver.go` — HTTP server + `/mcp`, `/metrics`, `/admin/tokens`, `/files/raw`, and the auth/metrics middleware.
- `go/internal/mcpserver/mcpserver.go` — tool definitions, permission enforcement (`readTools`/`writeTools`), the `callTool` choke point (dispatch + audit), tools/list role filter.
- `go/internal/config/config.go` — `MYMCP_*` env + `.env` parsing; `ProtectedPaths()`.
- `go/internal/audit/audit.go` — rotating JSON-lines audit writer (RotatingFileHandler-compatible; consumed unchanged by the Python recorder's `EventTailer`).
- `go/internal/auth/store.go` — JSON file-backed token store (`tokens.json`).
- `go/internal/tools/*.go` — `read_file`, `write_file`, `edit_file`, `glob`, `grep`, `bash`, `transfer`, `overview`.
- `go/internal/fsutil/fsutil.go` + `tools/readfile.go:ProtectedFromConfig` — protected-path checks; the recorder overview dir is **write-only** protected.

### Design patterns

- **Permission model**: `readTools`/`writeTools` sets; `ro` tokens call only read tools, `rw` all. The tools/list result is role-filtered.
- **Protected paths**: the audit log dir + `MYMCP_PROTECTED_PATHS` extras are read+write protected; the recorder overview dir (`<recorder_data_dir>/overview`) is write-only protected (external LLMs read `changelog.md` but cannot write it). `bash_execute` is NOT subject to protected paths — issue `ro` tokens to untrusted clients.
- **SOC red line**: an audit write failure increments `mymcp_audit_write_failures_total` and returns InternalError/500 — never confirm an unauditable mutation.
- **Stateless transport**: each request is independent (no session tracking).

### Python ↔ Go contract

The Python recorder and the Go server share the same `audit.log` format,
`MYMCP_*` env vars, `tokens.json`, and — since the overview staleness signal —
`cursor.json` and the recorder's `MUTATING_TOOLS` set. The Go core reads
`cursor.json` to compute the unconsumed backlog for `server_overview`'s `stale`
flag, so its `{file, inode, offset}` shape and the six-entry mutating-tool set
in `src/mymcp/recorder/events.py` cannot be changed unilaterally — see
`go/internal/tools/recorderstatus.go`. The black-box compat suite
(`tests/compat/`) runs against the Go server and asserts its tool schemas match
the vendored `golden_tools.json` snapshot (frozen from the Python core when the
two were proven byte-identical); the recorder's `EventTailer` consumes the Go
audit log unchanged.

### Optional: llm-recorder

The recorder runs as a **standalone `mymcp-recorder` sidecar process** (install
the `[recorder]` extra; LLM calls go through httpx). When enabled
(`MYMCP_RECORDER_ENABLED=true`) it:

- Consumes successful mutating events from `audit.log` via a persistent cursor.
- Periodically (every `MYMCP_RECORDER_MERGE_INTERVAL_SEC`, default 300s) calls
  an LLM to fold them into `/var/lib/mymcp/recorder/overview/overview.md` and
  append effect-level summaries to `changelog.md`.
- Auto-bootstraps the initial overview via a self-built agent loop using
  internal `bash_probe` / `read_file_probe` tools.

The MCP tool `server_overview` (Go core) returns the current overview by reading
`overview.md` written by the sidecar. The changelog is read by external LLMs via
the `read_file` tool — the Go core write-protects the overview directory so
writes are refused.

`server_overview` returns `last_updated`, `pending_events`, and `stale` alongside
`overview`, and prefixes the body with a warning banner when stale. `stale` is
the conjunction `pending_events > 0 AND last_updated older than 2 ×
MYMCP_RECORDER_MERGE_INTERVAL_SEC` — an idle server with no backlog is never
reported stale.

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
above queries. The `server_overview` banner (Go core) surfaces only the
`stale` condition described above — it has no circuit-breaker or per-attempt
error state to show, since the Go core cannot see the sidecar's in-memory
state; those live in the metrics above instead.

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
