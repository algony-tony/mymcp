#!/usr/bin/env bash
# Re-exec under bash if invoked via sh/dash
if [ -z "${BASH_VERSION:-}" ]; then
    exec bash "$0" "$@"
fi
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

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
source "${REPO_DIR}/deploy/install_lib.sh"
SERVICE_NAME="mymcp"
MIN_PYTHON_MINOR=11

echo "=== Installing MyMCP Server ==="

# ---------------------------------------------------------------------------
# Step 1: Install path
# ---------------------------------------------------------------------------
APP_DIR=$(prompt_value "Install path" "/opt/mymcp")
validate_app_dir "$APP_DIR" || exit 1
echo ""

# ---------------------------------------------------------------------------
# Step 2: Find and confirm Python version
# ---------------------------------------------------------------------------
PYTHON=$(find_python "$MIN_PYTHON_MINOR") || {
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

# ---------------------------------------------------------------------------
# Step 6: .env configuration
# ---------------------------------------------------------------------------
GENERATED_TOKEN=""
CONFIGURED_PORT="8765"

if [ -f "${APP_DIR}/.env" ]; then
    echo "Existing .env found, preserving configuration."
    CONFIGURED_PORT=$(sed -n 's/^MCP_PORT=//p' "${APP_DIR}/.env" 2>/dev/null || echo "8765")
else
    CONFIGURED_PORT=$(prompt_value "MCP port" "8765")
    GENERATED_TOKEN=$(openssl rand -hex 16)

    cat > "${APP_DIR}/.env" <<EOF
MCP_ADMIN_TOKEN=${GENERATED_TOKEN}
MCP_HOST=0.0.0.0
MCP_PORT=${CONFIGURED_PORT}
MCP_TOKEN_FILE=${APP_DIR}/tokens.json
EOF
    chmod 600 "${APP_DIR}/.env"
fi
echo ""

# ---------------------------------------------------------------------------
# Step 7: systemd service
# ---------------------------------------------------------------------------
echo "Installing systemd service..."
sed -e "s|WorkingDirectory=.*|WorkingDirectory=${APP_DIR}|" \
    -e "s|EnvironmentFile=.*|EnvironmentFile=${APP_DIR}/.env|" \
    -e "s|ExecStart=.*|ExecStart=${APP_DIR}/venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port ${CONFIGURED_PORT}|" \
    "${REPO_DIR}/deploy/mymcp.service" > "/etc/systemd/system/${SERVICE_NAME}.service"

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"

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
