# File Transfer Design

Date: 2026-05-04
Status: Draft

## Background

The current mymcp server exposes file I/O via three MCP tools: `read_file`,
`write_file`, and `edit_file`. These are line- and string-oriented and
designed for the LLM to inspect or modify text content. They have these
limits today (`src/mymcp/config.py`):

- `read_file`: 2000 lines/call default (max 50000), each line truncated to 32 KB
- `write_file`: 10 MB total bytes hard cap
- `edit_file`: 1 MB per `old_string` / `new_string`

Three painful gaps emerge in real use:

1. **Binary files** (`.deb`, `.tar.gz`, images, executables) cannot be
   transferred at all — the existing tools are text-oriented and UTF-8
   decode breaks on raw bytes.
2. **Large files** (>10 MB) hit `write_file`'s cap. Even when the cap is
   raised, the bytes have to flow through MCP tool results, which means they
   land in the LLM's conversation context — wasteful and quickly exhausts
   the context window.
3. **Local ↔ remote transfer** has no first-class support. A concrete user
   scenario: the server cannot reach a public package mirror due to network
   policy, the user has the `.deb` locally, and currently there is no clean
   way to push it to the server for installation.

The goal is to support binary and large-file transfer **without** routing
the file bytes through MCP tool results (and therefore the LLM context),
while keeping the surface area small — at most a handful of new endpoints
and tools.

## Non-Goals

- Replacing `read_file` / `write_file` for text editing. Those remain the
  right tool for files <10 MB that the LLM needs to see or modify.
- Resumable / chunked uploads. v1 is single-request PUT/GET.
- Server-side download from arbitrary URLs as a dedicated tool. The
  existing `bash_execute` already covers this (`curl -o /path URL`).
- A web UI for file transfer.

## Architecture

### High-level flow

The MCP server (FastAPI app) gains two **bypass HTTP endpoints** that
stream raw file bytes directly between the MCP client's local shell and
the server's filesystem. These endpoints are siblings of the MCP
`/mcp` route on the same FastAPI app, sharing process, configuration,
audit log, and protected-path enforcement.

The LLM never sees file bytes. Instead, it calls one of two new MCP tools
(`prepare_upload` / `prepare_download`) that return a **one-time signed
URL** plus a ready-to-use `curl` command. The LLM then dispatches the
actual byte transfer to the MCP client's local shell.

```
┌──────────────────────┐                   ┌──────────────────────┐
│   MCP Client (LLM)   │                   │   mymcp server       │
│                      │ ─── prepare_X ──▶ │   (FastAPI)          │
│                      │ ◀── ticket+URL ── │                      │
│                      │                   │                      │
│   local shell:       │                   │   /files/raw/{tkt}   │
│   curl -T file URL   │ ════ raw bytes ══▶│   PUT or GET         │
│   curl URL -o file   │ ◀═══ raw bytes ═══│   (no MCP, no LLM)   │
└──────────────────────┘                   └──────────────────────┘
```

Bytes flow on the dashed double-line; only metadata flows through MCP.

### Why bypass endpoints, not MCP tools

MCP tool results are returned to the LLM and consume context tokens. A
50 MB binary base64-encoded into a tool result would consume ~67 MB of
context — nonsensical when the LLM has no need to see those bytes. The
bypass endpoint is a regular HTTP route on the same FastAPI app: same
authentication story, same audit logging, but completely outside the MCP
protocol.

### Why one-time signed URLs

The MCP client already holds a long-lived Bearer token (admin/rw/ro). We
deliberately do **not** hand that token to whatever shell command runs
the curl: shells leave history, processes leak via `/proc/*/cmdline`,
and a long-lived token in those places is a real hazard. Instead the
prepare tool mints an ephemeral, single-use, path-scoped, byte-bounded
ticket with a 5-minute TTL. Worst case if the ticket leaks: one
upload/download of one already-known path within five minutes.

## Components

### 1. New HTTP endpoints (FastAPI bypass routes)

