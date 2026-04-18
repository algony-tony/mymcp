#!/usr/bin/env bash
# Install, prepare a wheels dir, run upgrade with --wheels-dir to verify
# offline install path.
set -euo pipefail
source "$(dirname "$0")/_setup.sh"

bootstrap_test_repo
cd "$WORKDIR/repo"

APP_DIR=/opt/mymcp
AUTO_YES=true MCP_ADMIN_TOKEN=testtoken bash deploy/install.sh -y

WHEELS="$WORKDIR/wheels"
mkdir -p "$WHEELS"
"$APP_DIR/venv/bin/pip" download -r "$APP_DIR/requirements.txt" -d "$WHEELS"

bash "$APP_DIR/deploy/upgrade.sh" --app-dir="$APP_DIR" \
    --source="$REMOTE_SRC" --foreground --no-health-check --wheels-dir="$WHEELS" v-test-new

test -f "$APP_DIR/.install-info"
grep -q "v-test-new" "$APP_DIR/.install-info"

echo "PASS: scenario_offline_wheels"
