# Audit Logging & Permission Control Design

**Date:** 2026-04-10
**Scope:** Add audit logging, per-token read/write permissions, protected paths, and remove file transfer endpoints.

## Problem

The MCP server gives AI clients unrestricted root-level access with no audit trail and no way to differentiate read-only vs read-write users. Self-protection of MCP's own files is also missing.

## 1. Token Model Changes

### Role field

Each token gains a `role` field: `"ro"` (read-only) or `"rw"` (read-write).

```json
{
  "tokens": {
    "tok_abc123...": {
      "name": "my-ai-client",
      "role": "rw",
      "created_at": "...",
      "last_used": null,
      "enabled": true
    }
  }
}
```

### Admin API change

`POST /admin/tokens` accepts optional `role` parameter (default `"ro"` — secure by default).

Request: `{"name": "client-name", "role": "rw"}`
Response: `{"token": "tok_...", "name": "client-name", "role": "rw"}`

### Backward compatibility

When loading `tokens.json` from disk, existing tokens without a `role` field default to `"rw"` to avoid breaking current deployments. On next token file save, the field is persisted. Note: this is distinct from the API default — `POST /admin/tokens` defaults new tokens to `"ro"`.

### Admin token

Admin token remains management-only. It cannot call MCP tools (unchanged from current behavior).

## 2. Permission Control

### Tool classification

Hardcoded in `mcp_server.py`:

```python
READ_TOOLS = {"read_file", "glob", "grep"}
WRITE_TOOLS = {"bash_execute", "write_file", "edit_file"}
```

### Enforcement points

**`list_tools()`:** ro users see only READ_TOOLS. rw users see all tools. AI clients never discover tools they cannot use.

**`call_tool()`:** Double-check — if an ro user calls a write tool, return error and log to audit:
```
PermissionError: Tool 'bash_execute' requires 'rw' role
```

## 3. Audit Logging

### Module

New file: `audit.py`

### Log format

JSON Lines (one JSON object per line) written to a rotating log file:

```json
{"ts":"2026-04-10T15:30:22.123Z","token_name":"my-ai-client","role":"rw","ip":"203.0.113.5","tool":"bash_execute","params":{"command":"apt update"},"result":"success","duration_ms":1523}
{"ts":"2026-04-10T15:30:25.456Z","token_name":"monitor-bot","role":"ro","ip":"10.0.0.1","tool":"write_file","params":{"file_path":"/tmp/test"},"result":"denied","reason":"ro_role"}
{"ts":"2026-04-10T15:30:28.789Z","token_name":"my-ai-client","role":"rw","ip":"203.0.113.5","tool":"edit_file","params":{"file_path":"/opt/mymcp/config.py"},"result":"denied","reason":"protected_path"}
```

### Fields

| Field | Description | When |
|-------|-------------|------|
| `ts` | ISO 8601 timestamp | Always |
| `token_name` | Token name (who) | Always |
| `role` | Token role (ro/rw) | Always |
| `ip` | Source IP address | Always |
| `tool` | Tool name or endpoint | Always |
| `params` | Key parameters (command, file_path, pattern) | Always |
| `result` | `success` / `denied` / `error` | Always |
| `reason` | Denial reason | Only when denied |
| `duration_ms` | Execution time | Only on success |

### Rotation

Python `logging.handlers.RotatingFileHandler`: default 10MB per file, 5 backups.

### What is logged

- All `call_tool()` invocations (success, denied, error)
- Permission denials (ro calling write tool)
- Protected path violations

### What is NOT logged

- `list_tools()` calls
- `/health` endpoint
- Admin API calls (token management)

### On/Off switch

Audit logging is controlled by `MCP_AUDIT_ENABLED` config. When disabled, no log file is created and the audit function is a no-op.

## 4. Protected Paths

### Function

`check_protected_path(path)` in `tools/files.py` — resolves `os.path.realpath()` (prevents symlink bypass), checks against protected path list.

### Default protected paths

- `APP_DIR` (e.g., `/opt/mymcp`) — MCP code, .env, tokens.json
- Audit log directory (e.g., `/var/log/mymcp`)

These are always protected, not configurable to remove.

### Additional protected paths

`MCP_PROTECTED_PATHS` environment variable: comma-separated list of additional paths to protect.

### Enforced on these tools

| Tool | Behavior |
|------|----------|
| `read_file` | Reject if path is protected |
| `write_file` | Reject if path is protected |
| `edit_file` | Reject if path is protected |
| `glob` | Filter out protected paths from results |
| `grep` | Filter out protected paths from matches |

### NOT enforced on

`bash_execute` — cannot effectively control; audit log provides accountability.

## 5. Configuration

### New config.py entries

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `MCP_AUDIT_ENABLED` | `false` | Enable audit logging |
| `MCP_AUDIT_LOG_DIR` | `/var/log/mymcp` | Audit log directory |
| `MCP_AUDIT_MAX_BYTES` | `10485760` (10MB) | Max log file size |
| `MCP_AUDIT_BACKUP_COUNT` | `5` | Number of rotated backups |
| `MCP_PROTECTED_PATHS` | (empty) | Additional protected paths, comma-separated |

`APP_DIR` and audit log directory are automatically added to the protected path list.

### deploy/install.sh changes

Interactive mode adds two new prompts:

```
Enable audit logging? (recommended) [Y/n]:
Audit log directory [/var/log/mymcp]:
```

`-y` mode: audit enabled, default directory.

Install script creates log directory with `mkdir -p` and appropriate permissions.

`.env` file includes the new configuration entries.

## 6. File Transfer Endpoints — Removal

### Deleted

- `tools/transfer.py` — entire file
- `/files/upload` endpoint
- `/files/download` endpoint
- `tests/test_transfer.py` — entire file
- `main.py` — remove transfer_router import and include

### Rationale

These HTTP endpoints are not in the MCP tool list and are never discovered or used by AI clients. File operations are fully covered by `read_file`/`write_file` MCP tools.

## 7. Files Changed

### New files

- `audit.py` — audit logging module
- `tests/test_audit.py` — audit log tests
- `tests/test_permissions.py` — ro/rw permission tests
- `tests/test_protected_paths.py` — path protection tests

### Modified files

- `auth.py` — token role field, create_token role parameter, backward compat
- `mcp_server.py` — list_tools/call_tool permission filtering, audit logging calls
- `tools/files.py` — check_protected_path() function, integrate into all file tools
- `config.py` — new audit and path protection config entries
- `main.py` — remove transfer_router, pass token info to MCP context
- `deploy/install.sh` — audit log prompts
- `deploy/install_lib.sh` — if needed
- `README.md` — document audit logging, permissions, protected paths

### Deleted files

- `tools/transfer.py`
- `tests/test_transfer.py`

## Out of Scope

- Token expiration / TTL
- Rate limiting
- IP whitelist per token
- Multi-admin support
- `bash_execute` command filtering
