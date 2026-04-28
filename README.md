# Linux MCP Server

[![CI](https://github.com/algony-tony/mymcp/actions/workflows/ci.yml/badge.svg)](https://github.com/algony-tony/mymcp/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/algony-tony/f5b7d1a23781d63db40ea2e2dcdf71c2/raw/mymcp-coverage.json&cacheSeconds=3600)](https://github.com/algony-tony/mymcp/actions/workflows/ci.yml)
[![Branch Coverage](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/algony-tony/f5b7d1a23781d63db40ea2e2dcdf71c2/raw/mymcp-branch-coverage.json&cacheSeconds=3600)](https://github.com/algony-tony/mymcp/actions/workflows/ci.yml)
[![Mutation Score](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/algony-tony/f5b7d1a23781d63db40ea2e2dcdf71c2/raw/mymcp-mutation.json&cacheSeconds=3600)](https://github.com/algony-tony/mymcp/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

A Python MCP server that exposes full Linux system control to AI clients (Claude Desktop, Claude Code, Cursor, Gemini CLI, etc.) over Streamable HTTP.

## Features

- **6 MCP tools**: `bash_execute`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`
- **Per-token permissions**: read-only (`ro`) or read-write (`rw`) roles
- **Audit logging**: JSON Lines audit trail with error details for all tool invocations
- **Application logging**: errors and warnings output to stderr (captured by journald)
- **Protected paths**: MCP's own files are protected from tool access
- **Multi-user auth**: Bearer token authentication with per-token management
- **Admin API**: create/revoke tokens without restarting the server
- **Streamable HTTP transport**: stateless mode, each request is independent (no session issues on reconnect)

## Requirements

- Python 3.11+
- `ripgrep` (`rg`) — optional but recommended for faster `grep` tool (falls back to Python regex)

## Install

Requires Python 3.11+ on Linux.

```bash
pipx install algony-mymcp
```

The PyPI distribution name is `algony-mymcp` (the bare name `mymcp` is reserved
on PyPI). After install the command and the Python import path are still
plain `mymcp`.

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
`127.0.0.1:8765`, and discards both tokens on exit.

### Production install (systemd)

```bash
sudo mymcp install-service --yes
sudo systemctl start mymcp
```

This writes `/etc/mymcp/.env`, generates an admin token (printed once),
optionally generates a metrics token, installs `/etc/systemd/system/mymcp.service`,
sets up logrotate for `/var/log/mymcp/audit.log`, and (by default) installs
`ripgrep` for fast file search.

Useful flags: `--port 9000`, `--bind 127.0.0.1`, `--config-dir`, `--log-dir`,
`--service-user mymcp` (run as a restricted user), `--no-metrics`,
`--no-audit`, `--skip-ripgrep`.

### Upgrade

```bash
pipx upgrade algony-mymcp
sudo systemctl restart mymcp
```

### Air-gapped install

Each GitHub Release ships a `mymcp-X.Y.Z-offline-bundle.tar.gz` containing
all wheels and ripgrep binaries:

```bash
tar xzf mymcp-2.0.0-offline-bundle.tar.gz
cd mymcp-2.0.0-offline-bundle
sudo ./install-offline.sh
sudo mymcp install-service --yes
```

## Upgrading from 1.x to 2.0

Breaking changes:
- Environment variable prefix renamed: `MCP_*` → `MYMCP_*` (no compat shim).
- Install layout: `/opt/mymcp/` (1.x) → `/etc/mymcp/` (2.0). Code is now
  managed by `pipx`, not unpacked into `/opt/mymcp/`.
- Install method: `git clone + deploy/install.sh` → `pipx install algony-mymcp`.

One-line migration:

```bash
pipx install algony-mymcp
sudo mymcp migrate-from-legacy
sudo rm -rf /opt/mymcp     # after verifying the new service is healthy
```

`mymcp migrate-from-legacy` reads `/opt/mymcp/.env`, rewrites `MCP_*` keys to
`MYMCP_*`, copies `tokens.json`, installs the new systemd unit, and restarts
the service. Pass `--dry-run` to see what it would do without making changes.

The legacy `deploy/install.sh` and `deploy/upgrade.sh` scripts remain in the
repository through the 2.0.x lifecycle for users who can't migrate yet.

## Configuration

`mymcp install-service` writes `/etc/mymcp/.env`. The `serve` command also
honors `--env-file PATH`, `MYMCP_ENV_FILE`, and (in dev) `./.env`.

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

| Tool | Permission | Description |
|------|-----------|-------------|
| `bash_execute` | rw | Run any shell command |
| `read_file` | ro | Read file with line numbers and pagination |
| `write_file` | rw | Create or overwrite a file (max 10MB) |
| `edit_file` | rw | Replace a string in a file |
| `glob` | ro | Find files by pattern |
| `grep` | ro | Search file contents with regex |

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

## Testing

```bash
# Run all tests (excludes benchmarks)
python -m pytest tests/ -v --benchmark-disable

# Run with coverage report
python -m pytest tests/ -v --cov=. --cov-branch --cov-report=term-missing --benchmark-disable

# Run benchmark tests only
python -m pytest tests/test_benchmark.py --benchmark-only -v

# Save benchmark baseline for comparison
python -m pytest tests/test_benchmark.py --benchmark-save=baseline

# Run mutation testing
python -m mutmut run
python -m mutmut results

# Run load tests (start server first: mymcp serve)
export MYMCP_TEST_TOKEN=<your-rw-token>
locust -f tests/loadtest/locustfile.py --host http://localhost:8765
```

### Test Dimensions

| Dimension | Tool | Target |
|-----------|------|--------|
| Line coverage | pytest-cov | 97%+ |
| Branch coverage | pytest-cov --cov-branch | tracked |
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