Mounted on the same app as `/mcp`, behind the same `McpAuthMiddleware`?
**No.** These endpoints authenticate via the **ticket in the URL path**,
not via Bearer token, so the middleware should skip them. Reasoning: we
want the curl command to be self-contained (just `curl -T file URL`),
without needing the long-lived Bearer header.

#### `PUT /files/raw/{ticket}`

- **Request body:** raw file bytes (any `Content-Type`; treated as
  `application/octet-stream`)
- **Behavior:**
  1. Look up `ticket` in the in-memory ticket store (see §3).
  2. If missing/expired/wrong-op/already-consumed → 4xx with hint JSON.
  3. Stream the request body to a temp file alongside the destination
     (same directory, prefix `.mymcp-upload-`), enforcing
     `max_bytes`. If the cap is hit mid-stream, abort, delete temp,
     return 413.
  4. On `Content-Length` exceeding `max_bytes` upfront, return 413
     before reading body.
  5. After successful write, `os.replace()` temp → final path
     (atomic on POSIX).
  6. Mark ticket consumed, append audit entry.
- **Response:**
  ```json
  {"ok": true, "path": "/tmp/foo.deb", "bytes_written": 12345678}
  ```
- **Error response shape:**
  ```json
  {"ok": false, "error": "ticket_expired",
   "hint": "Call prepare_upload again to mint a fresh URL."}
  ```

#### `GET /files/raw/{ticket}`

- **Behavior:**
  1. Look up `ticket`. If missing/expired/wrong-op/already-consumed → 4xx.
  2. Open `src_path` for reading. If file is missing or is a directory
     → 404.
  3. Stream bytes back as `application/octet-stream` with
     `Content-Length: <size>` and
     `Content-Disposition: attachment; filename="<basename>"`.
  4. Mark ticket consumed after stream completes (do not consume on
     stream abort — let user retry within the TTL).
  5. Append audit entry on success.
- **Error response shape** (same as PUT for 4xx).

#### `check_protected_path` enforcement

Both endpoints call the same `check_protected_path()` helper used by
`read_file` / `write_file`. The check happens at **ticket mint time**
(in `prepare_upload` / `prepare_download`) and again at **ticket
redeem time** (in the HTTP handler) — defense in depth in case
protected paths are reconfigured between mint and redeem.

### 2. New MCP tools

Tools live in a new module `src/mymcp/tools/transfer.py`. Both tools are
registered in `READ_TOOLS` / `WRITE_TOOLS` accordingly:

- `prepare_upload` → **`WRITE_TOOLS`** (it grants write capability to a path)
- `prepare_download` → **`READ_TOOLS`** (read-only)

#### Tool descriptions (kept minimal — these eat context tokens on every session)

```
prepare_upload: Mint a signed URL for uploading bytes to a server path.
prepare_download: Mint a signed URL for downloading bytes from a server path.
```

That's it. No usage hints, no examples, no when-to-use guidance. The
LLM will see the parameter schema (with field descriptions like
`dest_path`, `max_bytes`, `expires_in`) and figure it out from there.
Detailed how-to-use lives in the **tool return JSON**, which is paid
for only when the tool is actually called.

#### `prepare_upload` parameters

| Field | Type | Default | Description |
|---|---|---|---|
| `dest_path` | string | required | Absolute path on the server where bytes will be written. |
| `max_bytes` | int | 2 GB | Reject the upload if more bytes are sent. |
| `expires_in` | int (seconds) | 300 | TTL for the ticket. Capped at `transfer_max_ttl_sec` (default 900). |
| `overwrite` | bool | true | If false and `dest_path` exists, mint refuses. |

#### `prepare_download` parameters

| Field | Type | Default | Description |
|---|---|---|---|
| `src_path` | string | required | Absolute path on the server to read. |
| `expires_in` | int (seconds) | 300 | TTL for the ticket. |

#### Tool return JSON (rich — paid only on use)

`prepare_upload` returns:

