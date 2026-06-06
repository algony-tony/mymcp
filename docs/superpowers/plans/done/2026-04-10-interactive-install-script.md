# Interactive Install Script Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite `deploy/install.sh` to prompt users for confirmation at each installation step, with `-y` flag for unattended mode.

**Architecture:** Single-file bash script with two helper functions (`confirm`, `prompt_value`) gating all interactive decisions. A global `AUTO_YES` flag bypasses prompts when `-y` is passed.

**Tech Stack:** Bash, sed, openssl, systemd

---

## File Structure

- **Modify:** `deploy/install.sh` — full rewrite

No new files. `deploy/mymcp.service` unchanged (sed applied at install time).

---

### Task 1: Argument Parsing and Helper Functions

**Files:**
- Modify: `deploy/install.sh` (rewrite top section, lines 1-9)

- [ ] **Step 1: Write the script header with `-y` parsing and helpers**

Replace the entire `deploy/install.sh` with this initial skeleton:

```bash
#!/usr/bin/env bash
set -euo pipefail

AUTO_YES=false

while getopts "yh" opt; do
    case "$opt" in
        y) AUTO_YES=true ;;
        h)
            echo "Usage: $0 [-y]"
            echo "  -y  Unattended mode (accept all defaults, no prompts)"
            exit 0
            ;;
        *) echo "Usage: $0 [-y]"; exit 1 ;;
    esac
done
shift $((OPTIND - 1))

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------
confirm() {
    local prompt="$1" default="${2:-Y}"
    if [ "$AUTO_YES" = true ]; then
        [ "$default" = "Y" ] && return 0 || return 1
    fi
    if [ "$default" = "Y" ]; then
        read -rp "${prompt} [Y/n]: " answer
        case "${answer,,}" in
            n|no) return 1 ;;
            *)    return 0 ;;
        esac
    else
        read -rp "${prompt} [y/N]: " answer
        case "${answer,,}" in
            y|yes) return 0 ;;
            *)     return 1 ;;
        esac
    fi
}

prompt_value() {
    local prompt="$1" default="$2"
    if [ "$AUTO_YES" = true ]; then
        echo "$default"; return
    fi
    read -rp "${prompt} [${default}]: " value
    echo "${value:-$default}"
}

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SERVICE_NAME="mymcp"
MIN_PYTHON_MINOR=11

echo "=== Installing MyMCP Server ==="
```

- [ ] **Step 2: Verify syntax**

Run: `bash -n deploy/install.sh`
Expected: no output (syntax OK)

- [ ] **Step 3: Commit**

```bash
git add deploy/install.sh
git commit -m "refactor: add argument parsing and helper functions to install script"
```

---

### Task 2: Install Path and Python Detection (Steps 1-2)

**Files:**
- Modify: `deploy/install.sh` (append after helpers)

- [ ] **Step 1: Add install path prompt and Python detection**

Append to `deploy/install.sh`:

```bash
# ---------------------------------------------------------------------------
# Step 1: Install path
# ---------------------------------------------------------------------------
APP_DIR=$(prompt_value "Install path" "/opt/mymcp")
echo ""

# ---------------------------------------------------------------------------
# Step 2: Find and confirm Python version
# ---------------------------------------------------------------------------
find_python() {
    for minor in 14 13 12 11; do
        for cmd in "python3.${minor}" "python${minor}"; do
            if command -v "$cmd" &>/dev/null; then
                echo "$cmd"; return
            fi
        done
    done
    for cmd in python3 python; do
        if command -v "$cmd" &>/dev/null; then
            ver=$("$cmd" -c 'import sys; print(sys.version_info.minor)' 2>/dev/null || echo 0)
            if [ "$ver" -ge "$MIN_PYTHON_MINOR" ]; then
                echo "$cmd"; return
            fi
        fi
    done
    return 1
}

PYTHON=$(find_python) || {
    echo "ERROR: Python 3.${MIN_PYTHON_MINOR}+ not found."
    echo "Install it first, e.g.:"
    echo "  dnf install python3.11    # RHEL/CentOS"
    echo "  apt install python3.11    # Debian/Ubuntu"
    exit 1
}
PY_VERSION=$("$PYTHON" --version)

if ! confirm "Use ${PYTHON} (${PY_VERSION})?" Y; then
    echo "Aborted. Install your preferred Python 3.${MIN_PYTHON_MINOR}+ and re-run."
    exit 1
fi
echo ""
```

- [ ] **Step 2: Verify syntax**

Run: `bash -n deploy/install.sh`
Expected: no output

- [ ] **Step 3: Commit**

