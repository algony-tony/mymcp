#!/usr/bin/env bash
# Simulate a legacy (rsync-based) install and verify upgrade converts it
# to git-managed AND actually stops/installs/restarts the service via the
# detached-runner path (the real path MCP clients use — no --foreground).
#
# Regression guard for the Azure incident where legacy conversion in the
# parent process pre-advanced the disk to TARGET, causing the detached
# runner to hit the same-version guard (upgrade.sh:205) and exit 5 without
# ever running step_stop_service / step_start_service. The old PID kept
# running for days despite the banner saying "upgrade started".
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

# --- Upgrade via the REAL client path: no --foreground, runner detaches ---
# MYMCP_FORCE_FALLBACK=1 makes launch_detached use setsid+nohup instead of
# systemd-run (this container has systemd installed but not PID 1).
LOG_DIR="$WORKDIR/upgrade-logs"
MYMCP_FORCE_FALLBACK=1 MYMCP_LOG_DIR="$LOG_DIR" \
    bash "$APP_DIR/deploy/upgrade.sh" --app-dir="$APP_DIR" \
    --source="$REMOTE_SRC" --no-health-check v-test-new

# Parent returns immediately; poll the state file until the detached runner
# reaches a terminal step. Before the fix, state file is never written
# (runner exits before write_state is called) → this loop times out.
final_state=""
for _ in $(seq 1 60); do
    state_raw=$(cat "$APP_DIR/.upgrade-state" 2>/dev/null || true)
    step=$(echo "$state_raw" | sed -n 's/.*"step":"\([^"]*\)".*/\1/p')
    case "$step" in
        done|rolled-back|failed-manual-intervention)
            final_state="$step"; break ;;
    esac
    sleep 1
done

if [ "$final_state" != "done" ]; then
    echo "FAIL: detached runner did not reach 'done' (final=${final_state:-<no state file>})" >&2
    echo "--- state file ---" >&2
    cat "$APP_DIR/.upgrade-state" 2>&1 >&2 || echo "(missing)" >&2
    echo "--- detached runner logs ---" >&2
    ls -la "$LOG_DIR" >&2 || true
    for f in "$LOG_DIR"/upgrade-*.log; do
        [ -f "$f" ] && { echo "=== $f ==="; cat "$f"; } >&2
    done
    exit 1
fi

# --- Disk-level invariants (unchanged from previous scenario) ---
test -d "$APP_DIR/.git"
CURRENT=$(git -C "$APP_DIR" describe --tags --always)
test "$CURRENT" = "v-test-new"

# .install-info reflects the completed upgrade (written by step after success)
grep -q '"version":"v-test-new"' "$APP_DIR/.install-info"
grep -q '"upgraded_from"' "$APP_DIR/.install-info"

# --- Log-based invariants specific to the regression ---
log_file=$(ls -1t "$LOG_DIR"/upgrade-*.log 2>/dev/null | head -1 || true)
if [ -z "$log_file" ]; then
    echo "FAIL: no detached runner log found in $LOG_DIR" >&2
    exit 1
fi

# Anti-match: the same-version guard must NOT have fired.
if grep -q "target is same version as current" "$log_file"; then
    echo "FAIL: detached runner aborted with same-version guard;" >&2
    echo "      legacy conversion pre-advanced CURRENT to TARGET and --force" >&2
    echo "      was not propagated. stop/install/start never ran." >&2
    echo "--- $log_file ---" >&2
    cat "$log_file" >&2
    exit 1
fi

# Positive: runner completed the whole cascade.
if ! grep -q "=== Upgrade complete ===" "$log_file"; then
    echo "FAIL: detached runner did not reach 'Upgrade complete'" >&2
    cat "$log_file" >&2
    exit 1
fi

echo "PASS: scenario_legacy_convert"
