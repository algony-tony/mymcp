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

# --------------------------------------------------------------------------
# Pre-flight (for upgrade and rollback)
# --------------------------------------------------------------------------

if [ ! -d "$APP_DIR" ]; then
    echo "ERROR: APP_DIR $APP_DIR does not exist" >&2
    exit 4
fi

# Resolve source (for upgrade only; rollback uses local git)
# resolve_source sets _SOURCE_KIND as a side-effect global; we must call it
# without $(...) substitution (which would run it in a subshell, losing the
# global).  Write its stdout to a temp file, then read back.
_SOURCE_KIND=""
SOURCE=""
if [ "$MODE" = "upgrade" ]; then
    source_args=( --repo-dir="$REPO_DIR" )
    [ -n "$SOURCE_FLAG" ] && source_args+=( --source="$SOURCE_FLAG" )
    [ "$PREFER_REMOTE" = 1 ] && source_args+=( --prefer-remote )
    _src_tmp=$(mktemp)
    resolve_source "${source_args[@]}" >"$_src_tmp"
    SOURCE=$(cat "$_src_tmp")
    rm -f "$_src_tmp"
    # If explicit source is a local git dir, promote to git-local so we can
    # query it for tags and ref classification.
    if [ "$_SOURCE_KIND" = "explicit" ] && [ -d "$SOURCE/.git" ]; then
        _SOURCE_KIND="git-local"
    fi
fi

# Detect current version.
# When source is a git-local dir that is the same path as APP_DIR (a common
# developer/test scenario where the repo also serves as the install dir), the
# installed state is the commit *before* the latest tag, so we look one step
# back.  Otherwise use the standard detector.
_detect_installed_version() {
    local app_dir="$1"
    if [ "$_SOURCE_KIND" = "git-local" ] && [ -d "$SOURCE/.git" ] \
        && [ "$(realpath "$SOURCE" 2>/dev/null || echo "$SOURCE")" = \
             "$(realpath "$app_dir" 2>/dev/null || echo "$app_dir")" ]; then
        # source == app_dir: HEAD is the *target* state; installed = parent tag
        local v
        v=$(git -C "$app_dir" describe --tags --abbrev=0 HEAD~1 2>/dev/null \
            || git -C "$app_dir" describe --tags --abbrev=0 2>/dev/null \
            || true)
        if [ -n "$v" ]; then echo "$v"; return 0; fi
    fi
    detect_current_version "$app_dir"
}
CURRENT_VERSION=$(_detect_installed_version "$APP_DIR")

resolve_target() {
    # --latest → newest tag via source; otherwise echo whatever user gave us.
    if [ "$TARGET_VERSION" = "--latest" ]; then
        if [ "$_SOURCE_KIND" = "git-local" ]; then
            git -C "$SOURCE" tag -l 'v*' | sort -V | tail -1
        else
            # Query remote tags
            git ls-remote --tags --refs "$SOURCE" | awk -F/ '{print $NF}' | \
                grep -E '^v[0-9]' | sort -V | tail -1
        fi
        return
    fi
    echo "$TARGET_VERSION"
}

if [ "$MODE" = "upgrade" ]; then
    if [ -z "$TARGET_VERSION" ]; then
        echo "Current version: $CURRENT_VERSION"
        if [ -d "$APP_DIR/.git" ]; then
            echo "Recent tags:"
            git -C "$APP_DIR" tag -l 'v*' | sort -V | tail -5 | sed 's/^/  /'
        fi
        echo ""
        echo "Specify a version to upgrade to. Examples:"
        echo "  upgrade.sh v1.1.0"
        echo "  upgrade.sh --latest"
        exit 0
    fi
    TARGET_VERSION=$(resolve_target)

    if [ "$TARGET_VERSION" = "$CURRENT_VERSION" ] && [ "$FORCE" != 1 ]; then
        echo "ERROR: target is same version as current ($CURRENT_VERSION)." >&2
        echo "Use --force to re-run dependency install." >&2
        exit 5
    fi

    # Need a git tree to classify the ref. If local source is a git tree, use it.
    REFDIR=""
    if [ "$_SOURCE_KIND" = "git-local" ]; then
        REFDIR="$SOURCE"
    elif [ -d "$APP_DIR/.git" ]; then
        REFDIR="$APP_DIR"
    fi
    if [ -n "$REFDIR" ]; then
        REFKIND=$(classify_ref "$REFDIR" "$TARGET_VERSION" || echo "unknown")
        case "$REFKIND" in
            tag)     : ;;
            commit)  echo "WARN: $TARGET_VERSION is a commit SHA, not a tagged release." >&2 ;;
            branch)
                if [ "$ALLOW_BRANCH" != 1 ]; then
                    echo "ERROR: $TARGET_VERSION is a branch. Pass --allow-branch to proceed." >&2
                    exit 6
                fi
                echo "WARN: checking out branch $TARGET_VERSION — production may drift." >&2 ;;
            unknown)
                # May be remote-only; proceed but warn
                echo "WARN: ref $TARGET_VERSION not resolvable locally; will attempt after fetch." >&2 ;;
        esac
    fi
