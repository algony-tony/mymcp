# Go Core Rewrite

**Status:** Draft (awaiting review)
**Date:** 2026-07-04
**Owner:** algony-tony

## Problem

The Python core's memory floor is the interpreter plus the FastAPI/mcp
stack: ~61 MB peak at import, ~35-45 MB steady RSS (measured on the
deployed VPS; see `2026-07-04-recorder-llm-http-clients-design.md`). The
stated long-term target — 10-20 MB RSS on cheap VPSes deployed in numbers
— is unreachable in Python. Phase 1 (dropping the LLM SDKs) bought ~30%;
this is phase 2: rewrite the core in Go.

Feasibility was concluded in the phase-1 spec: full-server rewrite is the
only shape worth doing, Go over Rust (official MCP SDK, single static
binary, goroutines map cleanly onto subprocess management, several times
cheaper to build and maintain than Rust for ~5 MB extra RSS).

## Goals

1. **Drop-in replacement.** Same `MYMCP_*` env vars and `.env` semantics,
   same `tokens.json` format, same audit-log format, same HTTP endpoints
   and MCP tool schemas, same CLI surface. Switching = replacing the
   binary systemd runs; rollback = switching back. Clients and the
   recorder sidecar cannot tell the difference.
2. **RSS ≤ 20 MB** steady-state on the deployed VPS (expect 10-15 MB).
3. **Recorder stays Python**, demoted to an optional sidecar process.
   The inter-process contract is files: core writes `audit.log`, recorder
   tails it and writes `overview.md`, core's `server_overview` tool reads
   that file.
4. **pip install UX preserved.** v3 ships the Go binary inside
   platform-tagged wheels (ruff/uv style); `pipx install/upgrade
   algony-mymcp` keeps working unchanged.
5. **Executable compatibility contract.** A black-box test suite runs
   against both implementations in CI; both green = drop-in proven.

## Non-Goals

- OpenTelemetry tracing/logs/OTLP in the Go core. Production uses only
  Prometheus `/metrics`; the Go core ships native Prometheus metrics with
  identical names. Tracing can be added later if ever needed.
- Rewriting the recorder in Go. LLM/prompt logic gains nothing from Go.
- Long-term dual maintenance of the Python core. After v3.0.0 the Python
  core is retired; the Python package keeps only the recorder.
- Windows/macOS server support. Targets are linux/amd64 and linux/arm64
  (the CLI may incidentally build elsewhere; untested).
- New features. Behavior parity only; improvements come after the switch.

## Architecture

```
mymcp/
├── go/                          # Go core (own go.mod)
│   ├── cmd/mymcp/main.go        # entry: serve/version/token */install-service/doctor
│   └── internal/
│       ├── config/              # MYMCP_* env + .env parsing
│       ├── auth/                # tokens.json store, Bearer middleware, admin API
│       ├── audit/               # JSON-lines writer + size rotation
│       ├── tools/               # the 9 tool implementations
│       ├── transfer/            # one-shot ticket table + PUT/GET endpoints
│       ├── metrics/             # prometheus/client_golang, mymcp_* names
│       └── server/              # HTTP assembly: StreamableHTTPHandler + middleware
├── src/mymcp/                   # Python: slimmed to the recorder sidecar in v3
└── tests/compat/                # black-box compatibility suite (pytest)
```

**Dependencies** (kept deliberately minimal):

- `github.com/modelcontextprotocol/go-sdk` (official, v1.2+) — MCP server,
  `StreamableHTTPHandler` in stateless mode, explicit `jsonschema.Schema`
  tool registration (schemas ported verbatim from `tool_definitions.py`).
- `github.com/prometheus/client_golang` — metrics.
- `gopkg.in/natefinch/lumberjack.v2` — audit-log size rotation.
- Everything else stdlib.

Expected binary ~10-15 MB, steady RSS 10-15 MB.

**Request flow** (mirrors Python): HTTP → recover middleware → auth
middleware (token → role, request-scoped context) → MCP handler →
`callTool` (permission check by READ/WRITE tool sets, dispatch, audit,
metrics) → tool implementation.

## Packaging and the Recorder Split

v3 `algony-mymcp` wheels are platform-tagged (manylinux x86_64, aarch64):

