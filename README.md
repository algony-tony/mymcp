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

## Quick Deploy

```bash
git clone https://github.com/algony-tony/mymcp.git
cd mymcp
sudo bash deploy/install.sh
```

The install script will:
- Find a suitable Python 3.11+ binary automatically
- Install `ripgrep` if missing (dnf/apt/yum/pacman)
- Create a venv at `/opt/mymcp/venv` with all dependencies
- Install a systemd service (`mymcp`) with auto-restart

Then:

```bash
# 1. Set your admin token
sudo vim /opt/mymcp/.env

# 2. Start the service
sudo systemctl start mymcp

# 3. Check logs
journalctl -u mymcp -f
```

## Configuration

Edit `/opt/mymcp/.env`. See `.env.example` for all available settings.

### Core

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_ADMIN_TOKEN` | *(required)* | Admin token for managing user tokens |
| `MCP_HOST` | `0.0.0.0` | Bind address |
| `MCP_PORT` | `8765` | Listen port |
| `MCP_TOKEN_FILE` | `/opt/mymcp/tokens.json` | Token store path |
| `MCP_APP_DIR` | `/opt/mymcp` | Application directory (auto-protected) |
| `MCP_PROTECTED_PATHS` | *(empty)* | Additional protected paths, comma-separated |

### Audit Logging

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_AUDIT_ENABLED` | `false` | Enable audit logging |
| `MCP_AUDIT_LOG_DIR` | `/var/log/mymcp` | Audit log directory (auto-protected) |
| `MCP_AUDIT_MAX_BYTES` | `10485760` | Max audit log file size before rotation (10MB) |
| `MCP_AUDIT_BACKUP_COUNT` | `5` | Number of rotated log files to keep |

### Tool Limits

All limits are configurable via environment variables. Default values work well for most use cases.

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_BASH_MAX_OUTPUT_BYTES` | `102400` | bash stdout/stderr default cap (100KB) |
| `MCP_BASH_MAX_OUTPUT_BYTES_HARD` | `1048576` | bash output hard cap (1MB) |
| `MCP_READ_FILE_DEFAULT_LIMIT` | `2000` | read_file default lines per request |
| `MCP_READ_FILE_MAX_LIMIT` | `50000` | read_file max lines per request |
| `MCP_READ_FILE_MAX_LINE_BYTES` | `32768` | Max bytes per line before truncation (32KB) |
| `MCP_WRITE_FILE_MAX_BYTES` | `10485760` | write_file max size (10MB) |
| `MCP_EDIT_STRING_MAX_BYTES` | `1048576` | edit_file max old/new string size (1MB) |
| `MCP_GLOB_MAX_RESULTS` | `1000` | Max file paths returned by glob |
| `MCP_GREP_DEFAULT_MAX_RESULTS` | `500` | grep default max matches |
| `MCP_GREP_MAX_RESULTS` | `5000` | grep hard max matches |

## Managing Tokens

Use the admin API to create tokens for clients. All admin endpoints require `Authorization: Bearer <MCP_ADMIN_TOKEN>`.

```bash
# Create a read-only token (default)
curl -X POST http://localhost:8765/admin/tokens \
  -H "Authorization: Bearer <ADMIN_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"name": "my-claude-desktop"}'
# → {"token": "tok_abc123...", "name": "my-claude-desktop", "role": "ro"}

# Create a read-write token
curl -X POST http://localhost:8765/admin/tokens \
  -H "Authorization: Bearer <ADMIN_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"name": "my-admin-client", "role": "rw"}'
# → {"token": "tok_def456...", "name": "my-admin-client", "role": "rw"}

# List all tokens
curl http://localhost:8765/admin/tokens \
  -H "Authorization: Bearer <ADMIN_TOKEN>"

# Revoke a token
curl -X DELETE http://localhost:8765/admin/tokens/tok_abc123 \
  -H "Authorization: Bearer <ADMIN_TOKEN>"
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

When enabled (`MCP_AUDIT_ENABLED=true`), all tool invocations are logged to `<MCP_AUDIT_LOG_DIR>/audit.log` in JSON Lines format:

```json
{"ts":"2026-04-10T15:30:22Z","token_name":"my-client","role":"rw","ip":"203.0.113.5","tool":"bash_execute","params":{"command":"apt update"},"result":"ok","duration_ms":1523}
```

Error entries include `error_code` and `error_message`:

```json
{"ts":"2026-04-10T15:31:00Z","token_name":"ro-client","role":"ro","ip":"203.0.113.5","tool":"read_file","params":{"file_path":"/opt/mymcp/main.py"},"result":"error","error_code":"ProtectedPath","error_message":"Access denied: path is within protected directory /opt/mymcp","duration_ms":0}
```

Logs rotate automatically (default 10MB with 5 backups).

### Application Log

Tool errors and warnings are also output to stderr, which is captured by journald when running as a systemd service:

```bash
journalctl -u mymcp -f
```

## Protected Paths

MCP automatically protects its own installation directory and audit log directory from access via file tools (`read_file`, `write_file`, `edit_file`, `glob`, `grep`). This prevents AI clients from reading tokens, modifying server code, or tampering with audit logs.

Add extra protected paths via `MCP_PROTECTED_PATHS=/path/one,/path/two`.

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

# Run load tests (start server first: python main.py)
export MCP_TEST_TOKEN=<your-rw-token>
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
