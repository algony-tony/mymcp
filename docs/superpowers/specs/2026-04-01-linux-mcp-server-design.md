# Linux MCP Server — Design Spec

**Date:** 2026-04-01
**Status:** Approved

---

## Overview

A Python-based MCP server that exposes full Linux system control to AI clients (Claude Desktop, Claude Code, etc.) over HTTP/SSE. Designed to give an AI model the same filesystem and shell capabilities as Claude Code's built-in tools, but targeting a remote Linux server.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                  Linux MCP Server (Python)               │
│                                                         │
│  ┌─────────────────┐    ┌──────────────────────────┐   │
│  │   MCP over SSE  │    │   File Transfer HTTP     │   │
│  │   /sse          │    │   /files/upload  (POST)  │   │
│  │   /messages     │    │   /files/download (GET)  │   │
│  └────────┬────────┘    └──────────┬───────────────┘   │
│           │                        │                    │
│  ┌────────▼────────────────────────▼───────────────┐   │
│  │            Auth Middleware (Bearer Token)        │   │
│  └────────────────────┬────────────────────────────┘   │
│                       │                                 │
│  ┌────────────────────▼────────────────────────────┐   │
│  │                   MCP Tools                     │   │
│  │  bash_execute  read_file  write_file            │   │
│  │  edit_file     glob       grep                  │   │
│  └─────────────────────────────────────────────────┘   │
│                                                         │
│  ┌──────────────────────────────────────────────────┐  │
│  │  Token Store (tokens.json)  +  Admin API         │  │
│  │  /admin/tokens  CRUD  (admin token protected)    │  │
│  └──────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

**Tech stack:** FastAPI + `mcp` (official Python SDK) + `uvicorn`

- FastAPI handles HTTP routing (file transfer, Admin API)
- MCP Python SDK's `SseServerTransport` mounts on FastAPI routes
- Auth Middleware intercepts all routes, validates Bearer Token

---

## Authentication

**Scheme:** Static Bearer Token, multi-user, stored in `tokens.json`

### Token Store Format

```json
{
  "tokens": {
    "tok_abc123": {
      "name": "my-claude-desktop",
      "created_at": "2026-04-01T10:00:00Z",
      "last_used": "2026-04-01T12:00:00Z",
      "enabled": true
    }
  },
  "admin_token": "adm_super_secret"
}
```

### Auth Flow

```
Client → Authorization: Bearer tok_abc123
         ↓
         Middleware checks tokens.json
         ↓
    ┌────┴────┐
  found &    not found or
  enabled    disabled
    ↓            ↓
  pass        401 Unauthorized
  update
  last_used
```

### Admin API

Protected by `admin_token` (separate from user tokens):

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/admin/tokens` | Create token (body: `{"name": "..."}`) |
| `DELETE` | `/admin/tokens/{token}` | Revoke token |
| `GET` | `/admin/tokens` | List all tokens |

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_HOST` | `0.0.0.0` | Bind address |
| `MCP_PORT` | `8765` | Listen port |
| `MCP_TOKEN_FILE` | `./tokens.json` | Token store path |
| `MCP_ADMIN_TOKEN` | *(required)* | Admin token, written to token file on first start |

---

## MCP Tools

### `bash_execute`

Execute any shell command in a subprocess (stateless — no persistent shell session).

**Input:**
| Field | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `command` | `str` | yes | — | Shell command, e.g. `"ls -la /tmp"` |
| `timeout` | `int` | no | `30` | Seconds, max `600` |
| `working_dir` | `str` | no | `"/"` | Absolute path |
| `max_output_bytes` | `int` | no | `102400` | Max stdout/stderr each, max `1048576` |

**Output:**
```json
{
  "stdout": "...",
  "stderr": "...",
  "exit_code": 0,
  "timed_out": false
}
```

Stdout and stderr are each independently truncated at `max_output_bytes`. Truncated output appends `[TRUNCATED: total Xbytes, showing first Y bytes]`.

---

### `read_file`

Read a file with line numbers, supporting pagination for large files.

**Input:**
| Field | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `file_path` | `str` | yes | — | Absolute path |
| `offset` | `int` | no | `1` | Start line (1-based) |
| `limit` | `int` | no | `2000` | Lines to read, max `10000` |

**Output:**
```json
{
  "content": "   1\tline one\n   2\tline two\n",
  "total_lines": 5000,
  "truncated": true
}
```

Lines longer than 4096 bytes are truncated with `[LINE TRUNCATED]`.

---

### `write_file`

Create or overwrite a file. For files larger than 10MB use the `/files/upload` endpoint instead.

**Input:**
| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `file_path` | `str` | yes | Absolute path |
| `content` | `str` | yes | Max 10MB |