```bash
git add deploy/install.sh
git commit -m "feat: add interactive install path and Python version confirmation"
```

---

### Task 3: ripgrep Installation (Step 3)

**Files:**
- Modify: `deploy/install.sh` (append)

- [ ] **Step 1: Add ripgrep section with confirmation**

Append to `deploy/install.sh`:

```bash
# ---------------------------------------------------------------------------
# Step 3: ripgrep (optional)
# ---------------------------------------------------------------------------
if command -v rg &>/dev/null; then
    echo "ripgrep already installed: $(rg --version | head -1)"
elif confirm "Install ripgrep? (recommended for better search performance)" Y; then
    echo "Installing ripgrep..."
    installed=false

    if command -v apt-get &>/dev/null; then
        apt-get update -qq && apt-get install -y -qq ripgrep && installed=true
    elif command -v dnf &>/dev/null; then
        dnf install -y -q ripgrep && installed=true
    elif command -v pacman &>/dev/null; then
        pacman -S --noconfirm ripgrep && installed=true
    fi

    # Fallback: download pre-built binary from GitHub
    if [ "$installed" = false ]; then
        echo "Package manager install failed, downloading binary from GitHub..."
        ARCH=$(uname -m)
        case "$ARCH" in
            x86_64)  RG_ARCH="x86_64-unknown-linux-musl" ;;
            aarch64) RG_ARCH="aarch64-unknown-linux-gnu" ;;
            *)       RG_ARCH="" ;;
        esac
        if [ -n "$RG_ARCH" ]; then
            RG_VERSION=$(curl -sI https://github.com/BurntSushi/ripgrep/releases/latest \
                | grep -i ^location: | sed 's|.*/||' | tr -d '\r\n')
            RG_URL="https://github.com/BurntSushi/ripgrep/releases/download/${RG_VERSION}/ripgrep-${RG_VERSION}-${RG_ARCH}.tar.gz"
            TMP_DIR=$(mktemp -d)
            if curl -sL "$RG_URL" | tar xz -C "$TMP_DIR" --strip-components=1; then
                cp "${TMP_DIR}/rg" /usr/local/bin/rg
                chmod +x /usr/local/bin/rg
                echo "ripgrep ${RG_VERSION} installed to /usr/local/bin/rg"
                installed=true
            fi
            rm -rf "$TMP_DIR"
        fi
    fi

    if [ "$installed" = false ]; then
        echo "WARNING: Could not install ripgrep. grep tool will use Python regex fallback (slower)."
    fi
else
    echo "Skipped. grep tool will use Python regex fallback (slower)."
fi
echo ""
```

- [ ] **Step 2: Verify syntax**

Run: `bash -n deploy/install.sh`
Expected: no output

- [ ] **Step 3: Commit**

```bash
git add deploy/install.sh
git commit -m "feat: add interactive ripgrep installation with skip option"
```

---

### Task 4: File Copy, Venv, and Dependencies (Steps 4-5)

**Files:**
- Modify: `deploy/install.sh` (append)

- [ ] **Step 1: Add file copy and venv sections**

Append to `deploy/install.sh`:

```bash
# ---------------------------------------------------------------------------
# Step 4: Copy project files
# ---------------------------------------------------------------------------
echo "Copying files to ${APP_DIR}..."
mkdir -p "${APP_DIR}"
rsync -a --exclude='.git' --exclude='__pycache__' --exclude='tests' \
    --exclude='.pytest_cache' --exclude='deploy' --exclude='docs' \
    "${REPO_DIR}/" "${APP_DIR}/"

# ---------------------------------------------------------------------------
# Step 5: Virtual environment & dependencies
# ---------------------------------------------------------------------------
if [ -d "${APP_DIR}/venv" ]; then
    VENV_PY="${APP_DIR}/venv/bin/python3"
    if [ -x "$VENV_PY" ] && "$VENV_PY" -c 'import pip' &>/dev/null; then
        echo "Existing venv is healthy, skipping creation."
    else
        echo "Existing venv is broken, recreating..."
        rm -rf "${APP_DIR}/venv"
    fi
fi

if [ ! -d "${APP_DIR}/venv" ]; then
    echo "Creating virtual environment..."
    if ! "$PYTHON" -m venv --help &>/dev/null && command -v apt-get &>/dev/null; then
        PY_MINOR=$("$PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        echo "Installing python${PY_MINOR}-venv..."
        apt-get install -y -qq "python${PY_MINOR}-venv"
    fi
    "$PYTHON" -m venv "${APP_DIR}/venv"

    if ! "${APP_DIR}/venv/bin/python3" -c 'import pip' &>/dev/null; then
        echo "Installing pip into venv..."
        "${APP_DIR}/venv/bin/python3" -m ensurepip --upgrade 2>/dev/null \
            || curl -sS https://bootstrap.pypa.io/get-pip.py | "${APP_DIR}/venv/bin/python3"
    fi
fi

echo "Installing dependencies..."
"${APP_DIR}/venv/bin/pip" install -q --upgrade pip
"${APP_DIR}/venv/bin/pip" install -q -r "${APP_DIR}/requirements.txt"
echo ""
```

