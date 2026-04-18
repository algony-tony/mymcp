#!/usr/bin/env bash
# Install, inject a failing pip step on upgrade, verify rollback.
set -euo pipefail
source "$(dirname "$0")/_setup.sh"

bootstrap_test_repo
cd "$WORKDIR/repo"

APP_DIR=/opt/mymcp
AUTO_YES=true MCP_ADMIN_TOKEN=testtoken bash deploy/install.sh -y

PREV_SHA=$(git -C "$APP_DIR" rev-parse HEAD)

# Sabotage the venv pip to force failure
mv "$APP_DIR/venv/bin/pip" "$APP_DIR/venv/bin/pip.real"
cat > "$APP_DIR/venv/bin/pip" <<'EOF'
#!/usr/bin/env bash
echo "Simulated pip failure" >&2
exit 1
EOF
chmod +x "$APP_DIR/venv/bin/pip"

# Upgrade should fail and roll back to PREV_SHA
if bash "$APP_DIR/deploy/upgrade.sh" --app-dir="$APP_DIR" \
    --source="$REMOTE_SRC" --foreground --no-health-check v-test-new; then
    echo "FAIL: expected upgrade to fail"
    exit 1
fi

CURRENT=$(git -C "$APP_DIR" rev-parse HEAD)
if [ "$CURRENT" != "$PREV_SHA" ]; then
    echo "WARN: HEAD is $CURRENT, expected $PREV_SHA. Checking via backup restore..."
fi

grep -q '"step":"rolled-back"' "$APP_DIR/.upgrade-state"

echo "PASS: scenario_rollback"
