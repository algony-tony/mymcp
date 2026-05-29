# llm-recorder: Server Overview Module

**Status:** Draft (awaiting review)
**Date:** 2026-05-29
**Owner:** algony-tony

## Problem

Today every interaction with mymcp leaves the server in a slightly-changed state (packages installed, files written, services configured), but there is no living record of *what this server has become*. Each new conversation forces the external LLM to rediscover the server from scratch via `bash_execute` probes — wasteful, slow, and unreliable (it can only see the present, not the *why*).

We want a maintained "server overview" document that:

- Acts as a **progressive-disclosure map**, not an operation manual
- Captures the important shape of the server (services, apps, network, data, quirks), not every config detail
- Stays in sync automatically as mymcp tools mutate the system
- Costs no extra latency on the user-facing tool path

## Goals & Non-Goals

**Goals**

1. Maintain `overview.md` summarising the server's important state.
2. Maintain `changelog.md` recording effect-level summaries of all mutating tool calls.
3. Update both automatically and asynchronously after successful mutating tool calls.
4. Expose the overview to external MCP clients via a single, clearly-scoped tool.
5. Survive process restarts without losing events.
6. Integrate with mymcp's existing observability (metrics, logs, traces).
7. Ship as an **optional** install — mymcp without the recorder works exactly as today.
8. Support multiple LLM providers (Anthropic-style and OpenAI-style).

**Non-Goals**

- Replacing audit log. Audit remains the compliance record; recorder is a state summary.
- Real-time per-event updates. Merge cadence is on the order of minutes, not seconds.
- Recording configuration details (config files, env var values, version pins). Those stay queryable via `read_file`/`bash_execute` on demand.
- A general "AI agent" framework. The agent loop is purpose-built for system probing.
- Multi-host or fleet overview. One mymcp instance documents one host.

## Architecture

```
┌────────────────────────── mymcp process ──────────────────────────┐
│                                                                    │
│  MCP tool call ──► call_tool() ──► audit.log_tool_call() ──► audit.log
│                                                       │            │
│                                                       │ (rich entry│
│                                                       │  with T1   │
│                                                       │  truncated │
│                                                       │  output)   │
│                                                       ▼            │
│   ┌──── recorder background task (asyncio) ─────────────────────┐  │
│   │                                                              │  │
│   │  • read audit.log from cursor                                │  │
│   │  • filter mutating successful events                         │  │
│   │  • on first run / missing overview ──► schedule bootstrap    │  │
│   │  • normal cycle:                                             │  │
│   │       LLM merge (events + current overview + recent log)    │  │
│   │       → write overview.md + append changelog.md             │  │
│   │  • bootstrap cycle:                                          │  │
│   │       agent loop (LLM + bash/read probe tools)              │  │
│   │       → write first overview.md, seed changelog.md          │  │
│   │  • emit metrics / spans / logs throughout                    │  │
│   └──────────────────────────────────────────────────────────────┘  │
│                                                                    │
│  server_overview()  ──► reads overview.md (write-protected)        │
│  read_file(changelog.md) ──► allowed (read-protected paths permit  │
│                              read but block write)                 │
│  POST /admin/overview/bootstrap ──► force re-bootstrap             │
│  GET  /admin/overview/status   ──► state, lag, last error          │
└────────────────────────────────────────────────────────────────────┘
```

## Components

### `mymcp.recorder.events` — Event source

Reads `audit.log` (and its rotated siblings) from a persistent cursor file `/var/lib/mymcp/recorder/cursor.json`:

```json
{ "file": "audit.log", "inode": 1234567, "offset": 84219 }
```

- On startup, reopen by inode. If inode changed (rotation happened), scan rotated files numerically (`audit.log.1` → `audit.log`), resume from the matching offset.
- If the cursor's recorded inode no longer exists anywhere (rotation pushed it out), log a warning, increment `mymcp_recorder_event_loss_total`, and resume from current `audit.log` head.
- Filters: only `result == "success"` and `tool ∈ {bash_execute, write_file, edit_file, transfer_*}`. Read-only tools (`read_file`, `glob_files`, `grep_files`, `server_overview`) are skipped.

### `mymcp.audit` — Audit log enrichment (T1 truncation)

Adds optional `output` field to audit entries, tool-specific shape:

