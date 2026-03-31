# Linux MCP Server

A Python MCP server that exposes full Linux system control to AI clients (Claude Desktop, Claude Code, etc.) over HTTP/SSE. Modeled after Claude Code's built-in tool set.

## Features

- **6 MCP tools**: `bash_execute`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`
- **File transfer**: large file upload/download via dedicated HTTP endpoints
- **Multi-user auth**: Bearer token authentication with per-token management
- **Admin API**: create/revoke tokens without restarting the server

## Requirements

- Python 3.11+
- `ripgrep` (`rg`) installed on the server for `grep` tool

## Installation

```bash
git clone <repo>
cd mymcp
pip install -r requirements.txt
```

## Configuration

Copy `.env.example` to `.env` and edit:

```bash
cp .env.example .env
```

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_HOST` | `0.0.0.0` | Bind address |
| `MCP_PORT` | `8765` | Listen port |
| `MCP_TOKEN_FILE` | `./tokens.json` | Token store path |
| `MCP_ADMIN_TOKEN` | *(required)* | Admin token for managing user tokens |

## Starting the Server

```bash
# Set required admin token
export MCP_ADMIN_TOKEN=adm_your_secret_here

# Start
python main.py
```

On first start, `tokens.json` is created automatically with the admin token written in.

## Managing Tokens

Use the admin API to create tokens for clients. All admin endpoints require `Authorization: Bearer <MCP_ADMIN_TOKEN>`.

```bash
# Create a token
curl -X POST http://localhost:8765/admin/tokens \
  -H "Authorization: Bearer adm_your_secret_here" \
  -H "Content-Type: application/json" \
  -d '{"name": "my-claude-desktop"}'
# → {"token": "tok_abc123...", "name": "my-claude-desktop"}

# List all tokens
curl http://localhost:8765/admin/tokens \
  -H "Authorization: Bearer adm_your_secret_here"

# Revoke a token
curl -X DELETE http://localhost:8765/admin/tokens/tok_abc123 \
  -H "Authorization: Bearer adm_your_secret_here"
```

## Connecting Clients

### Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "linux-server": {
      "url": "http://your-server:8765/sse",
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
  --url http://your-server:8765/sse \
  --header "Authorization: Bearer tok_abc123"
```

## File Transfer

For files larger than 10MB, use the HTTP file transfer endpoints directly (same Bearer token).

### Upload a file

```bash
curl -X POST http://your-server:8765/files/upload \
  -H "Authorization: Bearer tok_abc123" \
  -F "file=@/local/path/backup.tar.gz" \
  -F "dest_path=/data/backup.tar.gz"
# → {"path": "/data/backup.tar.gz", "size": 1073741824}
```

### Download a file

```bash
curl http://your-server:8765/files/download?path=/data/backup.tar.gz \
  -H "Authorization: Bearer tok_abc123" \
  -o backup.tar.gz
```

## MCP Tools Reference

| Tool | Description |
|------|-------------|
| `bash_execute` | Run any shell command. Args: `command`, `timeout` (default 30s), `working_dir`, `max_output_bytes` |
| `read_file` | Read file with line numbers. Args: `file_path`, `offset`, `limit` (default 2000 lines) |
| `write_file` | Create or overwrite a file (max 10MB; use upload for larger). Args: `file_path`, `content` |
| `edit_file` | Replace a string in a file. Args: `file_path`, `old_string`, `new_string`, `replace_all` |
| `glob` | Find files by pattern. Args: `pattern`, `path` (default `/`) |
| `grep` | Search file contents. Args: `pattern`, `path`, `glob`, `output_mode`, `context_lines`, `max_results` |

## Security Note

This server grants **unrestricted root-equivalent access** to the Linux system. Only issue tokens to trusted clients. Run behind a firewall and consider using TLS (e.g. via nginx reverse proxy) in production.
