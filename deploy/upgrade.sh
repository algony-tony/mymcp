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

# --- Actual upgrade/rollback path continues in next task ---
echo "upgrade execution not yet implemented; pre-flight passed." >&2
exit 3