| Tool | `output` shape |
|---|---|
| `bash_execute` | `{stdout_head, stdout_tail, stdout_truncated_bytes, stdout_sha256, exit_code, timed_out}` — head/tail capped at 4 KB each. stderr handled similarly. |
| `write_file` | `{path, size_bytes, sha256, first_line}` — never includes content |
| `edit_file` | `{path, lines_added, lines_removed, hunk_count}` |
| `transfer_upload` / `transfer_download` | `{path, size_bytes, sha256, direction}` |
| others | omitted |

Truncation thresholds configurable via `MYMCP_AUDIT_OUTPUT_*_BYTES` envs. The change is additive — existing audit consumers that ignore unknown fields keep working.

### `mymcp.recorder.protected_paths` — Read/write distinction (P2)

Refactor `check_protected_path(path)` → `check_protected_path(path, mode: Literal["read","write"])`:

- Existing protected paths (audit dir, `MYMCP_PROTECTED_PATHS`) default to **both** read and write blocked (no behaviour change).
- Recorder registers `/var/lib/mymcp/recorder/overview/` as **write-only protected** — `read_file` works, `write_file`/`edit_file` raises permission error.
- Implementation: protected paths become a list of `(pattern, modes)` tuples instead of a flat set.

### `mymcp.recorder.llm` — Provider-agnostic LLM client

```python
class LLMClient(Protocol):
    async def call(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        max_tokens: int,
    ) -> LLMResponse: ...
```

Internal types `Message`, `ToolUse`, `ToolResult`, `LLMResponse` are recorder-owned dataclasses. Vendor SDK objects never leak past the client adapter.

Two adapters, each lazy-imports its SDK:

- `AnthropicClient` — wraps `anthropic` SDK; uses `messages.create` with `tools` parameter and `tool_use`/`tool_result` content blocks.
- `OpenAIClient` — wraps `openai` SDK; uses `chat.completions` with `tool_calls`/`tool` role messages. Compatible with `MYMCP_RECORDER_LLM_BASE_URL` for self-hosted endpoints.

Selected at startup via `MYMCP_RECORDER_LLM_PROVIDER ∈ {anthropic, openai}`. Misconfiguration (provider configured but extra not installed) raises a clear `RuntimeError` with the install command.

The abstraction is intentionally minimal: text + tool-use + token usage. No streaming, no vision, no prompt caching, no batch — YAGNI.

### `mymcp.recorder.merge_cycle` — Normal merge loop

Runs every `MYMCP_RECORDER_MERGE_INTERVAL_SEC` (default 300):

1. Drain unread events from `events` (capped per cycle at `MAX_EVENTS_PER_CYCLE`, default 50).
2. If no events: skip, no LLM call.
3. If `overview.md` missing or bootstrap state ∉ `{succeeded}`: skip and ensure bootstrap is scheduled.
4. Build prompt:
   - System: "You maintain a server overview map. Update only what these events affect. Keep it bounded. Prefer effect over command. ..." (full prompt in implementation plan)
   - User: current `overview.md` + last 10 changelog lines + the new events (truncated outputs included).
5. LLM call (no tools — pure synthesis). Structured JSON output:
   ```json
   {
     "new_changelog_lines": ["2026-05-28 10:20 | installed nginx and postgres 16", ...],
     "updated_overview_md": "..."
   }
   ```
6. Atomic write: write to `overview.md.tmp` then `os.replace`. Append `new_changelog_lines` to `changelog.md` (line-buffered).
7. Advance cursor only after both writes succeed.

### `mymcp.recorder.bootstrap` — Initial scan via agent loop

Triggered by:

- recorder startup detecting missing `overview.md`
- `server_overview()` called with missing `overview.md` (fire-and-forget; the call still returns the stub immediately)
- `POST /admin/overview/bootstrap` (force; rebuilds even if `overview.md` exists)

Concurrency: a single `asyncio.Lock` plus `bootstrap_state` field. Repeat triggers while one is running are coalesced (no-op).

Agent loop:

