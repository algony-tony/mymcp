# CLI Reference

`mymcp` is the server management binary for `algony-mymcp`. It provides service initialization, health diagnostics, foreground execution, and local token management.

---

## Command Overview

`mymcp` uses Go-style command flags (`-host`, `-port`, `-role`).

| Command | Description |
|---|---|
| `mymcp init [--yes]` | Run interactive setup wizard (creates `/etc/mymcp/.env`, initial tokens, and systemd service) |
| `mymcp doctor` | Run preflight health diagnostics, permission checks, and inspect host tools (`rg`) |
| `mymcp serve [flags]` | Run the MCP server in the foreground |
| `mymcp token list` | Display active client tokens, roles, and names |
| `mymcp token add [-role ro\|rw] <name>` | Generate and display a new Bearer token |
| `mymcp token revoke <token>` | Revoke and invalidate a client token |
| `mymcp config example` | Print an example `.env` configuration file to stdout |
| `mymcp version` | Print server binary version |

---

## Detailed Command Usage

### `mymcp init`

Interactive setup wizard designed for initial host setup.

```bash
sudo mymcp init
```

Flags:
- `--yes` or `-yes`: Accept all recommended defaults automatically without interactive prompts.
- `-port <PORT>`: Specify the listening port (default `8765`).
- `-client-name <NAME>`: Name for the initial client token (default `default`).
- `-recorder-api-key <KEY>`: Optional LLM API key for the recorder sidecar.

What it does:
1. Creates configuration directory at `/etc/mymcp/`.
2. Generates initial admin token, metrics token, and client token.
3. Writes `/etc/mymcp/.env` and `/etc/mymcp/tokens.json`.
4. Creates and enables the `mymcp.service` systemd unit.

---

### `mymcp doctor`

Inspects runtime prerequisites, file permissions, and optional dependencies:

```bash
sudo mymcp doctor
```

Checks performed:
- Availability of `ripgrep` (`rg`) for accelerated `grep`.
- Read and write permissions on `/var/log/mymcp` and `/etc/mymcp/`.
- Validity and syntax of `/etc/mymcp/.env` and `/etc/mymcp/tokens.json`.
- Systemd service status (if installed).

---

### `mymcp serve`

Runs the Streamable HTTP MCP server in the foreground. Typically invoked by systemd.

```bash
mymcp serve [flags]
```

Flags:
- `-host <IP>`: Bind IP address (overrides `MYMCP_HOST`, default `0.0.0.0`).
- `-port <PORT>`: Listen port (overrides `MYMCP_PORT`, default `8765`).
- `-env-file <PATH>`: Load environment variables from a specific file.

**Ephemeral Token Mode:**
If no `tokens.json` file is present and `MYMCP_ADMIN_TOKEN` is unset, `serve` automatically starts in ephemeral mode: it generates temporary admin and client tokens, prints them to stderr, and discards them on exit.

---

### `mymcp token`

Local token management subcommands. These operate directly on the local token store file (`/etc/mymcp/tokens.json`), requiring no running server or network calls.

```bash
# List all active client tokens
sudo mymcp token list

# Create a read-only token
sudo mymcp token add -role ro claude-desktop

# Create a read-write token
sudo mymcp token add -role rw admin-ci

# Revoke a token
sudo mymcp token revoke tok_1234567890abcdef
```

Notes:
- The token name is a required positional argument.
- `-role` defaults to `ro`. Use `-role rw` to grant write capabilities.
- The raw secret token is printed **only once** upon creation.

---

### `mymcp config example`

Outputs a clean template configuration file matching all built-in defaults:

```bash
mymcp config example > /tmp/mymcp.env
```
