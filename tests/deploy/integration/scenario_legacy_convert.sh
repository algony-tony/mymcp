#!/usr/bin/env bash
# Simulate a legacy (rsync-based) install and verify upgrade converts it to git-managed.
set -euo pipefail
source "$(dirname "$0")/_setup.sh"

WORKDIR=$(mktemp -d)
cp -r /src "$WORKDIR/repo"
cd "$WORKDIR/repo"

# Create a fake "release tarball" (strip .git) and install from it
TARBALL_DIR="$WORKDIR/tarball"
rsync -a --exclude='.git' "$WORKDIR/repo/" "$TARBALL_DIR/"

APP_DIR=/opt/mymcp
# Install from non-git source
cd "$TARBALL_DIR"
AUTO_YES=true MCP_ADMIN_TOKEN=testtoken bash deploy/install.sh -y

# Verify: no .git in APP_DIR (rsync mode)
test ! -d "$APP_DIR/.git"
test -f "$APP_DIR/.install-info"
grep -q '"mode":"rsync"' "$APP_DIR/.install-info"

# Set up a git source for the upgrade to convert into
REMOTE_SRC="$WORKDIR/remote"
cp -r "$WORKDIR/repo" "$REMOTE_SRC"
git -C "$REMOTE_SRC" tag -f v-test-new HEAD

# Run upgrade — should convert APP_DIR to git tree
bash "$APP_DIR/deploy/upgrade.sh" --app-dir="$APP_DIR" \
    --source="$REMOTE_SRC" --foreground --no-health-check v-test-new

test -d "$APP_DIR/.git"
CURRENT=$(git -C "$APP_DIR" describe --tags --always)
test "$CURRENT" = "v-test-new"

echo "PASS: scenario_legacy_convert"
