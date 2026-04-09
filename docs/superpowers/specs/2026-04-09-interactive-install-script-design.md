# Interactive Install Script Design

**Date:** 2026-04-09
**Scope:** Redesign `deploy/install.sh` to support interactive and unattended modes.

## Problem

Current install script runs all system installations silently without user consent. Users should be able to review and confirm each step before system-level changes are made.

## Command Line Interface

```
./deploy/install.sh          # Interactive mode — prompts for confirmation
./deploy/install.sh -y       # Unattended mode — all defaults, no prompts
```

Global flag: `AUTO_YES=false` (set to `true` when `-y` is passed).

## Helper Functions

### `confirm prompt [default]`

Yes/no confirmation prompt.

- `default` is `Y` or `N`, controls what happens on empty input
- Displays `[Y/n]` or `[y/N]` accordingly
- When `AUTO_YES=true`: returns the default without prompting

### `prompt_value prompt default`

Prompt user for a value with a default.

- Displays `prompt [default]: `, returns user input or default on empty input
- When `AUTO_YES=true`: returns `default` without prompting

## Installation Flow

All steps are **idempotent** — safe to re-run after partial failure.

### Step 1: Install Path

```
APP_DIR = prompt_value "Install path" "/opt/mymcp"
```

User can customize the installation directory. Default: `/opt/mymcp`.

### Step 2: Python Version

Auto-detect highest available Python >= 3.11, then:

```
confirm "Use python3.12 (Python 3.12.3)?" Y
```

- Yes (or Enter): proceed with detected Python
- No: clean exit (exit 1) with message instructing user to install desired Python version first

No version selection menu — the script picks the best available; user should install their preferred version before running the script if unsatisfied.

### Step 3: ripgrep (Optional)

- If `rg` is already installed: **skip** (idempotent)
- Otherwise:

```
confirm "Install ripgrep? (recommended for better search performance)" Y
```

- Yes: install via package manager or GitHub binary (existing fallback logic)
- No: skip, print "Skipped. grep tool will use Python regex fallback (slower)."

### Step 4: Copy Project Files

```
rsync -a ... "${REPO_DIR}/" "${APP_DIR}/"
```

Always runs (rsync is naturally idempotent). No confirmation needed.

### Step 5: Virtual Environment & Dependencies

- If venv exists and is healthy: skip creation, only update dependencies
- If venv missing or broken: auto-install `python3.x-venv` package if needed (Debian/Ubuntu), create venv
- Always run `pip install -r requirements.txt`

No confirmation — this is a required step.

### Step 6: .env Configuration

- If `.env` already exists: **skip** (do not overwrite user config, idempotent)
- Otherwise:
  - `port = prompt_value "MCP port" "8765"`
  - `token = openssl rand -hex 16` (auto-generated, no prompt)
  - Write `.env` with these values
  - Print generated admin token prominently at the end

### Step 7: systemd Service

- Use `sed` to replace hardcoded paths in `mymcp.service` template with actual `APP_DIR` and port
- Copy to `/etc/systemd/system/mymcp.service`
- Run `systemctl daemon-reload && systemctl enable mymcp`

No confirmation — required step.

### Final Output

```
=== Installation complete ===
  Install path: /opt/mymcp
  Python:       python3.12 (Python 3.12.3)
  MCP port:     8765
  Admin token:  a1b2c3d4e5f6...

  Start: systemctl start mymcp
  Logs:  journalctl -u mymcp -f
```

## `-y` Mode Defaults

| Setting | Default |
|---------|---------|
| Install path | `/opt/mymcp` |
| Python | Highest available >= 3.11 |
| ripgrep | Install |
| MCP port | `8765` |
| Admin token | Random (printed at end) |

## Idempotency Rules

| Component | Already exists? | Behavior |
|-----------|----------------|----------|
| ripgrep | Installed | Skip |
| APP_DIR files | Present | rsync overwrites (idempotent) |
| venv | Healthy | Skip creation, update deps |
| venv | Broken/missing | Recreate |
| .env | Present | Skip (preserve user config) |
| systemd service | Present | Overwrite with latest |

## Files Changed

- `deploy/install.sh` — full rewrite with interactive/unattended modes
- `deploy/mymcp.service` — no change to file itself; `sed` applied at install time

## Out of Scope

- Multi-Python version selection menu
- Uninstall script
- Non-systemd init systems
