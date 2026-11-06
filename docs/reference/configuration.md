# Configuration Reference

`algony-mymcp` is configured via environment variables or an environment file.

## Configuration Loading Order

Settings are resolved in the following priority order:
1. Command-line flags (e.g. `-host`, `-port`)
2. `MYMCP_*` environment variables
3. File specified by `-env-file <PATH>`
4. File specified by `MYMCP_ENV_FILE`
5. Standard system location: `/etc/mymcp/.env`
6. Development fallback: `./.env` (current working directory)

---

## Server & Network

| Variable | Default | Description |
|---|---|---|
| `MYMCP_HOST` | `0.0.0.0` | IP address to bind to |
| `MYMCP_PORT` | `8765` | TCP port to listen on |
| `MYMCP_SHUTDOWN_GRACE_SEC` | `5` | Grace period in seconds to wait for running commands on SIGTERM |
| `MYMCP_PUBLIC_BASE_URL` | *(empty)* | Public base URL used to construct ticket URLs when running behind a reverse proxy |

---

## Authentication & Permissions

| Variable | Default | Description |
|---|---|---|
| `MYMCP_ADMIN_TOKEN` | *(auto-generated)* | Secret token for remote `/admin/*` HTTP endpoints |
| `MYMCP_METRICS_TOKEN` | *(empty)* | Optional Bearer token required for `/metrics` (empty allows unauthenticated access) |
| `MYMCP_TOKEN_FILE` | `/etc/mymcp/tokens.json` | Path to the JSON token store file |
| `MYMCP_PROTECTED_PATHS` | *(empty)* | Additional paths protected from MCP file tools (comma-separated) |

---

## Audit Logging

| Variable | Default | Description |
|---|---|---|
| `MYMCP_AUDIT_ENABLED` | `false` | Enable structured audit logging |
| `MYMCP_AUDIT_LOG_DIR` | `/var/log/mymcp` | Directory for `audit.log` (automatically protected) |
| `MYMCP_AUDIT_MAX_BYTES` | `10485760` (10 MB) | Maximum size of an audit log file before rotation |
| `MYMCP_AUDIT_BACKUP_COUNT` | `5` | Number of rotated backup files to retain |
| `MYMCP_AUDIT_OUTPUT_BASH_HEAD_BYTES` | `4096` (4 KB) | Head bytes retained in audit log for bash command output |
| `MYMCP_AUDIT_OUTPUT_BASH_TAIL_BYTES` | `4096` (4 KB) | Tail bytes retained in audit log for bash command output |

---

## Tool Execution & Safety Limits

| Variable | Default | Description |
|---|---|---|
| `MYMCP_BASH_MAX_OUTPUT_BYTES` | `102400` (100 KB) | Default per-stream stdout/stderr limit |
| `MYMCP_BASH_MAX_OUTPUT_BYTES_HARD` | `1048576` (1 MB) | Hard ceiling for `max_output_bytes` parameter |
| `MYMCP_READ_FILE_DEFAULT_LIMIT` | `2000` | Default lines returned per `read_file` call |
| `MYMCP_READ_FILE_MAX_LIMIT` | `50000` | Maximum lines that can be requested per `read_file` call |
| `MYMCP_READ_FILE_MAX_LINE_BYTES` | `32768` (32 KB) | Per-line truncation threshold for `read_file` |
| `MYMCP_WRITE_FILE_MAX_BYTES` | `10485760` (10 MB) | Maximum file size for `write_file` |
| `MYMCP_EDIT_STRING_MAX_BYTES` | `1048576` (1 MB) | Maximum size of `old_string`/`new_string` in `edit_file` |
| `MYMCP_GLOB_MAX_RESULTS` | `1000` | Maximum file matches returned by `glob` |
| `MYMCP_GREP_DEFAULT_MAX_RESULTS` | `500` | Default match limit for `grep` |
| `MYMCP_GREP_MAX_RESULTS` | `5000` | Hard ceiling for `grep` matches |

---

## File Transfer Tickets

| Variable | Default | Description |
|---|---|---|
| `MYMCP_TRANSFER_ENABLED` | `true` | Enable binary file transfer streaming endpoints |
| `MYMCP_TRANSFER_DEFAULT_TTL_SEC` | `300` (5 min) | Default ticket validity period in seconds |
| `MYMCP_TRANSFER_MAX_TTL_SEC` | `900` (15 min) | Maximum allowed ticket expiration time |
| `MYMCP_TRANSFER_MAX_BYTES` | `2147483648` (2 GB) | Maximum file size allowed for streaming upload/download |

---

## Recorder Sidecar (Optional)

These settings apply to the `mymcp-recorder` daemon (`pip install "algony-mymcp[recorder]"`):

| Variable | Default | Description |
|---|---|---|
| `MYMCP_RECORDER_ENABLED` | `false` | Enable the recorder sidecar |
| `MYMCP_RECORDER_DATA_DIR` | `/var/lib/mymcp/recorder` | Directory where `overview.md`, `changelog.md`, and cursor are stored |
| `MYMCP_RECORDER_LLM_PROVIDER` | `anthropic` | LLM provider: `anthropic` or `openai` (compatible) |
| `MYMCP_RECORDER_LLM_MODEL` | *(provider default)* | LLM model override |
| `MYMCP_RECORDER_LLM_API_KEY` | *(unset)* | API key for LLM provider |
| `MYMCP_RECORDER_LLM_BASE_URL` | *(unset)* | Base URL override for OpenAI-compatible providers |
| `MYMCP_RECORDER_LLM_MAX_TOKENS` | `32768` | Output token ceiling for recorder LLM calls |
| `MYMCP_RECORDER_MERGE_INTERVAL_SEC` | `300` (5 min) | Interval between periodic merge cycles |
| `MYMCP_RECORDER_MAX_EVENTS_PER_CYCLE` | `50` | Maximum audit events processed per merge cycle |
| `MYMCP_RECORDER_CIRCUIT_BREAKER_THRESHOLD` | `5` | Consecutive failures before tripping the circuit breaker (0 disables) |
| `MYMCP_RECORDER_BOOTSTRAP_MAX_ITERATIONS` | `200` | Maximum probe iterations during initial host bootstrap |
| `MYMCP_RECORDER_BOOTSTRAP_TOKEN_BUDGET` | `10000000` | LLM token budget for bootstrap exploration |
| `MYMCP_RECORDER_BOOTSTRAP_PROBE_TIMEOUT_SEC` | `30` | Timeout in seconds for individual bootstrap probes |
| `MYMCP_RECORDER_BOOTSTRAP_RETRY_INTERVAL_SEC` | `3600` (1 hr) | Retry interval if initial bootstrap fails |
