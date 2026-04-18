#!/usr/bin/env bash
# Run inside an integration container. Mounts the repo at /src.
# Install at v-test-old (current HEAD) then upgrade to v-test-new (fabricated
# commit on top). Verify APP_DIR is git-managed and at the target tag.
set -euo pipefail
source "$(dirname "$0")/_setup.sh"

bootstrap_test_repo
cd "$WORKDIR/repo"

APP_DIR=/opt/mymcp
AUTO_YES=true MCP_ADMIN_TOKEN=testtoken bash deploy/install.sh -y

test -f /etc/systemd/system/mymcp.service
test -d "$APP_DIR/.git"

bash "$APP_DIR/deploy/upgrade.sh" --app-dir="$APP_DIR" \
    --source="$REMOTE_SRC" --foreground --no-health-check v-test-new

test -d "$APP_DIR/.git"
CURRENT=$(git -C "$APP_DIR" describe --tags --always)
test "$CURRENT" = "v-test-new"
test -f "$APP_DIR/.install-info"

echo "PASS: scenario_fresh_upgrade"
