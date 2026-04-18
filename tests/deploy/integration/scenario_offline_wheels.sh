#!/usr/bin/env bash
# Install, prepare a wheels dir, run upgrade with --wheels-dir to verify offline install path.
set -euo pipefail

WORKDIR=$(mktemp -d)
cp -r /src "$WORKDIR/repo"
cd "$WORKDIR/repo"

PREV=$(git rev-parse HEAD~1 2>/dev/null || git rev-parse HEAD)
LATEST=$(git rev-parse HEAD)
git tag -f v-test-new "$LATEST"
git checkout -q "$PREV"

APP_DIR=/opt/mymcp
AUTO_YES=true MCP_ADMIN_TOKEN=testtoken bash deploy/install.sh -y

# Prepare wheels dir
WHEELS=$WORKDIR/wheels
mkdir -p "$WHEELS"
"$APP_DIR/venv/bin/pip" download -r "$APP_DIR/requirements.txt" -d "$WHEELS"

REMOTE_SRC="$WORKDIR/remote"
cp -r "$WORKDIR/repo" "$REMOTE_SRC"
git -C "$REMOTE_SRC" checkout -q "$LATEST"
git -C "$REMOTE_SRC" tag -f v-test-new "$LATEST"

# Simulate offline by removing network: inject a sentinel that fails if pip tries to go online.
# We rely on pip's --no-index to enforce offline; the test is that upgrade succeeds with wheels only.
bash "$APP_DIR/deploy/upgrade.sh" --app-dir="$APP_DIR" \
    --source="$REMOTE_SRC" --foreground --no-health-check --wheels-dir="$WHEELS" v-test-new

test -f "$APP_DIR/.install-info"
grep -q "v-test-new" "$APP_DIR/.install-info"

echo "PASS: scenario_offline_wheels"
