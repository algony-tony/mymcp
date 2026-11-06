# Linux MCP Server

[![CI](https://github.com/algony-tony/mymcp/actions/workflows/ci.yml/badge.svg)](https://github.com/algony-tony/mymcp/actions/workflows/ci.yml)
[![Go Coverage](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/algony-tony/f5b7d1a23781d63db40ea2e2dcdf71c2/raw/mymcp-go-coverage.json&cacheSeconds=3600)](https://github.com/algony-tony/mymcp/actions/workflows/ci.yml)
[![Recorder Coverage](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/algony-tony/f5b7d1a23781d63db40ea2e2dcdf71c2/raw/mymcp-coverage.json&cacheSeconds=3600)](https://github.com/algony-tony/mymcp/actions/workflows/ci.yml)
[![Mutation Score](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/algony-tony/f5b7d1a23781d63db40ea2e2dcdf71c2/raw/mymcp-mutation.json&cacheSeconds=3600)](https://github.com/algony-tony/mymcp/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

A high-performance, secure Go MCP (Model Context Protocol) server that exposes Linux system control to AI clients (Claude Desktop, Claude Code, Cursor, Gemini CLI, etc.) over Streamable HTTP with Bearer token authentication.

The core server `mymcp` is a single static Go binary. An optional Python sidecar daemon (`mymcp-recorder`) continuously maintains a live LLM-generated host overview.

---

## Features

- **9 MCP Tools**: Shell command execution, safe file operations (`read`, `write`, `edit`), fast file search (`glob`, `grep`), streaming file transfer, and host overview.
- **Context-Free File Transfer**: Upload and download binary or large files directly via signed HTTP streaming tickets — file bytes never enter the LLM context window.
- **Role-Based Token Security**: Issue `ro` (read-only) or `rw` (read-write) Bearer tokens with strict tool filtering.
- **Protected Paths**: Critical server files, tokens, configurations, and audit logs are automatically shielded from tool access.
- **SOC-Compliant Audit Logging**: Complete JSON Lines audit trail with atomic write guarantees (mutations fail safely if logging fails).
- **Stateless Streamable HTTP**: Built on modern Streamable HTTP transport; reconnects seamlessly without persistent session state.
- **Production Observability**: Built-in Prometheus `/metrics` endpoint, OpenTelemetry tracing support, and pre-built Grafana dashboards.

---

## Quick Install

### 1. One-Line Script (Recommended)

Downloads the static binary, runs an interactive configuration wizard, and configures the systemd service:

```bash
curl -fsSL https://github.com/algony-tony/mymcp/releases/latest/download/install.sh | sudo bash
```

### 2. Via pipx

```bash
pipx install algony-mymcp
sudo mymcp init
```

`mymcp init` prompts for listening port, admin token, and creates `/etc/mymcp/.env` along with the systemd service. Add `--yes` to accept all defaults automatically.

### 3. Air-Gapped / Offline Install

Download `mymcp-<version>-offline-bundle.tar.gz` from GitHub Releases:

```bash
tar xzf mymcp-*-offline-bundle.tar.gz
cd mymcp-*-offline-bundle
sudo ./install-offline.sh
```

To upgrade: `pipx upgrade algony-mymcp && sudo systemctl restart mymcp`.

---

## Connecting Clients

### Claude Desktop / Cursor

Add the server to your MCP client configuration (`claude_desktop_config.json` or Cursor Settings):

```json
{
  "mcpServers": {
    "linux-server": {
      "type": "streamableHttp",
      "url": "http://your-server:8765/mcp",
      "headers": {
        "Authorization": "Bearer <YOUR_TOKEN>"
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
  --header "Authorization: Bearer <YOUR_TOKEN>"
```

### Gemini CLI

Configure `gemini` with Streamable HTTP headers:

```json
{
  "mcpServers": {
    "linux-server": {
      "url": "http://your-server:8765/mcp",
      "headers": {
        "Authorization": "Bearer <YOUR_TOKEN>"
      }
    }
  }
}
```

---

## CLI Reference

`mymcp` uses Go-style command flags (`-host`, `-port`).

| Command | Purpose |
|---------|---------|
| `mymcp init [--yes]` | Run interactive setup wizard (generates tokens, `.env`, systemd service) |
| `mymcp doctor` | Run preflight health diagnostics and inspect host environment |
| `mymcp serve [-host H] [-port P]` | Run MCP server in foreground (default `0.0.0.0:8765`) |
| `mymcp token list` | List all active `ro` and `rw` client tokens |
| `mymcp token add [-role ro\|rw] <name>` | Generate and display a new Bearer token |
| `mymcp token revoke <token>` | Revoke a client token |
| `mymcp config example` | Print an example `.env` configuration file |
| `mymcp version` | Print server binary version |

### Managing Tokens

Tokens can be managed directly on the host using the CLI without restarting the server:

```bash
# List all active tokens
sudo mymcp token list

# Create a read-only token for an untrusted client
sudo mymcp token add -role ro claude-reader

# Create a read-write token for system administration
sudo mymcp token add -role rw admin-client

# Revoke a token
sudo mymcp token revoke tok_1234567890abcdef
```

Alternatively, remote clients can use the HTTP Admin API with `Authorization: Bearer <MYMCP_ADMIN_TOKEN>` at `POST/GET/DELETE /admin/tokens`.

---

## MCP Tools Overview

`algony-mymcp` provides 9 dedicated tools. Tools accessible to a client depend on its token role:

| Tool | Role | Description |
|------|------|-------------|
| `bash_execute` | `rw` | Execute shell commands in a stateless subprocess |
| `read_file` | `ro` | Read files with line numbering, offset, and limit pagination |
| `write_file` | `rw` | Atomically create or overwrite files (auto-creates directories) |
| `edit_file` | `rw` | Precise string replacement in existing files |
| `glob` | `ro` | Find matching file paths by glob pattern, sorted by mtime |
| `grep` | `ro` | Search file contents with regex (uses `ripgrep` with Go RE2 fallback) |
| `prepare_upload` | `rw` | Mint signed one-time URL for streaming binary/large file uploads |
| `prepare_download` | `ro` | Mint signed one-time URL for streaming file downloads |
| `server_overview` | `ro` | Retrieve live host status maintained by the recorder sidecar |

👉 **Full Schema & Examples**: See [docs/reference/tools.md](docs/reference/tools.md) for detailed JSON parameters, return structures, and curl transfer examples.

---

## Configuration

Settings are resolved in order: CLI flags → `MYMCP_*` environment variables → `/etc/mymcp/.env`.

### Common Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `MYMCP_HOST` | `0.0.0.0` | Bind IP address |
| `MYMCP_PORT` | `8765` | Listen port |
| `MYMCP_TOKEN_FILE` | `/etc/mymcp/tokens.json` | Token store file path |
| `MYMCP_PROTECTED_PATHS` | *(empty)* | Additional paths protected from file tools (comma-separated) |
| `MYMCP_AUDIT_ENABLED` | `false` | Enable structured audit logging |
| `MYMCP_AUDIT_LOG_DIR` | `/var/log/mymcp` | Directory for `audit.log` (auto-protected) |
| `MYMCP_TRANSFER_MAX_BYTES` | `2147483648` (2 GB) | Maximum file size for streaming transfer tickets |
| `MYMCP_TRANSFER_MAX_TTL_SEC` | `900` | Maximum ticket expiration time in seconds |
| `MYMCP_METRICS_TOKEN` | *(empty)* | Optional Bearer token required for `/metrics` |

👉 **Complete Configuration**: See [.env.example](.env.example) for all tuning knobs including tool memory caps, audit buffer sizes, and recorder options.

---

## Security & Observability

### Security Model
- **Protected Paths**: Server configuration, tokens, and audit directories are strictly read- and write-protected from MCP file tools.
- **Audit Logs**: Every tool invocation records timestamp, caller IP, token role, arguments, and duration to `/var/log/mymcp/audit.log`.
- **Safe Mutation Rule**: In accordance with SOC requirements, any failure to write to the audit log immediately aborts the mutation and returns `500 InternalError`.

### Observability
- **Prometheus Metrics**: Pull runtime metrics directly from `GET /metrics` (protected by `MYMCP_METRICS_TOKEN`).
- **Grafana Dashboards**: Ready-to-import dashboards available under [deploy/grafana/](deploy/grafana/):
  - `mymcp-dashboard.json`: Server performance, tool invocation rates, and error tracking.
  - `mymcp-logs-dashboard.json`: Correlated trace exploration with Loki and Tempo.

👉 **Setup Recipes**: See [docs/reference/observability.md](docs/reference/observability.md) for OTLP push recipes, LGTM Docker Compose, and PromQL alerting rules.

---

## Roadmap

### v3.2 (Near-Term: Polish & Reliability)
- [ ] **Dedicated System Tools**: First-class `systemctl` and `journalctl` tools to avoid raw shell text parsing.
- [ ] **Real-Time Streaming**: Stream stdout/stderr chunks from `bash_execute` in real-time via SSE/WebSocket.
- [ ] **Per-Token ACLs**: Granular path and command whitelist/blacklist scoped to individual client tokens.

### v3.3 (Medium-Term: Architecture Unification)
- [ ] **Pure Go Recorder**: Re-implement the overview recorder sidecar natively in Go, removing the Python runtime requirement entirely.
- [ ] **Container Sandboxing**: Optional rootless Docker/Podman isolation wrapper for `bash_execute`.

### v4.0 (Long-Term: Multi-Platform & Enterprise)
- [ ] **Cross-Platform Support**: Extend core server support to macOS (Darwin) with launchd integration.
- [ ] **Dual Transports**: Full bi-directional WebSocket support alongside Streamable HTTP.
- [ ] **Pluggable Tools**: Dynamic tool provider protocol via Go IPC plugins.

---

## Documentation Index

- [MCP Tools Reference](docs/reference/tools.md) — Comprehensive parameter schemas, return formats, and transfer workflows.
- [Observability Guide](docs/reference/observability.md) — Prometheus metrics, Grafana dashboards, and OTLP setups.
- [Architecture Design](docs/design/architecture.md) — Dual-component Go core + sidecar design and security invariants.
- [File Transfer Design](docs/design/file-transfer.md) — HTTP bypass ticket protocol for large files.
- [Overview Recorder Design](docs/design/overview-recorder.md) — Self-updating host overview architecture.
- [Developer Guide (AGENTS.md)](AGENTS.md) — Git workflow, conventional commits, test execution, and CI conventions.

---

## License

Apache License 2.0. See [LICENSE](LICENSE) for details.
