# Linux MCP Server

A Python MCP server that exposes full Linux system control to AI clients (Claude Desktop, Claude Code, Cursor, etc.) over Streamable HTTP.

## Features

- **6 MCP tools**: `bash_execute`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`
- **Per-token permissions**: read-only (`ro`) or read-write (`rw`) roles
- **Audit logging**: JSON Lines audit trail of all tool invocations
- **Protected paths**: MCP's own files are protected from tool access
- **Multi-user auth**: Bearer token authentication with per-token management
- **Admin API**: create/revoke tokens without restarting the server
- **Streamable HTTP transport**: modern MCP protocol with session management

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

Edit `/opt/mymcp/.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_ADMIN_TOKEN` | *(required)* | Admin token for managing user tokens |
| `MCP_HOST` | `0.0.0.0` | Bind address |
| `MCP_PORT` | `8765` | Listen port |
| `MCP_TOKEN_FILE` | `/opt/mymcp/tokens.json` | Token store path |
| `MCP_APP_DIR` | `/opt/mymcp` | Application directory (auto-protected) |
| `MCP_AUDIT_ENABLED` | `false` | Enable audit logging |
| `MCP_AUDIT_LOG_DIR` | `/var/log/mymcp` | Audit log directory (auto-protected) |
| `MCP_PROTECTED_PATHS` | *(empty)* | Additional protected paths, comma-separated |

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

## Audit Logging

When enabled (`MCP_AUDIT_ENABLED=true`), all tool invocations are logged to `<MCP_AUDIT_LOG_DIR>/audit.log` in JSON Lines format:

```json
{"ts":"2026-04-10T15:30:22Z","token_name":"my-client","role":"rw","ip":"203.0.113.5","tool":"bash_execute","params":{"command":"apt update"},"result":"success","duration_ms":1523}
```

Logs rotate automatically at 10MB with 5 backups.

## Protected Paths

MCP automatically protects its own installation directory and audit log directory from access via file tools (`read_file`, `write_file`, `edit_file`, `glob`, `grep`). This prevents AI clients from reading tokens, modifying server code, or tampering with audit logs.

Add extra protected paths via `MCP_PROTECTED_PATHS=/path/one,/path/two`.

Note: `bash_execute` is not subject to path protection — use `ro` tokens for untrusted clients.

## Security Note

This server grants system access to AI clients. Security measures:

- **Permissions**: New tokens default to `ro` (read-only). Only grant `rw` to trusted clients.
- **Audit**: Enable audit logging to track all tool invocations.
- **Protected paths**: Server files are automatically protected from tool access.
- **Network**: Run behind a firewall and consider TLS (e.g. via nginx reverse proxy).