- The `mymcp` console entry resolves to the bundled Go binary (placed in
  the wheel's scripts directory, ruff-style).
- The recorder's Python source stays in the package. Base dependencies
  drop to **zero** — the binary needs nothing. The recorder's runtime
  deps (httpx, anyio, pydantic-settings, python-json-logger) move to the
  `[recorder]` extra, which now genuinely installs something again.
- New `mymcp-recorder` console entry: a standalone asyncio runner around
  the existing `build_supervisor()`, plus an `mymcp-recorder.service`
  systemd template. The in-process FastAPI mounting of the recorder is
  removed with the Python core.
- Wheel assembly happens in release CI: cross-compile the two
  architectures, inject each binary into a wheel with the right platform
  tag (the `ziglang` PyPI package pattern), publish. An sdist is NOT
  published for v3 (there is no meaningful source build via pip).

**File contract between core and sidecar:**

| File | Writer | Reader | Notes |
|---|---|---|---|
| `audit.log` (+ rotations) | Go core | recorder tailer | JSON-lines, keys and rotation behavior identical to today |
| `overview/overview.md` | recorder | Go `server_overview` tool | tool returns file content; "recorder not enabled" text when absent |
| `overview/changelog.md` | recorder | external LLMs via `read_file` | unchanged |
| `cursor.json` | recorder | recorder | unchanged |

The overview directory stays write-protected in the core's protected-path
set. The recorder admin endpoints (`/recorder/*`) move to the sidecar's
own small HTTP listener bound to localhost, or are dropped if unused —
decided in the M3 plan after checking actual usage.

## Behavior-Porting Notes (the hard 20%)

**bash_execute subprocess management.** `exec.Cmd` with
`SysProcAttr{Setsid: true}` (equivalent of `start_new_session=True`);
every spawned process registered in a mutex-guarded in-flight table,
removed on completion. On SIGTERM/SIGINT: TERM each process group
(`kill(-pgid, SIGTERM)`), wait `MYMCP_SHUTDOWN_GRACE_SEC` (default 5),
KILL survivors, then exit. Timeout semantics identical: `exit_code=-1`,
`timed_out=true`, partial output returned, output truncated at
`max_output_bytes` per stream with the same truncation marker.

**grep.** Same strategy: shell out to `rg` when present (same argument
mapping as today), else built-in fallback walking files with Go stdlib
`regexp`. Go's RE2 lacks backreferences/lookaround that Python `re`
allows; the compat suite asserts only the common syntax subset, and the
tool description keeps advertising ripgrep as the quality path.

**config.** `.env` file parsing plus env-var override order aligned with
pydantic-settings: process env beats `.env`; bool parsing accepts the
pydantic set (`true/false/1/0/yes/no/on/off`, case-insensitive); int
overflow/garbage → startup error naming the variable, as today.
`MYMCP_PROTECTED_PATHS` comma-splitting and normalization identical; the
audit dir is always protected.

**audit.** Same JSON keys in the same situations (`ts` RFC3339 UTC,
`token_name`, `role`, `ip`, `tool`, `params`, `result`, optional
`reason/error_code/error_message/duration_ms/output`, `request_id`).
`trace_id`/`span_id` are omitted (no OTel; keys are optional today, so
the recorder's tailer is unaffected). Rotation via lumberjack configured
to match `RotatingFileHandler` semantics (maxBytes/backupCount names →
`MYMCP_AUDIT_MAX_BYTES`/`MYMCP_AUDIT_BACKUP_COUNT`). Write failure →
`mymcp_audit_write_failures_total` +1 and InternalError to the client
(SOC red line preserved).

**metrics.** Native prometheus/client_golang, metric names and label
sets copied verbatim from `observability/instruments.py` so the shipped
Grafana dashboards keep working. Recorder-specific gauges are simply
absent from the core (the sidecar can expose its own `/metrics` later if
wanted — out of scope). `/metrics` keeps Bearer `MYMCP_METRICS_TOKEN`
auth and the disable behavior when the token is empty.

**transfer.** In-memory ticket table (uuid → {path, expiry, max_bytes,
overwrite, used}), single-use enforced atomically, TTL clamped to
`MYMCP_TRANSFER_MAX_TTL_SEC`. PUT/GET endpoints and URL shape identical
(`MYMCP_PUBLIC_BASE_URL` respected). Streaming copy with a hard cap at
`max_bytes`/`MYMCP_TRANSFER_MAX_BYTES`; partial writes to a temp file +
rename, as today.

**error shapes.** Tool-level failures return
`{"success": false, "error": ..., "message": ...}`; bash returns
`{"exit_code": N, "timed_out": bool}`; both patterns feed audit's
error extraction. Unhandled panics → recover middleware → MCP
InternalError + audit entry. `ro` tokens calling write tools → same
permission-denied shape and audit `reason` as today.

**CLI.** Subcommand-for-subcommand port: `serve` (with `--env-file`),
`version`, `token list/add/revoke/rotate-admin/rotate-metrics/
disable-metrics`, `install-service`, `uninstall-service`, `doctor`
(minus its OTel checks). `install-service` renders the same systemd
unit; temp-token printing on `serve` without configured tokens preserved.

## Compatibility Test Suite

`tests/compat/` — pytest + the official Python MCP client, aimed at a
live server via `MYMCP_COMPAT_URL` + `MYMCP_COMPAT_TOKEN(S)`. Purely
black-box. Coverage:

- `tools/list`: all 9 names present; input schemas compared field-by-field
  against golden copies extracted from `tool_definitions.py`.
- Per-tool behavior: happy path, boundary (offsets, limits, truncation),
  error shapes; bash timeout semantics; edit_file uniqueness rule; glob
  mtime ordering; grep the common-regex subset in all three output modes.
- Permissions: ro token × write tool → denied; admin API token CRUD;
  `/metrics` with and without token.
- Audit: after a scripted call sequence, read audit.log and assert line
  keys/values (the recorder-tailer contract).
- Transfer: mint → PUT → verify content → reuse rejected → expiry.

CI runs the suite twice: `compat(python)` and `compat(go)`. Both green is
the merge gate for milestone PRs and the cutover gate for v3.0.0.
Go-internal unit tests (`go test ./...`) cover what black-box can't
(process-group cleanup with real subprocesses, rotation edges, ticket
races).

## Milestones (one spec, three plans)

| Milestone | Contents | Acceptance |
|---|---|---|
| **M1 read-only core** | go.mod skeleton, config, tokens.json auth, StreamableHTTP (stateless), read_file/glob/grep, graceful shutdown, CI (`go test` + lint) | compat read-only subset green against Go; real MCP client works end-to-end |
| **M2 full tool surface + safety** | bash_execute (process groups), write_file/edit_file, protected paths, audit writer, /metrics | full compat suite green except transfer/admin; integration test: the existing Python `EventTailer` consumes Go-written audit.log files correctly (the sidecar runner itself is M3) |
| **M3 finish + cutover** | transfer, admin API, server_overview, CLI parity, binary-wheel pipeline, `mymcp-recorder` entry + unit, docs, ucloud cutover | everything green; ucloud runs Go core at RSS ≤ 20 MB; v3.0.0 released |

Each milestone is its own implementation plan, PR, and review cycle.

## Rollout and Rollback

- v3.0.0 releases when M3's acceptance holds. The Python core is removed
  from the package in the same major bump (breaking change is the point
  of the major version); the recorder and its tests remain.
- ucloud cutover: `pipx upgrade algony-mymcp` (pulls the binary wheel) +
  `systemctl restart mymcp` via the systemd-run detached-timer pattern.
  `.env`, `tokens.json`, audit history, dashboards: untouched.
- Rollback at any point: `pipx install algony-mymcp==2.5.0` + restart.
  All on-disk state is backward compatible by construction (Goal 1).
- v2.x receives security fixes only until v3.0.0 has run stable for one
  month, then is EOL.

## Success Criteria

1. Compat suite green against both implementations in CI.
2. ucloud steady-state RSS ≤ 20 MB (measured a week after cutover, same
   method as the phase-1 baseline: VmRSS + VmSwap).
3. Recorder sidecar produces overview updates from Go-written audit logs
   with no recorder code changes (entry-point/runner work aside).
4. `pipx upgrade` from 2.5.0 to 3.0.0 works on the deployed VPS.
