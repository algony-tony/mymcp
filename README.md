# Linux MCP Server

A Python MCP server that exposes full Linux system control to AI clients (Claude Desktop, Claude Code, Cursor, etc.) over Streamable HTTP.

## Features

- **6 MCP tools**: `bash_execute`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`
- **File transfer**: large file upload/download via dedicated HTTP endpoints
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

## Managing Tokens

Use the admin API to create tokens for clients. All admin endpoints require `Authorization: Bearer <MCP_ADMIN_TOKEN>`.

```bash
# Create a token
curl -X POST http://localhost:8765/admin/tokens \
  -H "Authorization: Bearer <ADMIN_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"name": "my-claude-desktop"}'
# → {"token": "tok_abc123...", "name": "my-claude-desktop"}

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

## File Transfer

For files larger than 10MB, use the HTTP file transfer endpoints directly (same Bearer token).

### Upload

```bash
curl -X POST http://your-server:8765/files/upload \
  -H "Authorization: Bearer tok_abc123" \
  -F "file=@/local/path/backup.tar.gz" \
  -F "dest_path=/data/backup.tar.gz"
```

### Download

```bash
curl http://your-server:8765/files/download?path=/data/backup.tar.gz \
  -H "Authorization: Bearer tok_abc123" \
  -o backup.tar.gz
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `bash_execute` | Run any shell command. Args: `command`, `timeout` (default 30s), `working_dir`, `max_output_bytes` |
| `read_file` | Read file with line numbers. Args: `file_path`, `offset`, `limit` (default 2000 lines) |
| `write_file` | Create or overwrite a file (max 10MB; use upload for larger). Args: `file_path`, `content` |
| `edit_file` | Replace a string in a file. Args: `file_path`, `old_string`, `new_string`, `replace_all` |
| `glob` | Find files by pattern. Args: `pattern`, `path` (default `/`) |
| `grep` | Search file contents with regex. Args: `pattern`, `path`, `glob`, `output_mode`, `context_lines`, `max_results` |

## Security Note

This server grants **unrestricted root-equivalent access** to the Linux system. Only issue tokens to trusted clients. Run behind a firewall and consider using TLS (e.g. via nginx reverse proxy) in production.
