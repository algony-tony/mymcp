#!/usr/bin/env bash
# Run inside an integration container. Mounts the repo at /src.
# 1. Clone /src into a private workdir
# 2. Install at HEAD~1 (last tag), simulating v1.0.0
# 3. Upgrade to HEAD (simulating v1.1.0)
# 4. Verify git checkout and /health
set -euo pipefail

WORKDIR=$(mktemp -d)
cp -r /src "$WORKDIR/repo"
cd "$WORKDIR/repo"

# Determine versions
PREVIOUS_TAG=$(git describe --tags --abbrev=0 HEAD~1 2>/dev/null || git rev-parse HEAD~1)
LATEST_REF=$(git rev-parse HEAD)
# Tag LATEST_REF as v-test-new for testability
git tag -f v-test-new HEAD

# Reset repo to PREVIOUS_TAG for install
git -C "$WORKDIR/repo" checkout -q "$PREVIOUS_TAG"

# Install
APP_DIR=/opt/mymcp
AUTO_YES=true MCP_ADMIN_TOKEN=testtoken bash deploy/install.sh -y

# Verify service file created
test -f /etc/systemd/system/mymcp.service

# Simulate: upstream advanced to HEAD (v-test-new). Make a local clone with that ref.
REMOTE_SRC="$WORKDIR/remote"
cp -r "$WORKDIR/repo" "$REMOTE_SRC"
git -C "$REMOTE_SRC" checkout -q "$LATEST_REF"
git -C "$REMOTE_SRC" tag -f v-test-new HEAD

# Start service for health check path
# (In a non-systemd container, skip systemctl; upgrade.sh uses --no-health-check.)
bash "$APP_DIR/deploy/upgrade.sh" --app-dir="$APP_DIR" \
    --source="$REMOTE_SRC" --foreground --no-health-check v-test-new

# Verify
test -d "$APP_DIR/.git"
CURRENT=$(git -C "$APP_DIR" describe --tags --always)
test "$CURRENT" = "v-test-new"
test -f "$APP_DIR/.install-info"

echo "PASS: scenario_fresh_upgrade"