- [ ] **Step 2: Verify syntax**

Run: `bash -n deploy/install.sh`
Expected: no output

- [ ] **Step 3: Commit**

```bash
git add deploy/install.sh
git commit -m "feat: add idempotent file copy and venv setup"
```

---

### Task 5: .env Configuration and systemd Service (Steps 6-7)

**Files:**
- Modify: `deploy/install.sh` (append)

- [ ] **Step 1: Add .env and systemd sections**

Append to `deploy/install.sh`:

```bash
# ---------------------------------------------------------------------------
# Step 6: .env configuration
# ---------------------------------------------------------------------------
GENERATED_TOKEN=""
CONFIGURED_PORT="8765"

if [ -f "${APP_DIR}/.env" ]; then
    echo "Existing .env found, preserving configuration."
    CONFIGURED_PORT=$(grep -oP '^MCP_PORT=\K.*' "${APP_DIR}/.env" 2>/dev/null || echo "8765")
else
    CONFIGURED_PORT=$(prompt_value "MCP port" "8765")
    GENERATED_TOKEN=$(openssl rand -hex 16)

    cat > "${APP_DIR}/.env" <<EOF
MCP_ADMIN_TOKEN=${GENERATED_TOKEN}
MCP_HOST=0.0.0.0
MCP_PORT=${CONFIGURED_PORT}
MCP_TOKEN_FILE=${APP_DIR}/tokens.json
EOF
fi
echo ""

# ---------------------------------------------------------------------------
# Step 7: systemd service
# ---------------------------------------------------------------------------
echo "Installing systemd service..."
sed -e "s|WorkingDirectory=.*|WorkingDirectory=${APP_DIR}|" \
    -e "s|EnvironmentFile=.*|EnvironmentFile=${APP_DIR}/.env|" \
    -e "s|ExecStart=.*|ExecStart=${APP_DIR}/venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port ${CONFIGURED_PORT}|" \
    "${REPO_DIR}/deploy/mymcp.service" > /etc/systemd/system/${SERVICE_NAME}.service

systemctl daemon-reload
systemctl enable ${SERVICE_NAME}
```

- [ ] **Step 2: Verify syntax**

Run: `bash -n deploy/install.sh`
Expected: no output

- [ ] **Step 3: Commit**

```bash
git add deploy/install.sh
git commit -m "feat: add interactive .env config and systemd service setup"
```

---

### Task 6: Final Summary Output

**Files:**
- Modify: `deploy/install.sh` (append)

- [ ] **Step 1: Add final summary**

Append to `deploy/install.sh`:

```bash
# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo ""
echo "=== Installation complete ==="
echo "  Install path: ${APP_DIR}"
echo "  Python:       ${PYTHON} (${PY_VERSION})"
echo "  MCP port:     ${CONFIGURED_PORT}"
if [ -n "$GENERATED_TOKEN" ]; then
    echo ""
    echo "  *** Admin token: ${GENERATED_TOKEN} ***"
    echo "  (Save this token — it won't be shown again)"
fi
echo ""
echo "  Start: systemctl start ${SERVICE_NAME}"
echo "  Logs:  journalctl -u ${SERVICE_NAME} -f"
```

- [ ] **Step 2: Verify final script syntax**

Run: `bash -n deploy/install.sh`
Expected: no output

- [ ] **Step 3: Manual review — read the complete script end to end**

Run: `cat deploy/install.sh | head -200`
Verify: all 7 steps present, helpers at top, summary at bottom, no leftover old code.

- [ ] **Step 4: Commit and push**

```bash
git add deploy/install.sh
git commit -m "feat: add installation summary with token display"
git push
```

---

## Verification Checklist

After all tasks complete:

- [ ] `bash -n deploy/install.sh` — syntax check passes
- [ ] `./deploy/install.sh -h` — shows usage (can run locally, doesn't need root)
- [ ] Read through full script — no hardcoded `/opt/mymcp` outside defaults, all paths use `${APP_DIR}`
- [ ] Confirm `mymcp.service` template file is unchanged