```json
{
  "url": "https://server.example.com/files/raw/9f3c8a72b1e4...",
  "method": "PUT",
  "ticket": "9f3c8a72b1e4...",
  "expires_in": 300,
  "expires_at": "2026-05-04T10:35:00Z",
  "max_bytes": 2147483648,
  "dest_path": "/tmp/foo.deb",
  "curl_example": "curl -fsS -T /local/path/to/foo.deb 'https://server.example.com/files/raw/9f3c8a72b1e4...'",
  "instructions": "Run the curl above from the MCP client's local shell. The file's raw bytes go in the request body. On success the server returns {\"ok\": true, \"path\": \"...\", \"bytes_written\": N}.",
  "on_error": "If the URL returns 4xx, read the JSON error.hint field and call prepare_upload again if needed. Tickets are single-use; do not retry the same URL."
}
```

`prepare_download` returns:

```json
{
  "url": "https://server.example.com/files/raw/abf17d0e25c8...",
  "method": "GET",
  "ticket": "abf17d0e25c8...",
  "expires_in": 300,
  "expires_at": "2026-05-04T10:35:00Z",
  "src_path": "/var/log/big.log",
  "size": 87654321,
  "curl_example": "curl -fsS 'https://server.example.com/files/raw/abf17d0e25c8...' -o /local/path/big.log",
  "instructions": "Run the curl above from the MCP client's local shell. Bytes stream back as the response body.",
  "on_error": "If the URL returns 4xx, read the JSON error.hint field and call prepare_download again if needed."
}
```

The `curl_example` field is the highest-value field: in practice LLMs
reliably copy and adapt the example rather than constructing curl from
scratch.

### 3. Ticket store

In-memory dict, keyed by ticket id:

```python
@dataclass
class Ticket:
    ticket_id: str          # 32 bytes from secrets.token_urlsafe()
    op: Literal["upload", "download"]
    path: str               # absolute, already validated against protected paths
    max_bytes: int          # for uploads only; ignored for downloads
    expires_at: float       # unix timestamp
    consumed: bool = False  # set True after successful redeem
    created_by: str         # token name from audit context
```

- Stored in a `dict[str, Ticket]` guarded by an `asyncio.Lock`.
- Lookup is O(1). Sweep expired entries lazily on each lookup; also
  schedule a background task that prunes every 60 s to bound memory.
- Single-process only. If we later run mymcp behind multiple workers,
  this becomes a problem — but mymcp is single-process today, and a
  switch to a shared store (Redis, file) is a localized change.

### 4. URL construction

The prepare tools need to know the **public base URL** of the server to
include in the response. Options:

- Read it from a new setting `MYMCP_PUBLIC_BASE_URL` (e.g.
  `https://mcp.example.com`). If unset, fall back to the `Host` header
  of the incoming MCP request — which is what the client used to reach
  us, so the same hostname will work for the bypass endpoint.
- Document `MYMCP_PUBLIC_BASE_URL` as required when running behind a
  reverse proxy that rewrites `Host`.

## Configuration

New settings in `src/mymcp/config.py`:

| Setting | Default | Purpose |
|---|---|---|
| `transfer_enabled` | `true` | Master switch; if false, both prepare tools and HTTP endpoints return 404/disabled error. |
| `transfer_max_bytes` | `2 * 1024**3` (2 GB) | Hard cap on a single upload, regardless of caller-supplied `max_bytes`. |
| `transfer_default_ttl_sec` | `300` | Default ticket TTL when caller doesn't override. |
| `transfer_max_ttl_sec` | `900` | Caller-supplied `expires_in` is clamped to this. |
| `public_base_url` | `""` (empty → use request `Host`) | Public-facing base URL for constructing ticket URLs. |

Env-var mapping follows existing convention (`MYMCP_TRANSFER_ENABLED`,
etc., wired through `_ENV_MAP` in `config.py`).

## Auth and Permissions

- **Tool gating:** standard `READ_TOOLS` / `WRITE_TOOLS` model. An `ro`
  token cannot mint upload tickets.
- **Endpoint auth:** tickets only — no Bearer header needed for the
  bypass endpoints. This keeps the curl command self-contained and
  avoids the long-lived token leaking into shell history.