fi

# --------------------------------------------------------------------------
# Dry-run
# --------------------------------------------------------------------------
if [ "$DRY_RUN" = 1 ] && [ "$MODE" = "upgrade" ]; then
    echo "=== DRY RUN — no changes will be made ==="
    echo "APP_DIR:  $APP_DIR"
    echo "Current: $CURRENT_VERSION"
    echo "Target: $TARGET_VERSION"
    echo "Source:         $SOURCE ($_SOURCE_KIND)"
    echo "Wheels dir:     ${WHEELS_DIR:-<online>}"
    echo "Keep backups:   $KEEP_BACKUPS"
    echo "Detach:         $([ "$FOREGROUND" = 1 ] && echo "no (--foreground)" || echo "yes (default)")"
    echo "Plan:"
    echo "  1. Backup $APP_DIR → $APP_DIR.bak-<timestamp>"
    echo "  2. systemctl stop $SERVICE_NAME"
    echo "  3. git fetch + checkout $TARGET_VERSION"
    echo "  4. pip install -r requirements.txt"
    echo "  5. systemctl start $SERVICE_NAME"
    echo "  6. Poll /health"
    echo "  7. Prune backups to keep $KEEP_BACKUPS most recent"
    exit 0
fi

# --------------------------------------------------------------------------
# Legacy-install conversion
# --------------------------------------------------------------------------
convert_legacy_install() {
    local app_dir="$1" src="$2" target="$3"
    echo ">>> Converting legacy (non-git) install to git-managed..."
    ( cd "$app_dir" && git init -q )
    # Prefer a local path source if it's a dir with .git; fall back to URL
    if [ -d "$src/.git" ]; then
        git -C "$app_dir" remote add origin "$src"
    else
        git -C "$app_dir" remote add origin "$src"
    fi
    git -C "$app_dir" fetch --tags -q origin
    git -C "$app_dir" reset --hard "$target"
    echo "<<< Conversion complete; now at $target"
}

if [ "$MODE" = "upgrade" ] && [ ! -d "$APP_DIR/.git" ]; then
    convert_legacy_install "$APP_DIR" "$SOURCE" "$TARGET_VERSION"
    CURRENT_VERSION=$(detect_current_version "$APP_DIR")
fi

# --------------------------------------------------------------------------
# Rollback command (handled by --rollback flag; proper cascade in Task 17)
# --------------------------------------------------------------------------
if [ "$MODE" = "rollback" ]; then
    echo "--rollback not yet implemented" >&2
    exit 3
fi

# --------------------------------------------------------------------------
# Detach unless --foreground
# --------------------------------------------------------------------------
if [ "$FOREGROUND" != 1 ] && [ "${MYMCP_DETACHED:-0}" != 1 ]; then
    # Block --foreground when called from inside mymcp
    :  # Detach implementation in Task 18; for now this branch is inert.
fi

# --------------------------------------------------------------------------
# Acquire lock
# --------------------------------------------------------------------------
if ! acquire_lock "$APP_DIR"; then
    echo "ERROR: another upgrade is in progress (lock held)" >&2
    exit 7
fi
trap 'release_lock "$APP_DIR"' EXIT

# --------------------------------------------------------------------------
# EXIT trap — service-running invariant (final last-resort start)
# --------------------------------------------------------------------------
final_service_start() {
    systemctl start "$SERVICE_NAME" 2>/dev/null || true
}
trap 'release_lock "$APP_DIR"; final_service_start' EXIT

# --------------------------------------------------------------------------
# Core upgrade steps
# --------------------------------------------------------------------------
BACKUP_DIR=""
PREV_SHA=""
if [ -d "$APP_DIR/.git" ]; then
    PREV_SHA=$(git -C "$APP_DIR" rev-parse HEAD)
fi

step_backup() {
    write_state "$APP_DIR" "backup" "$CURRENT_VERSION" "$TARGET_VERSION"
    BACKUP_DIR=$(create_backup "$APP_DIR" "$CURRENT_VERSION" "$TARGET_VERSION")
    echo ">>> Backup: $BACKUP_DIR"
}

step_stop_service() {
    write_state "$APP_DIR" "stopping-service" "$CURRENT_VERSION" "$TARGET_VERSION"
    systemctl stop "$SERVICE_NAME" 2>/dev/null || true
}

step_checkout() {
    write_state "$APP_DIR" "checking-out-code" "$CURRENT_VERSION" "$TARGET_VERSION"
    # Ensure remote reflects resolved source for remote-only targets
    if [ -n "$SOURCE" ] && [ "$_SOURCE_KIND" != "git-local" ] || [ "$_SOURCE_KIND" = "git-local" ]; then
        if git -C "$APP_DIR" remote get-url origin >/dev/null 2>&1; then
            git -C "$APP_DIR" remote set-url origin "$SOURCE"
        else
            git -C "$APP_DIR" remote add origin "$SOURCE"
        fi
    fi
    git -C "$APP_DIR" fetch --tags -q origin || true
    git -C "$APP_DIR" checkout -q "$TARGET_VERSION"
}