```python
messages = [{"role": "user", "content": INITIAL_PROBE_PROMPT}]
tools = [bash_probe_tool, read_file_probe_tool]
tokens_spent = 0
for i in range(MAX_ITERATIONS):  # default 60
    resp = await client.call(system=BOOTSTRAP_SYSTEM_PROMPT, messages=messages,
                              tools=tools, max_tokens=4096)
    tokens_spent += resp.usage_total
    if tokens_spent > TOKEN_BUDGET:  # default 1_000_000
        raise BootstrapBudgetExceeded
    if resp.stop_reason == "end_turn":
        return parse_final_overview(resp)
    for tu in resp.tool_uses:
        result = await dispatch_probe(tu)  # bash with timeout, or read_file
        messages.append(tool_result_message(tu.id, result))
```

The probe tools are **internal to recorder**:

- `bash_probe(command, timeout_sec)` — runs as the mymcp service user (non-root, same as main service). Output truncated like T1. Hard timeout 30 s default. Every call logged to `/var/lib/mymcp/recorder/bootstrap-trace.log` and recorded as a span.
- `read_file_probe(path)` — read-only file access, no protected-path check (recorder runs in-process and is trusted; the LLM's writes go nowhere — it can only call these two probes).

The bootstrap prompt steers the LLM toward broad directions without prescribing commands (so it adapts to Linux variant):

> "Build an initial overview of this Linux host. Probe systematically: OS/distro, running services, deployed applications, listening network ports, important data directories, unusual configurations. Use `bash_probe` and `read_file_probe` freely. Don't enumerate exhaustively — prefer the load-bearing facts. Output the final overview as a single Markdown document matching the section skeleton."

Final output goes through the same atomic write as merge_cycle. `changelog.md` is seeded with one line: `YYYY-MM-DD HH:MM | bootstrap | initial overview generated`.

### `mymcp.recorder.task` — asyncio supervisor

Started from `create_app()` lifespan (only when `MYMCP_RECORDER_ENABLED=true`):

- Owns merge_cycle ticking and bootstrap scheduling
- Survives transient LLM failures with exponential backoff (E1 mechanics, E2 surfacing)
- Cancellation-safe: on shutdown, cancel pending sleep, finish current cycle if mid-LLM-call (with hard deadline), persist cursor

### MCP tool: `server_overview`

Schema:
```python
{
  "name": "server_overview",
  "description": "Return a maintained map of this server's services, apps, data, and recent changes.",
  "inputSchema": { "type": "object", "properties": {}, "additionalProperties": false }
}
```

- Read-only tool (added to `READ_TOOLS`).
- Returns the contents of `overview.md`.
- If overview missing: returns stub with `_⚠️ Overview not initialized. Bootstrap scheduled in the background._`, and schedules bootstrap (idempotent).
- If overview present but `last_merge_age_seconds > 2 × MERGE_INTERVAL`: prepends a `_⚠️ overview is N minutes stale: <reason>_` banner read from status.

### Admin endpoints

- `POST /admin/overview/bootstrap` (admin token) — force re-bootstrap. Returns immediately with `{state: "scheduled"|"running", run_id}`.
- `GET /admin/overview/status` (admin token) — returns:
  ```json
  {
    "enabled": true,
    "bootstrap_state": "succeeded",
    "last_bootstrap_ts": "2026-05-28T09:00:00Z",
    "last_merge_ts": "2026-05-29T14:25:00Z",
    "last_merge_age_seconds": 180,
    "pending_events": 3,
    "last_error": null,
    "llm_provider": "anthropic",
    "llm_model": "claude-sonnet-4-6"
  }
  ```

## Document format

`/var/lib/mymcp/recorder/overview/overview.md` — single bounded markdown, sections fixed:

```markdown
# Server Overview
_Last updated: {ts} by mymcp-recorder ({provider}/{model})_
_Hostname: {h} | OS: {os} | Bootstrap: {bootstrap_ts}_

## TL;DR
{2–3 sentence summary of what this server is and does.}

## Installed Services
- `unit.service` — one-line purpose, key path(s)

## Deployed Applications
- `name` — runtime, install location, how started

## Network
- `:port` — process / purpose

## Data Locations
- `/path` — what lives here, owner process

## Recent Changes
{Last 10 effect-level lines, newest first.}
_Full changelog: /var/lib/mymcp/recorder/overview/changelog.md (use read_file)_

## Known Quirks
{Non-standard configs, workarounds, "be careful" notes.}
```

`/var/lib/mymcp/recorder/overview/changelog.md` — append-only, one line per change:

```
2026-05-28 10:20 | bash_execute | installed nginx and postgres 16
2026-05-28 10:23 | write_file   | wrote /etc/nginx/sites-available/myapp.conf
2026-05-28 10:24 | bash_execute | reloaded nginx
```

Format: `YYYY-MM-DD HH:MM | <tool> | <effect-level summary, ≤120 chars>`. Tool name is included so humans grepping can correlate to audit.

## Failure handling (E2)

| Failure | Behaviour |
|---|---|
| LLM call fails (network, 5xx) | Exponential backoff: 30s, 60s, 120s, 240s, cap 600s. Cursor unchanged. `last_error` set. Metric incremented. |
| LLM call succeeds but output unparseable | Treat as failure, same backoff. Log the raw response (one-time per cycle) for debugging. |
| Bootstrap budget exceeded | bootstrap_state → `failed`, `last_error` populated. No retry until next explicit trigger or `MYMCP_RECORDER_BOOTSTRAP_RETRY_INTERVAL_SEC` elapses (default 3600). |
| Atomic write fails (disk full) | Cursor unchanged. Fatal-log. Recorder keeps trying. |
| Audit log rotated past cursor | Increment `event_loss_total`, resume from current head, log a warning. |
| Provider misconfigured | Recorder fails to start with clear error. Main mymcp service unaffected. |

When stale (`last_merge_age_seconds > 2 × interval`) or `last_error` set, `server_overview()` prepends a banner so the external LLM is aware.

## Observability

Reuses `mymcp.observability` (already configured for OTel).

**Metrics** (Prometheus-exposed):

| Name | Type | Labels |
|---|---|---|
| `mymcp_recorder_events_consumed_total` | counter | `tool` |
| `mymcp_recorder_merge_cycles_total` | counter | `result` |
| `mymcp_recorder_bootstrap_runs_total` | counter | `result` |
| `mymcp_recorder_llm_calls_total` | counter | `provider`, `model`, `phase`, `result` |
| `mymcp_recorder_llm_tokens_total` | counter | `provider`, `phase`, `direction` |
| `mymcp_recorder_bash_probe_runs_total` | counter | `result` |
| `mymcp_recorder_event_loss_total` | counter | `reason` |
| `mymcp_recorder_pending_events` | gauge | – |
| `mymcp_recorder_last_merge_age_seconds` | gauge | – |
| `mymcp_recorder_bootstrap_state` | gauge | – (0=idle, 1=scheduled, 2=running, 3=succeeded, 4=failed) |

**Logs**: structured JSON on logger `mymcp.recorder`. Each cycle gets a `cycle_id`, each bootstrap a `bootstrap_run_id`. Standard fields plus `tool`, `events_in`, `tokens_in`, `tokens_out`, `duration_ms`.

**Traces**: each merge cycle is a root span (`recorder.merge_cycle`) with attributes `events_in`, `tokens_total`. Each bootstrap is a root span (`recorder.bootstrap`) with child spans `recorder.agent_iteration` containing child spans `recorder.llm_call` and `recorder.bash_probe`. Trace propagation into the `audit.log` entries written *during* bootstrap follows the existing trace contextvar.

## Configuration

All under `MYMCP_RECORDER_*`, all optional, all read by `pydantic-settings`:

| Env var | Default | Notes |
|---|---|---|
| `MYMCP_RECORDER_ENABLED` | `false` | Master switch. |
| `MYMCP_RECORDER_DATA_DIR` | `/var/lib/mymcp/recorder` | Holds `overview/`, `cursor.json`, `bootstrap-trace.log`. |
| `MYMCP_RECORDER_MERGE_INTERVAL_SEC` | `300` | |
| `MYMCP_RECORDER_MAX_EVENTS_PER_CYCLE` | `50` | |
| `MYMCP_RECORDER_BOOTSTRAP_MAX_ITERATIONS` | `60` | Generous default — bootstrap rarely runs and must be allowed to finish. |
| `MYMCP_RECORDER_BOOTSTRAP_TOKEN_BUDGET` | `1000000` | Generous default — guards against runaway loops, not against normal use. |
| `MYMCP_RECORDER_BOOTSTRAP_PROBE_TIMEOUT_SEC` | `30` | Per bash_probe call. |
| `MYMCP_RECORDER_BOOTSTRAP_RETRY_INTERVAL_SEC` | `3600` | After failed bootstrap. |
| `MYMCP_RECORDER_LLM_PROVIDER` | `anthropic` | `anthropic` or `openai`. |
| `MYMCP_RECORDER_LLM_MODEL` | per-provider default | `claude-sonnet-4-6` / `gpt-4o`. |
| `MYMCP_RECORDER_LLM_API_KEY` | falls back to `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | |
| `MYMCP_RECORDER_LLM_BASE_URL` | unset | OpenAI-compatible endpoint override. |
| `MYMCP_AUDIT_OUTPUT_BASH_HEAD_BYTES` | `4096` | T1 truncation knob. |
| `MYMCP_AUDIT_OUTPUT_BASH_TAIL_BYTES` | `4096` | |

## Packaging

`pyproject.toml` extras:

```toml
[project.optional-dependencies]
recorder-anthropic = ["anthropic>=0.40"]
recorder-openai    = ["openai>=1.40"]
recorder           = ["algony-mymcp[recorder-anthropic,recorder-openai]"]
```

Install paths:

- `pip install algony-mymcp[recorder-anthropic]` — Anthropic-only recorder
- `pip install algony-mymcp[recorder-openai]` — OpenAI-only recorder
- `pip install algony-mymcp[recorder]` — both

Without any recorder extra and `MYMCP_RECORDER_ENABLED=false`, no recorder code is imported.

## File layout

```
src/mymcp/recorder/
    __init__.py              # public API: start_recorder_task, get_status, request_bootstrap
    config.py                # recorder-specific pydantic settings (composes with main Settings)
    events.py                # audit log tailer + cursor
    protected_paths.py       # P2 read/write distinction extension
    overview.py              # read/write of overview.md + changelog.md (atomic)
    merge_cycle.py           # normal-mode LLM merge
    bootstrap.py             # agent loop + probe tools
    llm/
        __init__.py
        base.py              # LLMClient protocol, Message/ToolUse/ToolResult types
        anthropic_client.py  # lazy-imports anthropic
        openai_client.py     # lazy-imports openai
    task.py                  # asyncio supervisor
    admin.py                 # FastAPI router for /admin/overview/*
```

Touched (not new):

- `src/mymcp/audit.py` — add `output` field, T1 truncation helpers
- `src/mymcp/tools/files.py` — extend `check_protected_path` with `mode`
- `src/mymcp/mcp_server.py` — register `server_overview` tool when recorder enabled
- `src/mymcp/server.py` — mount recorder admin router; start recorder task in lifespan
- `src/mymcp/config.py` — recorder feature flag plumbing
- `pyproject.toml` — extras

## Testing

Coverage parity with the existing test suite (pytest + anyio, fixtures via `unittest.mock.patch.multiple("mymcp.config", ...)` and `monkeypatch.setenv` + `reset_settings_cache()`). All new public functions and branches in `mymcp.recorder.*` must be exercised. The CI gate (`pytest tests/ -v --benchmark-disable && ruff check . && ruff format --check . && mypy src/mymcp`) must pass.

### Unit tests

- **events.py**: cursor read/write round-trip; rotation recovery (inode change); rotated-past-cursor recovery + `event_loss_total` increment; corrupted cursor file recovery; filtering of read-only tools and failed events.
- **audit.py T1 truncation**: each tool's `output` shape; head/tail boundary at exactly N bytes; multibyte UTF-8 safety (no split mid-codepoint); sha256 stability; truncation knobs from env.
- **protected_paths.py**: legacy single-mode protected paths block both read and write (backwards compat); recorder dir blocks write, allows read; pattern matching corner cases; `MYMCP_PROTECTED_PATHS` env continues to work.
- **overview.py**: atomic write (interrupted tmp left no half-overview); changelog append concurrency-safe; reading missing files returns documented sentinels.
- **llm/base.py types**: Message/ToolUse/ToolResult round-trip, validation.
- **llm/anthropic_client.py & openai_client.py**: SDK-level mocking (no network). Tool-use serialization in both directions. Token-usage extraction. Error propagation for transient vs permanent failures. Lazy import: importing `mymcp.recorder` without the extras installed must not fail; instantiating the client raises a clear error mentioning the install command.
- **merge_cycle.py**: with a fake LLM returning canned JSON — audit log written → events drained → overview.md & changelog.md updated → cursor advanced. Unparseable JSON → backoff, cursor unchanged. No events → no LLM call, no write.
- **bootstrap.py**: fake LLM emitting N `tool_use` blocks then `end_turn` — every probe dispatched, results threaded back, final overview written, changelog seeded. Iteration cap hit → `bootstrap_state=failed`. Token budget exceeded → `bootstrap_state=failed`. Concurrent bootstrap requests coalesced (only one runs).
- **task.py supervisor**: graceful cancellation mid-cycle; cursor persisted on shutdown; restart from cursor without dup or loss.
- **admin.py**: status endpoint shape stable; bootstrap endpoint idempotent under concurrent calls; both require admin token (rw/ro tokens rejected).
- **server_overview tool**: missing overview returns stub + schedules bootstrap (exactly once across concurrent calls); stale overview prepends banner; tool is in `READ_TOOLS` (ro token can call it).

### Integration tests

End-to-end through `create_app()` lifespan with `MYMCP_RECORDER_ENABLED=true` and a fake LLM client injected via dependency override:

- Call mutating tools via HTTP → assert audit entries created → wait for one merge cycle (interval shortened to 1s in test config) → assert overview.md/changelog.md content matches expected snapshot.
- Bootstrap-from-zero: start with no overview → first merge cycle observes missing file → bootstrap task spawned → completes → next merge cycle folds in buffered events.
- Failure surfacing: inject LLM exception → `last_error` populated on status endpoint, overview gains stale banner.
- Lifespan: shutdown mid-cycle, restart, verify no event lost and no duplicate changelog entry.

### Live LLM tests (opt-in)

A `tests/live/` directory holds tests marked `@pytest.mark.live`. They are skipped unless `MYMCP_RECORDER_LIVE_TEST_API_KEY` is set in env. Run locally with DeepSeek (per <https://api-docs.deepseek.com/zh-cn/>):

```bash
# OpenAI-style endpoint (works for the openai adapter)
export MYMCP_RECORDER_LIVE_TEST_API_KEY="$DEEPSEEK_API_KEY"
export MYMCP_RECORDER_LIVE_TEST_PROVIDER=openai
export MYMCP_RECORDER_LIVE_TEST_BASE_URL=https://api.deepseek.com
export MYMCP_RECORDER_LIVE_TEST_MODEL=deepseek-chat
pytest tests/live/ -m live -v

# Anthropic-style endpoint (works for the anthropic adapter)
export MYMCP_RECORDER_LIVE_TEST_PROVIDER=anthropic
export MYMCP_RECORDER_LIVE_TEST_BASE_URL=https://api.deepseek.com/anthropic
pytest tests/live/ -m live -v
```

Live tests cover at minimum: one full merge cycle, one tiny bootstrap (probe budget capped low), JSON-output parseability, token accounting accuracy.

### CI

Default `pytest tests/ -v --benchmark-disable` does **not** invoke live tests (the `live` marker is excluded). Mock-based unit + integration tests run on every PR as today.

For optional live coverage in CI, add `DEEPSEEK_API_KEY` as a GitHub Actions repo secret and add a separate manual workflow (`workflow_dispatch`) that runs `pytest tests/live/ -m live`. Do **not** run live tests on every PR — they cost money, can flake on third-party availability, and a leaked PR could exfiltrate the key. The recommendation is: keep live tests as a local + manual-CI safety net, not a per-PR gate.

## Open questions

None remaining from brainstorming. Ready for implementation planning.

## Migration / rollout

This is a pure addition behind an extras + env flag. No migration needed for existing deployments.

For users opting in:

1. `pip install --upgrade "algony-mymcp[recorder-anthropic]"` (or `recorder-openai` / `recorder`)
2. Set `MYMCP_RECORDER_ENABLED=true` and `MYMCP_RECORDER_LLM_API_KEY=...` in `/etc/mymcp/.env`
3. `sudo systemctl restart mymcp`
4. Bootstrap auto-runs on first start; check progress with `GET /admin/overview/status`
