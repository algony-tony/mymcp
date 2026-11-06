# Linux MCP Server

[![CI](https://github.com/algony-tony/mymcp/actions/workflows/ci.yml/badge.svg)](https://github.com/algony-tony/mymcp/actions/workflows/ci.yml)
[![Go Coverage](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/algony-tony/f5b7d1a23781d63db40ea2e2dcdf71c2/raw/mymcp-go-coverage.json&cacheSeconds=3600)](https://github.com/algony-tony/mymcp/actions/workflows/ci.yml)
[![Recorder Coverage](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/algony-tony/f5b7d1a23781d63db40ea2e2dcdf71c2/raw/mymcp-coverage.json&cacheSeconds=3600)](https://github.com/algony-tony/mymcp/actions/workflows/ci.yml)
[![Mutation Score](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/algony-tony/f5b7d1a23781d63db40ea2e2dcdf71c2/raw/mymcp-mutation.json&cacheSeconds=3600)](https://github.com/algony-tony/mymcp/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](https://github.com/algony-tony/mymcp/blob/master/LICENSE)

A high-performance, secure Go MCP (Model Context Protocol) server that exposes Linux system control to AI clients (Claude Desktop, Claude Code, Cursor, Gemini CLI, etc.) over Streamable HTTP with Bearer token authentication.

The core server `mymcp` is a single static Go binary. An optional Python sidecar daemon (`mymcp-recorder`) continuously maintains a live LLM-generated host overview.

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

Configure your MCP client (Claude Desktop, Cursor, or any Streamable HTTP MCP client) using standard Streamable HTTP transport:

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

👉 **Full Schema & Examples**: See [MCP Tools Reference](https://github.com/algony-tony/mymcp/blob/master/docs/reference/tools.md) for detailed JSON parameters, return structures, and curl transfer examples.

---

## Configuration

The server reads configuration from `/etc/mymcp/.env` (or environment variables). All options, default values, and explanations are documented directly inside [.env.example](https://github.com/algony-tony/mymcp/blob/master/.env.example) and the [Configuration Reference](https://github.com/algony-tony/mymcp/blob/master/docs/reference/configuration.md).

---

## Documentation Index

- [MCP Tools Reference](https://github.com/algony-tony/mymcp/blob/master/docs/reference/tools.md) — Comprehensive parameter schemas, return formats, and transfer workflows.
- [CLI Reference](https://github.com/algony-tony/mymcp/blob/master/docs/reference/cli.md) — Server command-line management and token administration (`mymcp token`).
- [Configuration Reference](https://github.com/algony-tony/mymcp/blob/master/docs/reference/configuration.md) — Environment variables, safety caps, and default values.
- [Observability Guide](https://github.com/algony-tony/mymcp/blob/master/docs/reference/observability.md) — Prometheus metrics, Grafana dashboards, and OTLP setups.
- [Architecture Design](https://github.com/algony-tony/mymcp/blob/master/docs/design/architecture.md) — Dual-component Go core + sidecar design and security invariants.
- [File Transfer Design](https://github.com/algony-tony/mymcp/blob/master/docs/design/file-transfer.md) — HTTP bypass ticket protocol for large files.
- [Overview Recorder Design](https://github.com/algony-tony/mymcp/blob/master/docs/design/overview-recorder.md) — Self-updating host overview architecture.
- [Developer Guide (AGENTS.md)](https://github.com/algony-tony/mymcp/blob/master/AGENTS.md) — Git workflow, conventional commits, test execution, and CI conventions.

---

## License

Apache License 2.0. See [LICENSE](https://github.com/algony-tony/mymcp/blob/master/LICENSE) for details.