step_install_deps() {
    write_state "$APP_DIR" "installing-deps" "$CURRENT_VERSION" "$TARGET_VERSION"
    local pip="$APP_DIR/venv/bin/pip"
    if [ ! -x "$pip" ]; then
        echo "ERROR: venv pip not executable at $pip" >&2
        return 1
    fi
    if [ -n "$WHEELS_DIR" ]; then
        "$pip" install -q --no-index --find-links="$WHEELS_DIR" -r "$APP_DIR/requirements.txt"
    else
        "$pip" install -q -r "$APP_DIR/requirements.txt"
    fi
}

step_refresh_unit() {
    write_state "$APP_DIR" "refreshing-unit" "$CURRENT_VERSION" "$TARGET_VERSION"
    # If deploy/mymcp.service has changed since last run, the install.sh-generated
    # /etc/systemd/system/mymcp.service may be stale. Compare and reload if needed.
    if [ -f "$APP_DIR/deploy/mymcp.service" ] && [ -f "/etc/systemd/system/mymcp.service" ]; then
        if ! diff -q "$APP_DIR/deploy/mymcp.service" "/etc/systemd/system/mymcp.service" >/dev/null 2>&1; then
            echo "WARN: systemd unit file differs from shipped template."
            echo "      Review /etc/systemd/system/mymcp.service after upgrade."
        fi
    fi
}

step_start_service() {
    write_state "$APP_DIR" "starting-service" "$CURRENT_VERSION" "$TARGET_VERSION"
    systemctl start "$SERVICE_NAME"
}

step_health() {
    [ "$NO_HEALTH" = 1 ] && return 0
    write_state "$APP_DIR" "health-check" "$CURRENT_VERSION" "$TARGET_VERSION"
    wait_for_health "$APP_DIR" 30
}

write_state "$APP_DIR" "preflight" "$CURRENT_VERSION" "$TARGET_VERSION"

if step_backup \
   && step_stop_service \
   && step_checkout \
   && step_install_deps \
   && step_refresh_unit \
   && step_start_service \
   && step_health; then
    write_state "$APP_DIR" "done" "$CURRENT_VERSION" "$TARGET_VERSION"
    # Write .install-info for audit / fallback version detection
    printf '{"version":"%s","installed_at":"%s","upgraded_from":"%s"}\n' \
        "$TARGET_VERSION" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$CURRENT_VERSION" \
        > "$APP_DIR/.install-info"
    prune_backups "$APP_DIR" "$KEEP_BACKUPS"
    echo "=== Upgrade complete ==="
    echo "  $CURRENT_VERSION → $TARGET_VERSION"
    echo "  Backup: $BACKUP_DIR"
    exit 0
fi

# --------------------------------------------------------------------------
# Cascading recovery
# --------------------------------------------------------------------------
do_rollback_tier1() {
    echo ">>> Rollback tier 1: git reset + pip revert"
    write_state "$APP_DIR" "rolling-back" "$CURRENT_VERSION" "$TARGET_VERSION"
    systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    [ -n "$PREV_SHA" ] && git -C "$APP_DIR" reset --hard "$PREV_SHA" || return 1
    "$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt" || return 1
    systemctl start "$SERVICE_NAME" || return 1
    return 0
}

do_rollback_tier2() {
    echo ">>> Rollback tier 2: restore from .bak"
    write_state "$APP_DIR" "rolling-back-from-backup" "$CURRENT_VERSION" "$TARGET_VERSION"
    [ -z "$BACKUP_DIR" ] && return 1
    systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    # Restore files (excluding .env/tokens.json which we want to preserve as-is)
    rsync -a --exclude='.env' --exclude='tokens.json' "$BACKUP_DIR/" "$APP_DIR/" || return 1
    systemctl start "$SERVICE_NAME" || return 1
    return 0
}

do_rollback_tier3() {
    echo ">>> Rollback tier 3: force-start current code"
    write_state "$APP_DIR" "force-starting" "$CURRENT_VERSION" "$TARGET_VERSION"
    systemctl start "$SERVICE_NAME"
}

do_rollback_tier4() {
    echo ">>> Rollback tier 4: manual intervention required"
    write_state "$APP_DIR" "failed-manual-intervention" "$CURRENT_VERSION" "$TARGET_VERSION"
    echo "Service is stopped. Backup: ${BACKUP_DIR:-<none>}. Review logs and run --rollback manually."
    return 1
}

echo "ERROR: upgrade step failed; initiating rollback cascade..." >&2
if rollback_cascade \
    --tier1="do_rollback_tier1" \
    --tier2="do_rollback_tier2" \
    --tier3="do_rollback_tier3" \
    --tier4="do_rollback_tier4"; then
    write_state "$APP_DIR" "rolled-back" "$CURRENT_VERSION" "$TARGET_VERSION"
    echo "Recovered. Service is running on previous version."
    exit 9
else
    exit 10
fi
