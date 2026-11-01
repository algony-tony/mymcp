# Server Overview Recorder: Background & Architecture

## Problem Statement

When AI assistants operate a Linux server over an MCP interface, every session leaves the system in an altered state (packages installed, config files updated, services started). However, standard servers have no memory of *what the server has become*. Each new session forces the AI to rediscover the environment from scratch through redundant, slow, and expensive discovery probes.

The overview recorder solves this by acting as a **progressive-disclosure map** that tracks system state changes over time.

## Core Mechanisms

### 1. Asynchronous Decoupled Processing
- Mutation operations (`write_file`, `bash_execute`, `edit_file`) are not slowed down by LLM calls.
- The Go core server immediately writes the mutation audit record into `audit.log`.
- The `mymcp-recorder` daemon tails `audit.log` independently in the background.

### 2. Reliable Cursor Tracking
- Stored at `/var/lib/mymcp/recorder/cursor.json`:
  ```json
  { "file": "audit.log", "inode": 1234567, "offset": 84219 }
  ```
- Uses both `inode` and byte `offset` to safely tolerate log rotations without double-processing or losing events across restarts.

### 3. Bootstrap & Periodic Merge Cycles
- **Bootstrap Phase**: On a fresh deployment where `overview.md` does not yet exist, the recorder runs an autonomous probing agent loop (`bash_probe` and `read_file_probe`) to inspect distro, active systemd services, listening ports, and key applications, generating the initial overview.
- **Merge Phase**: Runs periodically (default every 300 seconds). Pulls unconsumed mutating events, invokes the configured LLM (Anthropic or OpenAI-compatible), folds changes into `/var/lib/mymcp/recorder/overview/overview.md`, and appends structured summaries to `changelog.md`.

### 4. Freshness & Staleness Detection
The Go core's `server_overview` tool reads `overview.md` directly and inspects the recorder's `cursor.json`:
- `pending_events`: Unconsumed mutating events in `audit.log`.
- `last_updated`: File modification timestamp of `overview.md`.
- `stale`: Evaluated as `pending_events > 0 AND last_updated older than 2 × MYMCP_RECORDER_MERGE_INTERVAL_SEC`.
- If stale, the tool prepends a visible warning banner so the AI knows the document is lagging.

### 5. Circuit Breaker & Fault Recovery
- If the LLM provider fails consecutively past `MYMCP_RECORDER_CIRCUIT_BREAKER_THRESHOLD`, the circuit breaker trips to prevent tight-loop failures.
- Recovery is **event-driven**: subsequent incoming mutating events past the watermark trigger a single probe retry. A successful merge immediately resets the breaker without requiring a process restart.
