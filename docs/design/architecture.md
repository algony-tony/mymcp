# Architecture Design: Go Core and Dual-Component Architecture

## Background

Originally, mymcp was implemented entirely in Python (FastAPI + uvicorn + official Python MCP SDK). While functional, Python's memory footprint was high for small VPS environments (~35–45 MB steady RSS, peaking at ~61 MB).

In version 3.0.0, the server core was completely rewritten in Go. The Python package was retained solely as an optional, standalone background recorder daemon (`mymcp-recorder`).

## Two-Component Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                       Host System                           │
│                                                             │
│  MCP Client (Claude Desktop / Code / Cursor / Gemini CLI)   │
│                             │                               │
│                             ▼ Streamable HTTP / Bearer Auth │
│  ┌───────────────────────────────────────────────────────┐  │
│  │               Go Core Server (mymcp)                  │  │
│  │                                                       │  │
│  │  - Stateless HTTP Transport (port 8765)               │  │
│  │  - Auth Middleware (tokens.json: ro / rw)             │  │
│  │  - 9 MCP Tools (bash, files, glob, grep, transfer)    │  │
│  │  - Protected Paths & SOC Audit Trail                  │  │
│  │  - Prometheus /metrics                                │  │
│  └───────────────┬───────────────────────────────────────┘  │
│                  │                                          │
│                  │ Writes audit.log (rotating JSON lines)   │
│                  ▼                                          │
│  ┌───────────────────────────────────────────────────────┐  │
│  │               Shared State on Filesystem              │  │
│  │                                                       │  │
│  │  - /var/log/mymcp/audit.log                           │  │
│  │  - /var/lib/mymcp/recorder/cursor.json                │  │
│  │  - /var/lib/mymcp/recorder/overview/overview.md       │  │
│  │  - /var/lib/mymcp/recorder/overview/changelog.md      │  │
│  └──────────────────────────────────────▲────────────────┘  │
│                                         │                   │
│                                         │ Reads audit log,  │
│                                         │ writes overview   │
│  ┌──────────────────────────────────────┴────────────────┐  │
│  │        Python Sidecar (mymcp-recorder, Optional)      │  │
│  │                                                       │  │
│  │  - EventTailer: persistent cursor tracking            │  │
│  │  - AgentSupervisor: periodic merge cycle via LLM      │  │
│  │  - Auto-bootstrap initial overview                    │  │
│  │  - Circuit breaker for resilience                     │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

## Design Principles & Contracts

### 1. Go Core (The Server)
- **Zero Base Dependencies**: Compiled as a static binary with no external runtime dependencies.
- **Low Footprint**: Steady-state RSS ≤ 15 MB.
- **SOC Red Line**: Any failure writing to the audit log returns `InternalError / 500`. Unauditable mutations are strictly forbidden.
- **Stateless Transport**: Every HTTP request is independent; reconnecting clients experience zero session state mismatch.
- **Strict Role-based Permissions**: Tokens are categorized as `ro` (read-only tools) or `rw` (all tools). The `tools/list` response is filtered according to the caller's role.

### 2. Python ↔ Go Inter-Process Contract
The Go server and Python recorder daemon communicate purely via the filesystem:
- **`audit.log`**: Go writes JSON Lines entries; Python's `EventTailer` tails the file based on `{inode, offset}` tracked in `cursor.json`.
- **`overview.md`**: The Python sidecar periodically folds mutating events into this file. The Go core's `server_overview` tool reads it to respond to MCP clients, while write-protecting the directory against client modifications.
- **`cursor.json`**: Go inspects `cursor.json` to compute event backlog lag and set the `stale` flag if the recorder falls behind.

### 3. Packaging & Distribution
- The project distributes platform wheels (`manylinux2014_x86_64`, `arm64`) where the `mymcp` executable is the Go binary injected directly into the wheel scripts directory via `scripts/assemble_wheel.py`.
- Running `pipx install algony-mymcp` delivers the static Go binary with zero Python dependencies.
- Installing the recorder extra (`pip install "algony-mymcp[recorder]"`) provides the Python sidecar and `mymcp-recorder` entry point.

### 4. Executable Compatibility Contract
To guarantee backward compatibility with previous versions and MCP clients, a black-box test suite (`tests/compat/`) runs against the Go server, comparing tool schemas against frozen golden schema definitions (`golden_tools.json`).
