#!/usr/bin/env bash
# Simulate a legacy (rsync-based) install and verify upgrade converts it
# to git-managed.
set -euo pipefail
source "$(dirname "$0")/_setup.sh"

bootstrap_test_repo

# Create a fake release tarball (strip .git) and install from it.
# This forces install.sh's rsync fallback path instead of git clone.
TARBALL_DIR="$WORKDIR/tarball"
rsync -a --exclude='.git' "$WORKDIR/repo/" "$TARBALL_DIR/"

APP_DIR=/opt/mymcp
cd "$TARBALL_DIR"
AUTO_YES=true MCP_ADMIN_TOKEN=testtoken bash deploy/install.sh -y

test ! -d "$APP_DIR/.git"
test -f "$APP_DIR/.install-info"
grep -q '"mode":"rsync"' "$APP_DIR/.install-info"

# Upgrade from a git source — upgrade.sh should convert APP_DIR to git tree.
bash "$APP_DIR/deploy/upgrade.sh" --app-dir="$APP_DIR" \
    --source="$REMOTE_SRC" --foreground --no-health-check v-test-new

test -d "$APP_DIR/.git"
CURRENT=$(git -C "$APP_DIR" describe --tags --always)
test "$CURRENT" = "v-test-new"

echo "PASS: scenario_legacy_convert"
