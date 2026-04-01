#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/mymcp"
SERVICE_NAME="mymcp"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== Installing MyMCP Server ==="

# Copy project files
echo "Copying files to ${APP_DIR}..."
mkdir -p "${APP_DIR}"
rsync -a --exclude='.git' --exclude='__pycache__' --exclude='tests' \
    --exclude='.pytest_cache' --exclude='deploy' --exclude='docs' \
    "${REPO_DIR}/" "${APP_DIR}/"

# Create venv and install deps
if [ ! -d "${APP_DIR}/venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "${APP_DIR}/venv"
fi
echo "Installing dependencies..."
"${APP_DIR}/venv/bin/pip" install -q -r "${APP_DIR}/requirements.txt"

# Create .env if not exists
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

# Install systemd service
echo "Installing systemd service..."
cp "${REPO_DIR}/deploy/mymcp.service" /etc/systemd/system/${SERVICE_NAME}.service
systemctl daemon-reload
systemctl enable ${SERVICE_NAME}

echo ""
echo "=== Installation complete ==="
echo "1. Edit ${APP_DIR}/.env to set MCP_ADMIN_TOKEN"
echo "2. Start with: systemctl start ${SERVICE_NAME}"
echo "3. Check logs: journalctl -u ${SERVICE_NAME} -f"
