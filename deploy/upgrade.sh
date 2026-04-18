#!/usr/bin/env bash
# Re-exec under bash if invoked via sh/dash
if [ -z "${BASH_VERSION:-}" ]; then
    exec bash "$0" "$@"
fi
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/install_lib.sh"

REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="${MYMCP_LOG_DIR:-/var/log/mymcp}"
SERVICE_NAME="mymcp"

print_help() {
    cat <<'EOF'
Usage: upgrade.sh [VERSION] [OPTIONS]

VERSION (positional):
  v1.1.0              Target tag (recommended)
  --latest            Latest tag available
  <commit-sha>        Specific commit (warns; requires confirmation)
  <branch>            Branch tip (requires --allow-branch)
  (none)              Show current version and recent tags; no action.

Options:
  --app-dir=PATH      Override install dir (default: auto-detect from systemd)
  --source=URL|PATH   Explicit source (URL or local git path)
  --prefer-remote     Try GitHub before local paths
  --wheels-dir=PATH   Offline pip install from local wheel directory
  --keep-backups=N    Backup retention (default: 3)
  --allow-branch      Permit branch checkout (dangerous)
  --force             Allow same-version reinstall, overwrite dirty tree
  --dry-run           Print plan without executing
  --no-health-check   Skip post-start /health probe
  --foreground        Run synchronously (default: detach)
  --no-detach         Alias for --foreground
  --rollback          Revert to last backup (uses most-recent .bak dir)
  --current           Print installed version and exit
  --list              Print available tags and exit
  --status            Print status of in-progress upgrade
  --logs [-f]         Print upgrade logs (-f to follow)
  -h, --help          Show this help
EOF
}

# Parse args
TARGET_VERSION=""
APP_DIR_FLAG=""
SOURCE_FLAG=""
WHEELS_DIR=""
KEEP_BACKUPS="3"
PREFER_REMOTE=0
ALLOW_BRANCH=0
FORCE=0
DRY_RUN=0
NO_HEALTH=0
FOREGROUND=0
MODE="upgrade"  # upgrade | rollback | current | list | status | logs | help
LOGS_FOLLOW=0

while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help)       MODE="help"; shift ;;
        --current)       MODE="current"; shift ;;
        --list)          MODE="list"; shift ;;
        --status)        MODE="status"; shift ;;
        --logs)          MODE="logs"; shift
                         if [ "${1:-}" = "-f" ]; then LOGS_FOLLOW=1; shift; fi ;;
        --rollback)      MODE="rollback"; shift ;;
        --dry-run)       DRY_RUN=1; shift ;;
        --latest)        TARGET_VERSION="--latest"; shift ;;
        --prefer-remote) PREFER_REMOTE=1; shift ;;
        --allow-branch)  ALLOW_BRANCH=1; shift ;;
        --force)         FORCE=1; shift ;;
        --no-health-check) NO_HEALTH=1; shift ;;
        --foreground|--no-detach) FOREGROUND=1; shift ;;
        --app-dir=*)     APP_DIR_FLAG="${1#--app-dir=}"; shift ;;
        --source=*)      SOURCE_FLAG="${1#--source=}"; shift ;;
        --wheels-dir=*)  WHEELS_DIR="${1#--wheels-dir=}"; shift ;;
        --keep-backups=*) KEEP_BACKUPS="${1#--keep-backups=}"; shift ;;
        --detach-runner) MODE="detach-runner"; shift ;;
        --*)             echo "Unknown option: $1" >&2; exit 2 ;;
        *)               if [ -z "$TARGET_VERSION" ]; then TARGET_VERSION="$1"; shift
                         else echo "Unexpected extra argument: $1" >&2; exit 2; fi ;;
    esac
done

# Resolve APP_DIR
APP_DIR=$(discover_app_dir --app-dir="$APP_DIR_FLAG")

case "$MODE" in
    help)    print_help; exit 0 ;;
    current) detect_current_version "$APP_DIR"; exit 0 ;;
    list)
        if [ -d "$APP_DIR/.git" ]; then
            git -C "$APP_DIR" tag -l 'v*' 2>/dev/null | sort -V || true
        else
            echo "(no git metadata; install.sh ran in rsync fallback mode)"
        fi
        exit 0 ;;
    status)
        if ! state=$(read_state "$APP_DIR" 2>/dev/null); then
            echo "no upgrade in progress"
            exit 0
        fi
        echo "$state"
        exit 0 ;;
    logs)
        # Find most recent log file
        local_latest=$(ls -1t "$LOG_DIR"/upgrade-*.log 2>/dev/null | head -1 || true)
        if [ -z "$local_latest" ]; then
            echo "no upgrade logs found in $LOG_DIR"
            exit 0
        fi
        if [ "$LOGS_FOLLOW" = 1 ]; then
            tail -f "$local_latest"
        else
            tail -n 200 "$local_latest"
        fi
        exit 0 ;;
esac

# --- Upgrade/rollback paths handled in subsequent tasks ---
echo "upgrade/rollback mode not yet implemented in this commit" >&2
exit 3
