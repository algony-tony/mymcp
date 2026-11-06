# Large & Binary File Transfer Design

## Problem Statement

Standard MCP file operations (`read_file`, `write_file`, `edit_file`) are line- and string-oriented text tools:
1. **Binary files** (`.deb`, `.tar.gz`, binaries, images) fail or corrupt under UTF-8 decoding.
2. **Large files** (>10 MB) cannot fit comfortably within an LLM conversation context, wasting context window tokens and risking timeouts.
3. Transferring local installer packages or backups to a remote server without public network access requires a clean transfer mechanism.

## Design Principle: Direct HTTP Streaming Bypass

**File bytes must never enter the LLM context window.**

Instead of transferring bytes over MCP JSON-RPC messages, `mymcp` provides signed, single-use ticket endpoints for raw HTTP streaming.

```
┌─────────────┐               ┌───────────────┐              ┌────────────────┐
│  AI Client  │               │ Go MCP Server │              │ Host / Local   │
└──────┬──────┘               └───────┬───────┘              └───────┬────────┘
       │                              │                              │
       │ 1. prepare_upload(dest_path) │                              │
       ├─────────────────────────────►│                              │
       │                              │                              │
       │ 2. Return signed URL + curl  │                              │
       │◄─────────────────────────────┤                              │
       │                              │                              │
       │ 3. Execute curl command      │                              │
       ├──────────────────────────────┼─────────────────────────────►│
       │                              │                              │
       │                              │ 4. PUT /files/raw/{ticket}   │
       │                              │◄─────────────────────────────┤
       │                              │ (Raw stream directly to disk)│
       │                              │                              │
```

## Protocol Specifications

### 1. Upload Flow (`prepare_upload`)
1. Client calls MCP tool `prepare_upload(dest_path, max_bytes)`.
2. Server validates path against protected paths and creates an in-memory single-use ticket.
3. Server returns a ticket URL (e.g. `PUT /files/raw/<ticket>`) and a formatted `curl` command.
4. Client runs the curl command locally.
5. Server streams bytes directly to a temporary file next to `dest_path`, verifies byte caps, and performs an atomic rename to `dest_path` upon completion.

### 2. Download Flow (`prepare_download`)
1. Client calls MCP tool `prepare_download(src_path)`.
2. Server validates path and permissions, issuing a single-use `GET /files/raw/<ticket>` URL.
3. Client streams bytes directly to local storage via HTTP GET.

### 3. Security Properties
- **Single-Use**: Tickets are invalidated immediately upon connection/consumption.
- **Path-Scoped**: Tickets are bound to a concrete, pre-validated absolute filesystem path.
- **Time-Bounded**: Tickets expire automatically after TTL (default 300s, max 900s).
- **Protected Paths Enforcement**: Evaluated both at ticket creation and redemption time.
- **Audit Logging**: Full audit trail recorded for both ticket minting and byte transfer completion.
