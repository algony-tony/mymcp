# MCP Tools Reference

This document provides complete parameter schemas, return formats, errors, and behavioral notes for all 9 MCP tools provided by `algony-mymcp`.

---

## Permission Model

| Tool | Permission | Description |
|------|------------|-------------|
| [`bash_execute`](#bash_execute-rw) | `rw` | Run shell commands in a stateless subprocess |
| [`read_file`](#read_file-ro) | `ro` | Read file contents with line numbering and pagination |
| [`write_file`](#write_file-rw) | `rw` | Create or overwrite files atomically |
| [`edit_file`](#edit_file-rw) | `rw` | Replace specific text in an existing file |
| [`glob`](#glob-ro) | `ro` | Find matching file paths with glob patterns |
| [`grep`](#grep-ro) | `ro` | Search file contents with regular expressions |
| [`prepare_upload`](#prepare_upload-rw-and-prepare_download-ro) | `rw` | Mint signed one-time URLs for streaming file uploads |
| [`prepare_download`](#prepare_upload-rw-and-prepare_download-ro) | `ro` | Mint signed one-time URLs for streaming file downloads |
| [`server_overview`](#server_overview-ro) | `ro` | Return LLM-maintained host state overview (recorder sidecar) |

Tokens with `ro` (read-only) permission can access: `read_file`, `glob`, `grep`, `prepare_download`, and `server_overview`. Tokens with `rw` (read-write) permission have access to all tools.

---

## Detailed Tool Specifications

### `bash_execute` (`rw`)

Runs a shell command in a fresh subprocess. Execution is stateless: environment variables, directory changes, shell functions, and exports do not persist between invocations.

**Parameters:**

| Field | Type | Required | Default | Description / Constraints |
|---|---|---|---|---|
| `command` | string | yes | – | Shell command to execute |
| `timeout` | integer | no | `30` | Execution timeout in seconds (clamped to `1..600`) |
| `working_dir` | string | no | `/` | Working directory for the command |
| `max_output_bytes` | integer | no | `102400` | Per-stream stdout/stderr output cap in bytes (hard max `1048576`) |

**Returns:**
- `stdout` (string): Standard output
- `stderr` (string): Standard error
- `exit_code` (integer): Process exit status code
- `timed_out` (boolean): Whether execution exceeded the timeout

**Example:**
```json
{
  "command": "uname -a",
  "timeout": 10,
  "working_dir": "/tmp"
}
```

---

### `read_file` (`ro`)

Reads text files with line numbering and support for paginated reads.

**Parameters:**

| Field | Type | Required | Default | Description / Constraints |
|---|---|---|---|---|
| `file_path` | string | yes | – | Absolute file path |
| `offset` | integer | no | `1` | 1-based start line number |
| `limit` | integer | no | `2000` | Number of lines to return (runtime maximum `50000`) |

**Returns:**
- `content` (string): File content with line numbers
- `total_lines` (integer): Total line count of the file
- `truncated` (boolean): Whether output was truncated due to limits

**Common Errors:**
- `FileNotFoundError`: Target file does not exist
- `IsADirectoryError`: Target path is a directory
- `PermissionError`: File read permission denied by OS
- `ProtectedPath`: Target path falls inside server-protected directories

**Example:**
```json
{
  "file_path": "/var/log/syslog",
  "offset": 1,
  "limit": 200
}
```

---

### `write_file` (`rw`)

Creates a new file or completely overwrites an existing file. Missing parent directories are created automatically.

**Parameters:**

| Field | Type | Required | Default | Description / Constraints |
|---|---|---|---|---|
| `file_path` | string | yes | – | Absolute file path |
| `content` | string | yes | – | Complete file contents (max `10485760` bytes / 10 MB) |

**Notes:**
- Automatically creates parent directories if they don't exist.
- Protected paths are rejected with `ProtectedPath`.

**Example:**
```json
{
  "file_path": "/tmp/hello.txt",
  "content": "hello from mymcp\n"
}
```

---

### `edit_file` (`rw`)

Performs targeted in-place text replacement in an existing file.

**Parameters:**

| Field | Type | Required | Default | Description / Constraints |
|---|---|---|---|---|
| `file_path` | string | yes | – | Absolute file path |
| `old_string` | string | yes | – | Text to search for (max `1048576` bytes) |
| `new_string` | string | yes | – | Replacement text (max `1048576` bytes) |
| `replace_all` | boolean | no | `false` | If false, `old_string` must appear exactly once in the file |

**Example:**
```json
{
  "file_path": "/tmp/app.conf",
  "old_string": "PORT=8000",
  "new_string": "PORT=8765"
}
```

---

### `glob` (`ro`)

Searches for files matching a glob pattern under a root directory. Results are sorted by modification time descending.

**Parameters:**

| Field | Type | Required | Default | Description / Constraints |
|---|---|---|---|---|
| `pattern` | string | yes | – | Glob pattern (e.g., `**/*.py`, `*.log`) |
| `path` | string | no | `/` | Root directory for search |

**Runtime Limits:** Maximum 1,000 matches returned.

**Example:**
```json
{
  "pattern": "**/*.log",
  "path": "/var/log"
}
```

---

### `grep` (`ro`)

Searches file contents using regular expressions. Uses `ripgrep` (`rg`) if available on the system; otherwise falls back to a Go-native RE2 regex implementation.

**Parameters:**

| Field | Type | Required | Default | Description / Constraints |
|---|---|---|---|---|
| `pattern` | string | yes | – | Regex pattern |
| `path` | string | no | `/` | Target file or directory |
| `glob` | string | no | none | Filename filter (e.g., `*.go`, `*.conf`) |
| `output_mode` | string | no | `content` | Output format: `content`, `files`, or `count` |
| `context_lines` | integer | no | `0` | Number of context lines before and after match |
| `max_results` | integer | no | `500` | Match limit (runtime hard cap `5000`) |
| `case_insensitive` | boolean | no | `false` | Case-insensitive matching |

**Example:**
```json
{
  "pattern": "Authorization",
  "path": "/etc",
  "glob": "*.conf",
  "output_mode": "content",
  "context_lines": 2
}
```

---

### `prepare_upload` (`rw`) and `prepare_download` (`ro`)

Optimized transfer mechanism for **binary files** or files **larger than 10 MB**. The MCP tool returns a short-lived ticket URL; file bytes stream directly via HTTP between the client shell and the server without entering the LLM context window.

**Workflow:**

1. **Mint Ticket**:
   - Client calls `prepare_upload(dest_path="/tmp/pkg.tar.gz", max_bytes=52428800)`
   - Server returns JSON: `{"url": "...", "method": "PUT", "expires_in": 300, "curl_example": "curl -fsS -T /local/path 'http://server/files/raw/<ticket>'"}`
2. **Execute Transfer**:
   - The user or client executes the curl command on the local shell.
   - Bytes stream directly to server temp storage and are atomically placed upon successful completion.
3. **Download Mirror**:
   - `prepare_download(source_path="/var/log/app.log")` returns a signed `GET` URL for `curl -fsS 'http://server/files/raw/<ticket>' -o local.log`.

**Security Properties:**
- Tickets are **single-use**, **path-scoped**, **byte-bounded**, and expire after `expires_in` seconds (default 300 s, max 900 s).
- Both minting and redemption enforce `check_protected_path` and write audit log records.
- See [docs/design/file-transfer.md](../design/file-transfer.md) for architecture details.

---

### `server_overview` (`ro`)

Returns the live overview of host packages, services, and recent system mutations maintained by the `mymcp-recorder` sidecar.

**Behavior:**
- Takes no parameters.
- If `overview.md` is absent, returns `RecorderDisabled` pointing to `systemctl status mymcp-recorder`.
- On success, returns `overview` text with metadata fields:
  - `last_updated`: RFC3339 timestamp of last merge.
  - `pending_events`: Unprocessed mutating audit events count.
  - `stale`: Boolean flag indicating if backlog exists and overview is older than `2 × MYMCP_RECORDER_MERGE_INTERVAL_SEC`.
- When stale, the text is prefixed with a warning banner indicating that host state has changed since last update.
- See [docs/design/overview-recorder.md](../design/overview-recorder.md) for sidecar details.

---

## General Security & Safety Rules

1. **Path Protection**:
   All file tools (`read_file`, `write_file`, `edit_file`, `glob`, `grep`, `prepare_upload`, `prepare_download`) enforce strict boundary checks against server tokens, configuration, and audit logs.
2. **Bash Execution Boundary**:
   `bash_execute` runs as the server process user and is **not** restricted by path protection. Restrict untrusted callers with `ro` tokens.
3. **Atomic Operations**:
   `write_file` and file upload ticket redemptions write to a temporary file before renaming, preventing partial-write corruption.
