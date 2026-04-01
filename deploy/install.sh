#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/mymcp"
SERVICE_NAME="mymcp"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
MIN_PYTHON_MINOR=11  # Python 3.11+

echo "=== Installing MyMCP Server ==="

# ---------------------------------------------------------------------------
# Find suitable Python (>= 3.11)
# ---------------------------------------------------------------------------
find_python() {
    # Try explicit versioned binaries first (highest to lowest)
    for minor in 14 13 12 11; do
        for cmd in "python3.${minor}" "python${minor}"; do
            if command -v "$cmd" &>/dev/null; then
                echo "$cmd"; return
            fi
        done
    done
    # Fall back to python3 / python and check version
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
echo "Using ${PYTHON} (${PY_VERSION})"

# ---------------------------------------------------------------------------
# Install ripgrep if missing
# ---------------------------------------------------------------------------
if ! command -v rg &>/dev/null; then
    echo "ripgrep (rg) not found, installing..."
    installed=false

    # Try package manager first
    if command -v apt-get &>/dev/null; then
        apt-get update -qq && apt-get install -y -qq ripgrep && installed=true
    elif command -v pacman &>/dev/null; then
        pacman -S --noconfirm ripgrep && installed=true
    fi

    # Fallback: download pre-built binary from GitHub
    if [ "$installed" = false ]; then
        echo "Package manager doesn't have ripgrep, downloading binary from GitHub..."
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
        echo "WARNING: Could not install ripgrep. The 'grep' tool will fall back to Python regex (slower)."
    fi
fi

# ---------------------------------------------------------------------------
# Copy project files
# ---------------------------------------------------------------------------
echo "Copying files to ${APP_DIR}..."
mkdir -p "${APP_DIR}"
rsync -a --exclude='.git' --exclude='__pycache__' --exclude='tests' \
    --exclude='.pytest_cache' --exclude='deploy' --exclude='docs' \
    "${REPO_DIR}/" "${APP_DIR}/"

# ---------------------------------------------------------------------------
# Create venv and install deps (recreate if Python version changed)
# ---------------------------------------------------------------------------
RECREATE_VENV=false
if [ -d "${APP_DIR}/venv" ]; then
    VENV_PY="${APP_DIR}/venv/bin/python3"
    if [ ! -x "$VENV_PY" ] || ! "$VENV_PY" -c 'import pip' &>/dev/null; then
        echo "Existing venv is broken, recreating..."
        rm -rf "${APP_DIR}/venv"
        RECREATE_VENV=true
    fi
fi

if [ ! -d "${APP_DIR}/venv" ]; then
    echo "Creating virtual environment..."
    "$PYTHON" -m venv "${APP_DIR}/venv"

    # Ensure pip is available (some distros ship venv without pip)
    if ! "${APP_DIR}/venv/bin/python3" -c 'import pip' &>/dev/null; then
        echo "Installing pip into venv..."
        "${APP_DIR}/venv/bin/python3" -m ensurepip --upgrade 2>/dev/null \
            || curl -sS https://bootstrap.pypa.io/get-pip.py | "${APP_DIR}/venv/bin/python3"
    fi
fi

echo "Installing dependencies..."
"${APP_DIR}/venv/bin/pip" install -q --upgrade pip
"${APP_DIR}/venv/bin/pip" install -q -r "${APP_DIR}/requirements.txt"

# ---------------------------------------------------------------------------
# Create .env if not exists
# ---------------------------------------------------------------------------
if [ ! -f "${APP_DIR}/.env" ]; then
    echo "Creating .env file (edit MCP_ADMIN_TOKEN before starting)..."
    cat > "${APP_DIR}/.env" <<'EOF'
MCP_ADMIN_TOKEN=CHANGE_ME
MCP_HOST=0.0.0.0
MCP_PORT=8765
MCP_TOKEN_FILE=/opt/mymcp/tokens.json
EOF
    echo "WARNING: Edit ${APP_DIR}/.env and set MCP_ADMIN_TOKEN before starting!"
fi

# ---------------------------------------------------------------------------
# Install systemd service (update Python path to match venv)
# ---------------------------------------------------------------------------
echo "Installing systemd service..."
cp "${REPO_DIR}/deploy/mymcp.service" /etc/systemd/system/${SERVICE_NAME}.service
systemctl daemon-reload
systemctl enable ${SERVICE_NAME}

echo ""
echo "=== Installation complete ==="
echo "1. Edit ${APP_DIR}/.env to set MCP_ADMIN_TOKEN"
echo "2. Start with: systemctl start ${SERVICE_NAME}"
echo "3. Check logs: journalctl -u ${SERVICE_NAME} -f"