- **Path safety:** `check_protected_path` is enforced both at mint and
  at redeem.
- **No path traversal in URL:** the URL only contains an opaque ticket
  id. The actual filesystem path lives in the ticket store, which only
  accepts paths validated at mint time.

## Audit logging

Every ticket mint and every endpoint redeem appends an audit entry,
reusing the existing `audit.py` rotating logger:

- **Mint:** `tool=prepare_upload|prepare_download`, `path`, `ticket_id`,
  `expires_at`, `token_name`, `success=true`.
- **Redeem success:** `event=transfer_redeem`,
  `op=upload|download`, `path`, `ticket_id`, `bytes`,
  `client_ip`, `success=true`.
- **Redeem failure:** same shape with `success=false`,
  `error_code` (`ticket_expired`, `ticket_consumed`, `path_not_found`,
  `size_exceeded`, ...).

This lets an operator reconstruct: "what was uploaded by whom to where
in the last hour."

## Error handling

| Condition | HTTP status | `error` code | `hint` |
|---|---|---|---|
| Ticket not found | 404 | `ticket_not_found` | "Ticket invalid or already used. Mint a new one." |
| Ticket expired | 410 | `ticket_expired` | "Call prepare_upload/download again." |
| Ticket consumed | 410 | `ticket_consumed` | "Tickets are single-use. Mint a fresh one." |
| Wrong method (GET on upload ticket, etc.) | 405 | `wrong_method` | "Use {expected} for this ticket." |
| `Content-Length` > max_bytes | 413 | `size_exceeded` | "File too large. Re-mint with a higher max_bytes." |
| Body bytes > max_bytes mid-stream | 413 | `size_exceeded` | (same) |
| Source file missing (download) | 404 | `path_not_found` | "Server file does not exist." |
| Protected path | 403 | `path_protected` | "Path is protected. See server configuration." |
| Transfer disabled | 404 | `transfer_disabled` | "File transfer feature is disabled on this server." |

Tool-level errors (mint failures) follow the existing
`{"success": False, "error": ..., "message": ...}` convention so they
are picked up by the audit error extractor in `mcp_server.call_tool`.

## Existing tools — deliberate non-changes

We considered tightening `read_file` / `write_file` to push users toward
the new endpoints for large files. We are **not** doing that:

- `write_file` already has a 10 MB cap, which is the right cap for a
  text-editing tool.
- `read_file` is line/byte bounded already (2000 lines × 32 KB/line
  worst case ~64 MB, in practice much less for real files).
- Adding new errors to existing tools just to advertise the transfer
  endpoints would create churn and break clients that work fine today.

The new feature is **additive**.

## Testing

- Unit tests for ticket store: mint, lookup, expiry, consumption,
  protected-path rejection.
- Endpoint tests with `httpx.AsyncClient` against the FastAPI app:
  - happy-path PUT and GET round-trip with a binary fixture
  - expired ticket → 410
  - consumed ticket → 410
  - wrong method → 405
  - oversized body (both via `Content-Length` and via mid-stream
    truncation) → 413, no partial file left on disk
  - protected path → 403
  - download of missing file → 404
- MCP tool tests: mint returns expected shape; `ro` token cannot call
  `prepare_upload`.
- Integration test: full LLM-style flow — call `prepare_upload`,
  parse `url`, do a PUT against the test client, assert file content.
- Audit log assertions: mint and redeem entries present, error_code
  populated on failures.

## Rollout

- Feature flagged by `transfer_enabled` (default `true` once tested).
- No migration: ticket store is in-memory.
- Bump minor version (2.1.0); add CHANGELOG entry.

## Open questions / deferred

1. **Multi-worker deployments.** Ticket store is per-process. If we
   adopt a multi-worker uvicorn config, we'll need a shared store. Not
   blocking v1.
2. **Resumable uploads.** Out of scope for v1.
3. **Streaming server-to-URL.** Letting the server PUT to a remote URL
   (object storage) without staging on disk — possible but defer.
4. **`Range` header support on download.** Not in v1; the curl
   examples don't request it.