**Output:**
```json
{ "success": true, "bytes_written": 1234 }
```

---

### `edit_file`

Targeted string replacement in an existing file. `old_string` must be unique in the file unless `replace_all` is true.

**Input:**
| Field | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `file_path` | `str` | yes | — | Absolute path |
| `old_string` | `str` | yes | — | Max 1MB |
| `new_string` | `str` | yes | — | Max 1MB |
| `replace_all` | `bool` | no | `false` | Replace every occurrence |

**Output:**
```json
{ "success": true, "replacements": 1 }
```

Fails with an error if `old_string` appears more than once and `replace_all` is false.

---

### `glob`

Find files by glob pattern.

**Input:**
| Field | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `pattern` | `str` | yes | — | e.g. `"**/*.py"` |
| `path` | `str` | no | `"/"` | Root directory to search from |

**Output:**
```json
{ "files": ["/etc/nginx/nginx.conf", "..."], "count": 42 }
```

Results capped at 1000 files, sorted by modification time descending.

---

### `grep`

Search file contents with regular expressions. Uses `ripgrep` (`rg`) if available on PATH, falls back to Python's `re` + `os.walk` otherwise. Install `ripgrep` for best performance on large trees.

**Input:**
| Field | Type | Required | Default | Notes |
|-------|------|----------|---------|-------|
| `pattern` | `str` | yes | — | Regex, e.g. `"error.*timeout"` |
| `path` | `str` | no | `"/"` | File or directory |
| `glob` | `str` | no | — | File filter, e.g. `"*.log"` |
| `output_mode` | `str` | no | `"content"` | `"content"` \| `"files"` \| `"count"` |
| `context_lines` | `int` | no | `0` | Lines of context around each match |
| `max_results` | `int` | no | `250` | Max matches, up to `5000` |
| `case_insensitive` | `bool` | no | `false` | |

**Output:**
```json
{ "results": "path/to/file.log:42:error connection timeout\n...", "match_count": 17 }
```

Truncated results append `[TRUNCATED: N more matches not shown]`.

---

## File Transfer HTTP Endpoints

Large files (>10MB) use dedicated HTTP endpoints authenticated with the same Bearer token.

### Upload

```
POST /files/upload
Authorization: Bearer <token>
Content-Type: multipart/form-data

Fields:
  file      — binary file data
  dest_path — absolute server path, e.g. /data/backup.tar.gz

Response 200:
  { "path": "/data/backup.tar.gz", "size": 1073741824 }

Response 400:
  { "error": "dest_path is required" }
```

### Download

```
GET /files/download?path=/data/backup.tar.gz
Authorization: Bearer <token>

Response 200:
  Content-Type: application/octet-stream
  Content-Disposition: attachment; filename="backup.tar.gz"
  [streaming file body]

Response 404:
  { "error": "File not found: /data/backup.tar.gz" }
```

---

## Error Handling

All MCP tools return structured errors instead of raising exceptions, so the AI can interpret and self-correct:

```json
{
  "success": false,
  "error": "PermissionError",
  "message": "Permission denied: /etc/shadow",
  "suggestion": "Try running with sudo via bash_execute"
}
```

Common error types: `FileNotFoundError`, `PermissionError`, `TimeoutError`, `OutputTruncated` (non-fatal), `FileTooLarge`.

---

## Output Size Limits (all configurable in `config.py`)

| Tool | Limit | Behavior on Exceed |
|------|-------|--------------------|
| `bash_execute` stdout/stderr | 100KB each (max 1MB) | Truncate + note |
| `read_file` lines | 2000 default, 10000 max | `truncated: true` + pagination |
| `read_file` line length | 4096 bytes | `[LINE TRUNCATED]` |
| `write_file` content | 10MB | Error: use upload endpoint |
| `edit_file` old/new string | 1MB each | Error |
| `glob` results | 1000 files | Truncate |
| `grep` matches | 250 default, 5000 max | Truncate + note |

---

## Project Structure

```
mymcp/
├── main.py                 # FastAPI app entry, route registration
├── config.py               # Constants and env var configuration
├── auth.py                 # Token middleware + Admin API routes
├── tools/
│   ├── __init__.py
│   ├── bash.py             # bash_execute
│   ├── files.py            # read_file, write_file, edit_file, glob, grep
│   └── transfer.py         # /files/upload, /files/download HTTP endpoints
├── mcp_server.py           # MCP Server, tool registration, SSE transport
├── tokens.json             # Runtime generated, git ignored
├── .gitignore              # Ignores tokens.json, .env, __pycache__
├── requirements.txt
├── .env.example
└── README.md
```
