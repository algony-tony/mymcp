# Linux MCP Server

[![CI](https://github.com/algony-tony/mymcp/actions/workflows/ci.yml/badge.svg)](https://github.com/algony-tony/mymcp/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/algony-tony/f5b7d1a23781d63db40ea2e2dcdf71c2/raw/mymcp-coverage.json&cacheSeconds=3600)](https://github.com/algony-tony/mymcp/actions/workflows/ci.yml)
[![Branch Coverage](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/algony-tony/f5b7d1a23781d63db40ea2e2dcdf71c2/raw/mymcp-branch-coverage.json&cacheSeconds=3600)](https://github.com/algony-tony/mymcp/actions/workflows/ci.yml)
[![Go Coverage](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/algony-tony/f5b7d1a23781d63db40ea2e2dcdf71c2/raw/mymcp-go-coverage.json&cacheSeconds=3600)](https://github.com/algony-tony/mymcp/actions/workflows/ci.yml)
[![Mutation Score](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/algony-tony/f5b7d1a23781d63db40ea2e2dcdf71c2/raw/mymcp-mutation.json&cacheSeconds=3600)](https://github.com/algony-tony/mymcp/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

A Python MCP server that exposes full Linux system control to AI clients (Claude Desktop, Claude Code, Cursor, Gemini CLI, etc.) over Streamable HTTP.

## Features

- **9 MCP tools**: `bash_execute`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`, `prepare_upload`, `prepare_download`, `server_overview` (optional, enabled via `MYMCP_RECORDER_ENABLED=true`)
- **Binary / large file transfer**: bypass HTTP endpoints (`PUT/GET /files/raw/{ticket}`) for streaming uploads and downloads — file bytes never enter the LLM context window
- **Per-token permissions**: read-only (`ro`) or read-write (`rw`) roles
- **Audit logging**: JSON Lines audit trail with error details for all tool invocations
- **Application logging**: errors and warnings output to stderr (captured by journald)
- **Protected paths**: MCP's own files are protected from tool access
- **Multi-user auth**: Bearer token authentication with per-token management
- **Admin API**: create/revoke tokens without restarting the server
- **Streamable HTTP transport**: stateless mode, each request is independent (no session issues on reconnect)

> **v3.0.0:** the server is now a single static **Go binary** shipped inside
> linux amd64/arm64 platform wheels — `pipx install algony-mymcp` still gives you
> the `mymcp` command, now with **zero Python dependencies**. The optional
> overview recorder is a separate `mymcp-recorder` sidecar (`pip install
> "algony-mymcp[recorder]"`). The `install-service`/`uninstall-service`/`doctor`
> CLI subcommands were removed; the systemd unit still runs `mymcp serve`. See
> [CHANGELOG](CHANGELOG.md) for the full breaking-change list.

## Requirements

- Linux x86_64 or arm64 (the wheel bundles a static binary; no Python runtime deps)
- `ripgrep` (`rg`) — optional but recommended for faster `grep` tool (falls back to a native scan)

## Install

```bash
pipx install algony-mymcp
```

The PyPI distribution name is `algony-mymcp` (the bare name `mymcp` is reserved
on PyPI). The installed command is plain `mymcp` (the Go binary).

Plain `pip` works too (a venv is recommended):

```bash
python3 -m venv ~/.local/share/mymcp-env
~/.local/share/mymcp-env/bin/pip install algony-mymcp
ln -s ~/.local/share/mymcp-env/bin/mymcp ~/.local/bin/mymcp
```

### Quick try (foreground, no system service)

```bash
mymcp serve
```

`mymcp` prints a temporary admin and rw token to stderr, listens on
`0.0.0.0:8765` by default, and discards both tokens on exit.

### Production install (systemd)

v3 removed the `install-service` helper (it was Python-CLI machinery). Install
the unit manually: create `/etc/mymcp/.env` (see [Configuration](#configuration)),
generate an admin token (`mymcp token add --role rw` writes to the token store),
and drop a unit at `/etc/systemd/system/mymcp.service` whose `ExecStart` is
`mymcp serve --env-file /etc/mymcp/.env`. Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now mymcp
```

Existing v2 deployments already have this unit — a `pipx upgrade` + restart
keeps it (the `ExecStart=mymcp serve` line is unchanged; it now runs the Go
binary). Install `ripgrep` separately for fast file search.

### Upgrade

```bash
pipx upgrade algony-mymcp
sudo systemctl restart mymcp
```

### Backup and disaster recovery

See [docs/operations/backup-and-disaster-recovery.md](docs/operations/backup-and-disaster-recovery.md)
for what to back up (token store, audit log, recorder data, `.env`),
restore procedures, and a failure-mode → response table.

### Air-gapped install

Each GitHub Release ships a `mymcp-X.Y.Z-offline-bundle.tar.gz` containing
all wheels and ripgrep binaries:

```bash
tar xzf mymcp-3.0.0-offline-bundle.tar.gz
cd mymcp-3.0.0-offline-bundle
sudo ./install-offline.sh   # installs the platform wheel (Go binary) + ripgrep
```

Then install the systemd unit manually (see Production install) and start it.

### Why we don't ship a Dockerfile

mymcp's purpose is to let an LLM operate the host Linux system: install
packages, edit config under `/etc`, manage processes, read/write files
under `/home`. Running mymcp inside a container defeats this — by default
the container sees only its own filesystem and PID namespace, so the LLM
can only operate the container itself.

To make a containerised mymcp actually control the host you'd need at
minimum `--privileged --pid=host --net=host -v /:/host` and a convention
that the LLM works against `/host`. At that point the container provides
no isolation; it's an awkward installer.

If you really want a container (e.g. as a sidecar that manages another
containerised service), it is ~10 lines:

```dockerfile
FROM python:3.13-slim
RUN pip install algony-mymcp
EXPOSE 8080
ENV MYMCP_HOST=0.0.0.0 MYMCP_PORT=8080
CMD ["mymcp", "serve"]
```

The recommended deployment is `pipx install algony-mymcp` + a systemd unit
running `mymcp serve`, which gives the service direct host access matching the
product's purpose.

## Optional: server overview recorder

A standalone `mymcp-recorder` sidecar that maintains a self-updating server
overview document via LLM (calls go through httpx). Install with
`pip install "algony-mymcp[recorder]"`; disabled by default, enable with
`MYMCP_RECORDER_ENABLED=true`. See
`docs/superpowers/specs/2026-05-29-llm-recorder-design.md` for full details.

## CLI Reference

`mymcp` is the Go server binary and uses Go-style single-dash flags.

### Top-level commands

| Command | Purpose |
|---------|---------|
| `mymcp serve` | Run the MCP server in the foreground |
| `mymcp version` | Print the version |
| `mymcp token list` | Show admin/metrics state and ro/rw tokens |
| `mymcp token add [--role ro\|rw] <name>` | Create a token (printed once) |
| `mymcp token revoke <token>` | Delete a token |

The v2 `install-service` / `uninstall-service` / `doctor` subcommands were
removed in v3 (see the note at the top). `mymcp-recorder` is a separate command
provided by the `[recorder]` extra.

### `mymcp serve`

| Flag | Description |
|------|-------------|
| `-env-file PATH` | Load settings from a specific env file |
| `-host HOST` | Override bind host |
| `-port PORT` | Override bind port |

All other settings come from `MYMCP_*` env vars (see Configuration). With no
token store present, `serve` runs in temporary-token mode: it prints an
ephemeral admin + rw token to stderr and discards them on exit.

## Configuration

The `serve` command reads `MYMCP_*` env vars and resolves an env file from
`-env-file PATH`, then `MYMCP_ENV_FILE`, then `/etc/mymcp/.env`, then (in dev)
`./.env`.

### Core

| Variable | Default | Description |
|----------|---------|-------------|
| `MYMCP_ADMIN_TOKEN` | *(required for /admin)* | Admin token for managing user tokens |
| `MYMCP_METRICS_TOKEN` | *(empty = disabled)* | Bearer for `/metrics` endpoint |
| `MYMCP_HOST` | `0.0.0.0` | Bind address |
| `MYMCP_PORT` | `8765` | Listen port |
| `MYMCP_TOKEN_FILE` | `/etc/mymcp/tokens.json` | Token store path |
| `MYMCP_PROTECTED_PATHS` | *(empty)* | Additional protected paths, comma-separated |
| `MYMCP_SHUTDOWN_GRACE_SEC` | `5` | Seconds to wait for in-flight bash children on SIGTERM |

### Audit Logging

| Variable | Default | Description |
|----------|---------|-------------|
| `MYMCP_AUDIT_ENABLED` | `false` | Enable audit logging |
| `MYMCP_AUDIT_LOG_DIR` | `/var/log/mymcp` | Audit log directory (auto-protected) |
| `MYMCP_AUDIT_MAX_BYTES` | `10485760` | Max audit log file size before rotation (10MB) |
| `MYMCP_AUDIT_BACKUP_COUNT` | `5` | Number of rotated log files to keep |

### Tool Limits

All limits are configurable via environment variables. Default values work well for most use cases.

| Variable | Default | Description |
|----------|---------|-------------|
| `MYMCP_BASH_MAX_OUTPUT_BYTES` | `102400` | bash stdout/stderr default cap (100KB) |
| `MYMCP_BASH_MAX_OUTPUT_BYTES_HARD` | `1048576` | bash output hard cap (1MB) |
| `MYMCP_READ_FILE_DEFAULT_LIMIT` | `2000` | read_file default lines per request |
| `MYMCP_READ_FILE_MAX_LIMIT` | `50000` | read_file max lines per request |
| `MYMCP_READ_FILE_MAX_LINE_BYTES` | `32768` | Max bytes per line before truncation (32KB) |
| `MYMCP_WRITE_FILE_MAX_BYTES` | `10485760` | write_file max size (10MB) |
| `MYMCP_EDIT_STRING_MAX_BYTES` | `1048576` | edit_file max old/new string size (1MB) |
| `MYMCP_GLOB_MAX_RESULTS` | `1000` | Max file paths returned by glob |
| `MYMCP_GREP_DEFAULT_MAX_RESULTS` | `500` | grep default max matches |
| `MYMCP_GREP_MAX_RESULTS` | `5000` | grep hard max matches |

### Recorder (optional)

These only apply to the `mymcp-recorder` sidecar (install
`pip install "algony-mymcp[recorder]"`) when it is enabled.

| Variable | Default | Description |
|----------|---------|-------------|
| `MYMCP_RECORDER_ENABLED` | `false` | Let the `mymcp-recorder` sidecar start (the Go `server_overview` tool works regardless, returning a stub until an overview exists) |
| `MYMCP_RECORDER_DATA_DIR` | `/var/lib/mymcp/recorder` | Where overview + changelog + cursor live |
| `MYMCP_RECORDER_MERGE_INTERVAL_SEC` | `300` | How often the merge cycle runs |
| `MYMCP_RECORDER_MAX_EVENTS_PER_CYCLE` | `50` | Cap on audit events folded per cycle |
| `MYMCP_RECORDER_BOOTSTRAP_MAX_ITERATIONS` | `200` | Max probe iterations during initial bootstrap |
| `MYMCP_RECORDER_BOOTSTRAP_TOKEN_BUDGET` | `10000000` | LLM token budget for bootstrap |
| `MYMCP_RECORDER_BOOTSTRAP_PROBE_TIMEOUT_SEC` | `30` | Per-probe timeout during bootstrap |
| `MYMCP_RECORDER_BOOTSTRAP_RETRY_INTERVAL_SEC` | `3600` | Retry interval if bootstrap fails |
| `MYMCP_RECORDER_LLM_PROVIDER` | `anthropic` | `anthropic` or `openai` (OpenAI-compatible) |
| `MYMCP_RECORDER_LLM_MODEL` | *(provider default)* | Model id override |
| `MYMCP_RECORDER_LLM_API_KEY` | *(unset)* | API key for the chosen provider |
| `MYMCP_RECORDER_LLM_BASE_URL` | *(unset)* | Base URL override (e.g. DeepSeek for the OpenAI adapter) |
| `MYMCP_RECORDER_LLM_MAX_TOKENS` | `16384` | Per-call output ceiling for the recorder's LLM. Must stay ≤ the chosen model's `max_output_tokens`. Larger values let the recorder cover more sections per cycle but cost more per call. |
| `MYMCP_RECORDER_CIRCUIT_BREAKER_THRESHOLD` | `5` | Consecutive merge failures before the breaker opens. Once open, recovery is event-driven (a new event triggers a single retry; success clears the breaker). Set to `0` to disable. |

## Managing Tokens

The `mymcp token` subcommands operate on the local token store directly (no
admin API call required). They read `/etc/mymcp/.env` by default; use
`MYMCP_ENV_FILE=...` to point elsewhere.

```bash
# List all tokens (admin/metrics state + ro/rw entries)
sudo mymcp token list

# Create a read-only token
sudo mymcp token add --name my-claude-desktop --role ro

# Create a read-write token
sudo mymcp token add --name my-admin-client --role rw

# Revoke
sudo mymcp token revoke tok_abc123

# Rotate the admin or metrics token (rewrites .env)
sudo mymcp token rotate-admin
sudo mymcp token rotate-metrics

# Disable the /metrics endpoint by emptying the metrics token
sudo mymcp token disable-metrics
```

The HTTP `/admin/*` API still works for clients that need to manage tokens
remotely; it requires `Authorization: Bearer <MYMCP_ADMIN_TOKEN>`.

## HTTP Endpoints

### Public / operational endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| `GET` | `/health` | none | Liveness check with version |
| `GET` | `/version` | none | Return just the server version |
| `GET` | `/metrics` | metrics token | Prometheus metrics |
| `POST` | `/mcp` | ro/rw token | Streamable HTTP MCP transport |

Examples:

```bash
curl http://your-server:8765/health
curl http://your-server:8765/version
curl -H "Authorization: Bearer $MYMCP_METRICS_TOKEN" \
  http://your-server:8765/metrics
```

Notes:
- `/metrics` returns `401` if the bearer token is wrong.
- `/metrics` returns `503` if metrics support is disabled or no metrics token is configured.

### Admin API

All `/admin/*` endpoints require:

```http
Authorization: Bearer <MYMCP_ADMIN_TOKEN>
```

| Method | Path | Body | Purpose |
|--------|------|------|---------|
| `GET` | `/admin/tokens` | none | List managed ro/rw tokens |
| `POST` | `/admin/tokens` | `{"name":"...", "role":"ro|rw"}` | Create a token |
| `DELETE` | `/admin/tokens/{token}` | none | Revoke a token |

Examples:

```bash
# List tokens
curl -H "Authorization: Bearer $MYMCP_ADMIN_TOKEN" \
  http://your-server:8765/admin/tokens

# Create a read-only token
curl -X POST \
  -H "Authorization: Bearer $MYMCP_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"ci-bot","role":"ro"}' \
  http://your-server:8765/admin/tokens

# Revoke a token
curl -X DELETE \
  -H "Authorization: Bearer $MYMCP_ADMIN_TOKEN" \
  http://your-server:8765/admin/tokens/tok_abc123
```

## Connecting Clients

### Claude Desktop / Cursor

Add to MCP settings:

```json
{
  "mcpServers": {
    "linux-server": {
      "type": "streamableHttp",
      "url": "http://your-server:8765/mcp",
      "headers": {
        "Authorization": "Bearer tok_abc123"
      }
    }
  }
}
```

### Claude Code

```bash
claude mcp add linux-server \
  --transport streamable-http \
  --url http://your-server:8765/mcp \
  --header "Authorization: Bearer tok_abc123"
```

## MCP Tools

### Permission model

| Tool | Permission | Summary |
|------|-----------|---------|
| `bash_execute` | rw | Run a shell command in a fresh subprocess |
| `read_file` | ro | Read a file with line numbers and pagination |
| `write_file` | rw | Create or overwrite a file |
| `edit_file` | rw | Replace text in a file |
| `glob` | ro | Find paths by glob pattern |
| `grep` | ro | Search file contents with regex |
| `prepare_upload` | rw | Mint a one-time signed URL for uploading bytes to a server path |
| `prepare_download` | ro | Mint a one-time signed URL for downloading bytes from a server path |
| `server_overview` | ro | Return the maintained server overview (requires the recorder module; see below) |

`ro` tokens can use `read_file`, `glob`, `grep`, `prepare_download`, and
`server_overview`. `rw` tokens can use all nine tools.

### `bash_execute` (`rw`)

Runs a shell command in a new subprocess. Commands are stateless: one call does
not preserve cwd, environment changes, shell functions, or exports for the next.

Parameters:

| Field | Type | Required | Default | Notes |
|------|------|----------|---------|-------|
| `command` | string | yes | – | Shell command to run |
| `timeout` | integer | no | `30` | Clamped to `1..600` seconds |
| `working_dir` | string | no | `/` | Command cwd |
| `max_output_bytes` | integer | no | `102400` | Per-stream cap; hard max `1048576` |

Returns:
- `stdout`
- `stderr`
- `exit_code`
- `timed_out`

Example:

```json
{
  "command": "uname -a",
  "timeout": 10,
  "working_dir": "/tmp"
}
```

### `read_file` (`ro`)

Reads a file and returns numbered lines. Large reads support pagination.

Parameters:

| Field | Type | Required | Default | Notes |
|------|------|----------|---------|-------|
| `file_path` | string | yes | – | Absolute path |
| `offset` | integer | no | `1` | 1-based start line |
| `limit` | integer | no | `2000` | Runtime max `50000` |

Returns:
- `content`
- `total_lines`
- `truncated`

Common errors:
- `FileNotFoundError`
- `IsADirectoryError`
- `PermissionError`
- `ProtectedPath`

Example:

```json
{
  "file_path": "/var/log/syslog",
  "offset": 1,
  "limit": 200
}
```

### `write_file` (`rw`)

Creates or overwrites a file in one shot.

Parameters:

| Field | Type | Required | Default | Notes |
|------|------|----------|---------|-------|
| `file_path` | string | yes | – | Absolute path |
| `content` | string | yes | – | Max size `10485760` bytes (10 MB) |

Notes:
- Missing parent directories are created automatically.
- Protected paths are rejected.

Example:

```json
{
  "file_path": "/tmp/hello.txt",
  "content": "hello from mymcp\n"
}
```

### `edit_file` (`rw`)

Replaces text in an existing file.

Parameters:

| Field | Type | Required | Default | Notes |
|------|------|----------|---------|-------|
| `file_path` | string | yes | – | Absolute path |
| `old_string` | string | yes | – | Max size `1048576` bytes |
| `new_string` | string | yes | – | Max size `1048576` bytes |
| `replace_all` | boolean | no | `false` | If false, `old_string` must match uniquely |

Use this when the client wants a precise in-file replacement instead of full overwrite.

Example:

```json
{
  "file_path": "/tmp/app.conf",
  "old_string": "PORT=8000",
  "new_string": "PORT=8765"
}
```

### `glob` (`ro`)

Finds matching files under a root directory. Results are sorted by modified time descending.

Parameters:

| Field | Type | Required | Default | Notes |
|------|------|----------|---------|-------|
| `pattern` | string | yes | – | Example: `**/*.py` |
| `path` | string | no | `/` | Root directory |

Runtime max results: `1000`.

Example:

```json
{
  "pattern": "**/*.log",
  "path": "/var/log"
}
```

### `grep` (`ro`)

Searches file contents with a regex. Uses `ripgrep` if available; otherwise falls back to a Python implementation.

Parameters:

| Field | Type | Required | Default | Notes |
|------|------|----------|---------|-------|
| `pattern` | string | yes | – | Regex pattern |
| `path` | string | no | `/` | File or directory to search |
| `glob` | string | no | none | Filename filter, e.g. `*.py` |
| `output_mode` | string | no | `content` | One of `content`, `files`, `count` |
| `context_lines` | integer | no | `0` | Include surrounding lines |
| `max_results` | integer | no | `500` | Runtime max `5000` |
| `case_insensitive` | boolean | no | `false` | Case-insensitive search |

Example:

```json
{
  "pattern": "Authorization",
  "path": "/etc",
  "glob": "*.conf",
  "output_mode": "content",
  "context_lines": 2
}
```

### `prepare_upload` (`rw`) and `prepare_download` (`ro`)

Use for **binary files** or files **larger than 10 MB** that don't need to be read into the conversation. The MCP tool only returns a small JSON object containing a one-time signed URL — the actual file bytes never enter the LLM context window. The client then drives the byte transfer with a regular `curl` from its local shell.

Workflow:

1. LLM calls `prepare_upload(dest_path="/tmp/foo.deb", max_bytes=20_000_000)` → server returns a JSON dict with `url`, `method: "PUT"`, `expires_in` (default 300 s), `max_bytes`, and a ready-to-run `curl_example` like `curl -fsS -T /local/path/to/file 'https://server/files/raw/<ticket>'`.
2. LLM (or the user) runs the curl on the **MCP client's local shell**. Bytes stream straight to the server — no MCP message, no base64.
3. Server writes to a temp file alongside the destination, atomically replaces it on success, and returns `{"ok": true, "path": "...", "bytes_written": N}`.

`prepare_download` is the mirror: it returns a `GET` URL, you `curl URL -o local`, and bytes stream back.

Tickets are **single-use**, **path-scoped**, **byte-bounded**, and expire after `expires_in` seconds (default 300, max 900). Both endpoints reuse the same `check_protected_path` and audit log as the file tools. Server tunables: `MYMCP_TRANSFER_ENABLED`, `MYMCP_TRANSFER_MAX_BYTES` (default 2 GB), `MYMCP_TRANSFER_DEFAULT_TTL_SEC`, `MYMCP_TRANSFER_MAX_TTL_SEC`, `MYMCP_PUBLIC_BASE_URL` (set when behind a reverse proxy).

### `server_overview` (`ro`)

Returns the current contents of the server overview document maintained by
the optional recorder module. Only available when the recorder is installed
and `MYMCP_RECORDER_ENABLED=true`; otherwise the call fails with a clear
error message. Takes no parameters.

The recorder periodically folds successful mutating audit events into an
overview of what's installed and recently changed on the host. The
companion `changelog.md` in the same directory can be read with `read_file`.
See the "Optional: server overview recorder" section above for setup; full
design details are in `docs/superpowers/specs/2026-05-29-llm-recorder-design.md`.

### Tool behavior notes

- File tools enforce protected-path checks.
- `bash_execute` does **not** enforce protected-path checks; use `ro` tokens for untrusted clients.
- `grep` and `glob` are capped to protect the server from unbounded scans.
- `read_file` truncates oversized individual lines and marks them with `[LINE TRUNCATED]`.
- `prepare_upload`/`prepare_download` enforce protected-path checks at both **mint** and **redeem** time.

## Logging

### Audit Log

When enabled (`MYMCP_AUDIT_ENABLED=true`), all tool invocations are logged to `<MYMCP_AUDIT_LOG_DIR>/audit.log` in JSON Lines format:

```json
{"ts":"2026-04-10T15:30:22Z","token_name":"my-client","role":"rw","ip":"203.0.113.5","tool":"bash_execute","params":{"command":"apt update"},"result":"ok","duration_ms":1523}
```

Error entries include `error_code` and `error_message`:

```json
{"ts":"2026-04-10T15:31:00Z","token_name":"ro-client","role":"ro","ip":"203.0.113.5","tool":"read_file","params":{"file_path":"/var/log/mymcp/audit.log"},"result":"error","error_code":"ProtectedPath","error_message":"Access denied: path is within protected directory","duration_ms":0}
```

Logs rotate automatically (default 10MB with 5 backups).

### Application Log

Tool errors and warnings are also output to stderr, which is captured by journald when running as a systemd service:

```bash
journalctl -u mymcp -f
```

## Protected Paths

MCP automatically protects its own installation directory and audit log directory from access via file tools (`read_file`, `write_file`, `edit_file`, `glob`, `grep`). This prevents AI clients from reading tokens, modifying server code, or tampering with audit logs.

Add extra protected paths via `MYMCP_PROTECTED_PATHS=/path/one,/path/two`.

Note: `bash_execute` is not subject to path protection — use `ro` tokens for untrusted clients.

## Observability

mymcp emits metrics, traces, and logs via OpenTelemetry. The default install supports Prometheus pull at `/metrics`; the `[otlp]` extra adds OTLP push for any backend.

### Quick reference

| Capability | Default install | `pip install algony-mymcp[otlp]` |
|---|---|---|
| `/metrics` Prometheus pull endpoint | yes | yes |
| OTLP push (metrics + traces + logs) | no | yes (when endpoint set) |
| FastAPI/ASGI auto-instrumentation | no | yes |
| Audit log → local file | yes | yes |
| Audit log → OTLP push | no | yes |
| Application logs → stderr (JSON) | yes | yes |

### Recipe 1 — Grafana Cloud (free tier)

```bash
pip install algony-mymcp[otlp]

export OTEL_EXPORTER_OTLP_ENDPOINT="https://otlp-gateway-prod-us-central-0.grafana.net/otlp"
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic <base64(instanceId:token)>"
export OTEL_SERVICE_NAME=mymcp

mymcp serve
```

Import `deploy/grafana/mymcp-dashboard.json` (metrics, incl. the Recorder
Health row) and `deploy/grafana/mymcp-logs-dashboard.json` (Loki+Tempo) into
Grafana Cloud.

### Recipe 2 — Self-hosted LGTM stack

`docker-compose.yml`:

```yaml
services:
  collector:
    image: otel/opentelemetry-collector-contrib:latest
    ports: ["4318:4318"]
    volumes: ["./otelcol-config.yaml:/etc/otelcol-contrib/config.yaml"]
  mimir:
    image: grafana/mimir:latest
    command: -config.file=/etc/mimir.yaml
  loki:
    image: grafana/loki:latest
  tempo:
    image: grafana/tempo:latest
  grafana:
    image: grafana/grafana:latest
    ports: ["3000:3000"]
```

Then run mymcp with:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 mymcp serve
```

Import the dashboard JSON; configure Mimir/Loki/Tempo as data sources.

### Recipe 3 — Pull-only Prometheus

No extra needed. Configure Prometheus:

```yaml
scrape_configs:
  - job_name: mymcp
    bearer_token: <your MYMCP_METRICS_TOKEN>
    static_configs:
      - targets: ['localhost:8000']
```

Import the dashboard JSON; the Traces and Logs panels remain empty (this is expected).

### Audit log integrity

| Metric | Type | Description |
|---|---|---|
| `mymcp_audit_write_failures_total` | counter | Audit log writes rejected (disk full, permission denied, rotation race). Tool calls in this state return `InternalError`. |

Silent audit loss is a SOC red line, so the Grafana dashboard ships an
**Audit Log Integrity** row (cumulative + per-second-rate panels). The
project does NOT ship alert rules — alerting is deployment-specific.
Recommended PromQL recipe for operators:

```
rate(mymcp_audit_write_failures_total[5m]) > 0
```

### Recorder metrics

When the recorder is enabled, these extra series appear on `/metrics`:

| Metric | Type | Labels | Use |
|---|---|---|---|
| `mymcp_recorder_merge_cycles_total` | counter | `reason` | One outcome per cycle. Reasons: `success`, `no_events`, `bootstrap_required`, `llm_error`, `max_tokens`, `empty`, `unparseable`, `schema_invalid`, `apply_error`. |
| `mymcp_recorder_merge_duration_seconds` | histogram | `reason` | Wall-clock per merge cycle (incl. LLM). p95: `histogram_quantile(0.95, sum(rate(..._bucket[5m])) by (le))`. |
| `mymcp_recorder_llm_calls_total` | counter | `phase`, `result` | HTTP boundary only — `success` means a response object was returned, not that it was usable. Response-quality failures flow through `merge_cycles{reason=…}`. |
| `mymcp_recorder_llm_tokens_total` | counter | `phase`, `direction` | Throughput by `input`/`output`. |
| `mymcp_recorder_pending_events` | gauge | — | Backlog: mutating audit events past the cursor. A growing line means the recorder is stuck. |
| `mymcp_recorder_merge_last_attempt_timestamp` | gauge | — | Unix seconds of the last merge attempt (success OR failure). Does not advance on idle ticks. Pair with `pending_events` for the canonical stuck signal. |
| `mymcp_recorder_merge_last_success_timestamp` | gauge | — | Unix seconds. `0` = never. Informational only (health trending); use the composite recipe below for staleness detection. |
| `mymcp_recorder_circuit_open` | gauge | — | `1` once the supervisor has tripped its consecutive-failure breaker. Recovery is event-driven: a new event past the high-water triggers one retry; success clears the breaker. No restart required. |
| `mymcp_recorder_event_loss_total` | counter | — | Audit lines lost because rotation moved the file past the cursor. |
| `mymcp_recorder_events_consumed_total` | counter | `tool` | Per-tool count of mutating events folded into the overview. |

**Recommended stale-recorder query** (project ships metrics and dashboards
only; alert rules are deployment-specific and not bundled):

```
( mymcp_recorder_pending_events > 0
  AND time() - mymcp_recorder_merge_last_attempt_timestamp > 1800 )
OR mymcp_recorder_circuit_open == 1
```

Both terms together avoid the historical false positive where an idle server
(no events to process) appeared "stale" because the success-timestamp gauge
hadn't moved.

The Grafana metrics dashboard's **Recorder Health** row materialises all of
the above. The supervisor's per-tick work runs inside a
`recorder.supervisor.cycle` span so `recorder.supervisor.cycle_error` log
lines carry the matching `trace_id`/`span_id` for jumping into Tempo from Loki.

### Configuration knobs

All standard OTel env vars work. The most useful:

| Variable | Default | Purpose |
|---|---|---|
| `OTEL_SERVICE_NAME` | `mymcp` | Service name |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | unset | OTLP target |
| `OTEL_EXPORTER_OTLP_HEADERS` | unset | OTLP auth headers |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `http/protobuf` | http or grpc |
| `OTEL_TRACES_SAMPLER` | `parentbased_traceidratio` | Sampler |
| `OTEL_TRACES_SAMPLER_ARG` | `1.0` | Sampling ratio |
| `OTEL_METRIC_EXPORT_INTERVAL` | `60000` (ms) | Push period |
| `MYMCP_LOG_LEVEL` | `INFO` | Application log level |

## Testing

```bash
# Run all tests (excludes benchmarks)
python -m pytest tests/ -v --benchmark-disable

# Run with coverage report (Python recorder sidecar)
python -m pytest tests/ -v --cov=. --cov-branch --cov-report=term-missing --benchmark-disable

# Go statement coverage (the server; whole-module, matches the CI badge)
cd go && go test -covermode=atomic -coverpkg=./... -coverprofile=cover.out ./... \
  && go tool cover -func=cover.out | tail -1

# Run benchmark tests only
python -m pytest tests/test_benchmark.py --benchmark-only -v

# Save benchmark baseline for comparison
python -m pytest tests/test_benchmark.py --benchmark-save=baseline

# Run mutation testing
python -m mutmut run --max-children 1
python -m mutmut results

# Run load tests (start server first: mymcp serve)
export MYMCP_TEST_TOKEN=<your-rw-token>
locust -f tests/loadtest/locustfile.py --host http://localhost:8765
```

### Test Dimensions

| Dimension | Tool | Target |
|-----------|------|--------|
| Line coverage (Python recorder) | pytest-cov | 97%+ |
| Branch coverage (Python recorder) | pytest-cov --cov-branch | tracked |
| Go statement coverage (server) | go test -coverpkg=./... | 65%+ floor (~69% now) |
| Integration tests | httpx ASGITransport | full auth->tool->audit chain |
| Boundary analysis | pytest | all parameter edge cases |
| Performance benchmarks | pytest-benchmark | per-function timing |
| Load testing | locust | multi-user concurrency |
| Mutation testing | mutmut | 80%+ score |

## Security Note

This server grants system access to AI clients. Security measures:

- **Permissions**: New tokens default to `ro` (read-only). Only grant `rw` to trusted clients.
- **Audit**: Enable audit logging to track all tool invocations.
- **Protected paths**: Server files are automatically protected from tool access.
- **Network**: Run behind a firewall and consider TLS (e.g. via nginx reverse proxy).
