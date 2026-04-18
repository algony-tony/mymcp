#!/usr/bin/env bash
# Shared functions for install.sh — sourced by both the installer and tests.

# ---------------------------------------------------------------------------
# confirm prompt [default]
#   Yes/no prompt. AUTO_YES=true → return default without prompting.
# ---------------------------------------------------------------------------
confirm() {
    local prompt="$1" default="${2:-Y}"
    if [ "$AUTO_YES" = true ]; then
        [ "$default" = "Y" ] && return 0 || return 1
    fi
    if [ "$default" = "Y" ]; then
        read -rp "${prompt} [Y/n]: " answer
        case "${answer,,}" in
            n|no) return 1 ;;
            *)    return 0 ;;
        esac
    else
        read -rp "${prompt} [y/N]: " answer
        case "${answer,,}" in
            y|yes) return 0 ;;
            *)     return 1 ;;
        esac
    fi
}

# ---------------------------------------------------------------------------
# prompt_value prompt default
#   Prompt for a value with a default. AUTO_YES=true → return default.
# ---------------------------------------------------------------------------
prompt_value() {
    local prompt="$1" default="$2"
    if [ "$AUTO_YES" = true ]; then
        echo "$default"; return
    fi
    read -rp "${prompt} [${default}]: " value
    echo "${value:-$default}"
}

# ---------------------------------------------------------------------------
# find_python [min_minor]
#   Find highest available Python >= 3.min_minor (default 11).
# ---------------------------------------------------------------------------
find_python() {
    local min_minor="${1:-11}"
    for minor in 14 13 12 11; do
        [ "$minor" -lt "$min_minor" ] && continue
        for cmd in "python3.${minor}" "python${minor}"; do
            if command -v "$cmd" &>/dev/null; then
                echo "$cmd"; return
            fi
        done
    done
    for cmd in python3 python; do
        if command -v "$cmd" &>/dev/null; then
            ver=$("$cmd" -c 'import sys; print(sys.version_info.minor)' 2>/dev/null || echo 0)
            if [ "$ver" -ge "$min_minor" ]; then
                echo "$cmd"; return
            fi
        fi
    done
    return 1
}

# ---------------------------------------------------------------------------
# validate_app_dir path
#   Validate install path is absolute. Returns 1 on failure.
# ---------------------------------------------------------------------------
validate_app_dir() {
    case "$1" in
        /*) return 0 ;;
        *)  echo "ERROR: Install path must be absolute."; return 1 ;;
    esac
}

# ---------------------------------------------------------------------------
# write_state app_dir step [from] [to]
#   Atomically write JSON state file. Preserves started_at across calls.
# ---------------------------------------------------------------------------
write_state() {
    local app_dir="$1" step="$2" from="${3:-}" to="${4:-}"
    local state_file="$app_dir/.upgrade-state"
    local tmp="$state_file.tmp.$$"
    local now
    now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    local started_at="$now"
    if [ -f "$state_file" ]; then
        local existing
        existing=$(sed -n 's/.*"started_at":"\([^"]*\)".*/\1/p' "$state_file")
        [ -n "$existing" ] && started_at="$existing"
    fi
    printf '{"pid":%d,"from":"%s","to":"%s","step":"%s","started_at":"%s","updated_at":"%s"}\n' \
        "$$" "$from" "$to" "$step" "$started_at" "$now" > "$tmp"
    mv -f "$tmp" "$state_file"
}

# ---------------------------------------------------------------------------
# read_state app_dir
#   Print the state file contents. Return 1 if no state file.
# ---------------------------------------------------------------------------
read_state() {
    local app_dir="$1"
    local state_file="$app_dir/.upgrade-state"
    [ -f "$state_file" ] || return 1
    cat "$state_file"
}

# ---------------------------------------------------------------------------
# acquire_lock app_dir
#   Acquire exclusive lock on $app_dir/.upgrade.lock (non-blocking).
#   If lock file contains a dead PID, remove it and retry once.
#   The lock FD is assigned to global _UPGRADE_LOCK_FD; release with
#   release_lock.
# ---------------------------------------------------------------------------
acquire_lock() {
    local app_dir="$1"
    local lockfile="$app_dir/.upgrade.lock"

    # Clean stale lock if PID is dead
    if [ -f "$lockfile" ]; then
        local pid
        pid=$(cat "$lockfile" 2>/dev/null || echo "")
        if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
            rm -f "$lockfile"
        fi
    fi

    exec {_UPGRADE_LOCK_FD}>"$lockfile"
    if ! flock -n -x "$_UPGRADE_LOCK_FD"; then
        exec {_UPGRADE_LOCK_FD}>&-
        return 1
    fi
    echo "$$" >&"$_UPGRADE_LOCK_FD"
    return 0
}

# ---------------------------------------------------------------------------
# release_lock app_dir
#   Release previously-acquired lock.
# ---------------------------------------------------------------------------
release_lock() {
    local app_dir="$1"
    [ -n "${_UPGRADE_LOCK_FD:-}" ] && exec {_UPGRADE_LOCK_FD}>&- 2>/dev/null || true
    rm -f "$app_dir/.upgrade.lock"
}

# ---------------------------------------------------------------------------
# detect_current_version app_dir
#   Returns installed version. Tries git describe, then .install-info, else 'unknown'.
# ---------------------------------------------------------------------------
detect_current_version() {
    local app_dir="$1"
    if [ -d "$app_dir/.git" ]; then
        local v
        v=$(git -C "$app_dir" describe --tags --always 2>/dev/null || true)
        if [ -n "$v" ]; then
            echo "$v"
            return 0
        fi
    fi
    if [ -f "$app_dir/.install-info" ]; then
        local v
        v=$(sed -n 's/.*"version":"\([^"]*\)".*/\1/p' "$app_dir/.install-info")
        if [ -n "$v" ]; then
            echo "$v"
            return 0
        fi
    fi
    echo "unknown"
    return 0
}

# ---------------------------------------------------------------------------
# is_under_mymcp
#   Walk ancestor PIDs via /proc. Return 0 if any ancestor's cmdline contains
#   uvicorn main:app or a path ending in /mymcp/venv. MYMCP_FAKE_UNDER=1
#   short-circuits to true (for tests on systems where /proc differs).
# ---------------------------------------------------------------------------
is_under_mymcp() {
    if [ "${MYMCP_FAKE_UNDER:-0}" = "1" ]; then
        return 0
    fi
    local pid=$PPID
    local depth=0
    while [ "$pid" -gt 1 ] && [ "$depth" -lt 20 ]; do
        if [ -r "/proc/$pid/cmdline" ]; then
            local cmd
            cmd=$(tr '\0' ' ' < "/proc/$pid/cmdline")
            if [[ "$cmd" == *"uvicorn"*"main:app"* ]] || [[ "$cmd" == *"/mymcp/venv/"* ]]; then
                return 0
            fi
        fi
        if [ -r "/proc/$pid/stat" ]; then
            pid=$(awk '{print $4}' "/proc/$pid/stat" 2>/dev/null)
            [ -z "$pid" ] && break
        else
            break
        fi
        depth=$((depth + 1))
    done
    return 1
}

MYMCP_DEFAULT_REMOTE="${MYMCP_DEFAULT_REMOTE:-https://github.com/algony-tony/mymcp.git}"

# ---------------------------------------------------------------------------
# resolve_source [--source=X] [--repo-dir=Y] [--prefer-remote]
#   Resolve code source via fallback chain. Prints the chosen source.
#   Sets global _SOURCE_KIND to 'git-local', 'git-remote', or 'rsync'.
# ---------------------------------------------------------------------------
resolve_source() {
    local explicit="" repo_dir="" prefer_remote=0
    for arg in "$@"; do
        case "$arg" in
            --source=*)      explicit="${arg#--source=}" ;;
            --repo-dir=*)    repo_dir="${arg#--repo-dir=}" ;;
            --prefer-remote) prefer_remote=1 ;;
        esac
    done

    if [ -n "$explicit" ]; then
        _SOURCE_KIND="explicit"
        echo "$explicit"
        return 0
    fi

    if [ "$prefer_remote" = 0 ] && [ -n "$repo_dir" ] && [ -d "$repo_dir/.git" ]; then
        _SOURCE_KIND="git-local"
        echo "$repo_dir"
        return 0
    fi

    _SOURCE_KIND="git-remote"
    echo "$MYMCP_DEFAULT_REMOTE"
}

# ---------------------------------------------------------------------------
# classify_ref app_dir ref
#   Print 'tag', 'branch', 'commit', or 'unknown'. Exit 0 unless unknown.
# ---------------------------------------------------------------------------
classify_ref() {
    local app_dir="$1" ref="$2"
    if git -C "$app_dir" show-ref --verify --quiet "refs/tags/$ref"; then
        echo "tag"
        return 0
    fi
    if git -C "$app_dir" show-ref --verify --quiet "refs/heads/$ref" \
        || git -C "$app_dir" show-ref --verify --quiet "refs/remotes/origin/$ref"; then
        echo "branch"
        return 0
    fi
    if git -C "$app_dir" rev-parse --verify --quiet "${ref}^{commit}" >/dev/null; then
        echo "commit"
        return 0
    fi
    echo "unknown"
    return 1
}

# ---------------------------------------------------------------------------
# create_backup app_dir from_version [to_version]
#   Snapshot $app_dir to $app_dir.bak-<timestamp>/ excluding venv and .git.
#   Prints the backup path on success.
# ---------------------------------------------------------------------------
create_backup() {
    local app_dir="$1" from_version="$2" to_version="${3:-}"
    local ts
    ts=$(date +%Y%m%d-%H%M%S)
    local bak="${app_dir}.bak-${ts}"
    mkdir -p "$bak"
    rsync -a --exclude='venv' --exclude='.git' "$app_dir/" "$bak/"
    local sha=""
    if [ -d "$app_dir/.git" ]; then
        sha=$(git -C "$app_dir" rev-parse HEAD 2>/dev/null || echo "")
    fi
    printf '{"from_version":"%s","to_version":"%s","from_sha":"%s","created_at":"%s"}\n' \
        "$from_version" "$to_version" "$sha" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$bak/.backup-info"
    echo "$bak"
}

# ---------------------------------------------------------------------------
# prune_backups app_dir keep
#   Keep the most recent $keep backups of the form $app_dir.bak-*; delete the rest.
# ---------------------------------------------------------------------------
prune_backups() {
    local app_dir="$1" keep="${2:-3}"
    local parent base
    parent=$(dirname "$app_dir")
    base=$(basename "$app_dir")
    local -a all
    # shellcheck disable=SC2207
    all=( $(ls -1d "$parent/${base}.bak-"*/ 2>/dev/null | sort) )
    local count=${#all[@]}
    local excess=$((count - keep))
    [ "$excess" -le 0 ] && return 0
    local i=0
    for d in "${all[@]}"; do
        [ "$i" -ge "$excess" ] && break
        rm -rf "${d%/}"
        i=$((i + 1))
    done
}

# ---------------------------------------------------------------------------
# wait_for_health app_dir [timeout_seconds]
#   Poll /health endpoint. Reads MCP_HOST/MCP_PORT from env or $app_dir/.env.
#   Defaults: host=127.0.0.1 port=8765. Returns 0 on 200, 1 on timeout.
# ---------------------------------------------------------------------------
wait_for_health() {
    local app_dir="$1" timeout="${2:-30}"
    local host="${MCP_HOST:-}" port="${MCP_PORT:-}"
    if [ -z "$host" ] || [ -z "$port" ]; then
        if [ -f "$app_dir/.env" ]; then
            [ -z "$host" ] && host=$(sed -n 's/^MCP_HOST=//p' "$app_dir/.env" 2>/dev/null || true)
            [ -z "$port" ] && port=$(sed -n 's/^MCP_PORT=//p' "$app_dir/.env" 2>/dev/null || true)
        fi
    fi
    [ -z "$host" ] && host="127.0.0.1"
    [ -z "$port" ] && port="8765"
    # 0.0.0.0 means "listen on all" — poll localhost
    [ "$host" = "0.0.0.0" ] && host="127.0.0.1"

    local deadline=$(( $(date +%s) + timeout ))
    while [ "$(date +%s)" -lt "$deadline" ]; do
        if curl -sf -m 2 "http://${host}:${port}/health" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    return 1
}

# ---------------------------------------------------------------------------
# discover_app_dir [--app-dir=PATH] [--unit-file=PATH]
#   Discover APP_DIR via flag > unit file WorkingDirectory > /opt/mymcp.
# ---------------------------------------------------------------------------
discover_app_dir() {
    local explicit="" unit="/etc/systemd/system/mymcp.service"
    for arg in "$@"; do
        case "$arg" in
            --app-dir=*)   explicit="${arg#--app-dir=}" ;;
            --unit-file=*) unit="${arg#--unit-file=}" ;;
        esac
    done
    if [ -n "$explicit" ]; then
        echo "$explicit"
        return 0
    fi
    if [ -f "$unit" ]; then
        local wd
        wd=$(sed -n 's/^WorkingDirectory=//p' "$unit" | head -1)
        if [ -n "$wd" ]; then
            echo "$wd"
            return 0
        fi
    fi
    echo "/opt/mymcp"
}

# ---------------------------------------------------------------------------
# launch_detached script_path [--log-dir=DIR] [--unit-name=NAME] [-- args...]
#   Launch script detached from this process. Prefers systemd-run, falls back
#   to setsid+nohup+disown. Prints 'UNIT name' or 'LOG path' on success.
#   Args after `--` are passed to the script.
# ---------------------------------------------------------------------------
launch_detached() {
    local script="$1"; shift
    local logdir="/var/log/mymcp" unit="mymcp-upgrade"
    local -a passthrough=()
    local in_passthrough=0
    for arg in "$@"; do
        if [ "$in_passthrough" = 1 ]; then
            passthrough+=( "$arg" )
            continue
        fi
        case "$arg" in
            --log-dir=*)   logdir="${arg#--log-dir=}" ;;
            --unit-name=*) unit="${arg#--unit-name=}" ;;
            --)            in_passthrough=1 ;;
            *)             passthrough+=( "$arg" ) ;;
        esac
    done
    mkdir -p "$logdir"
    local ts
    ts=$(date +%Y%m%d-%H%M%S)
    local logfile="$logdir/upgrade-$ts.log"

    local use_systemd=1
    [ "${MYMCP_FORCE_FALLBACK:-0}" = "1" ] && use_systemd=0
    if [ "$use_systemd" = 1 ]; then
        if ! command -v systemd-run >/dev/null 2>&1; then
            use_systemd=0
        elif ! systemctl is-system-running >/dev/null 2>&1; then
            use_systemd=0
        fi
    fi

    if [ "$use_systemd" = 1 ]; then
        systemd-run --unit="$unit" \
            --property=StandardOutput=append:"$logfile" \
            --property=StandardError=append:"$logfile" \
            --setenv=MYMCP_DETACHED=1 \
            --no-block --quiet \
            "$script" "${passthrough[@]}"
        echo "UNIT $unit"
        return 0
    fi
    # Fallback: setsid + nohup + disown (env inheritance is automatic)
    ( MYMCP_DETACHED=1; export MYMCP_DETACHED
      setsid nohup "$script" "${passthrough[@]}" >>"$logfile" 2>&1 </dev/null & disown ) &
    sleep 0.05
    echo "LOG $logfile"
}

# ---------------------------------------------------------------------------
# rollback_cascade --tier1=CMD --tier2=CMD --tier3=CMD --tier4=CMD
#   Run each tier in order. Stop as soon as one succeeds.
#   Each tier is eval'd as a shell command.
#   Returns 0 if any tier succeeds; returns exit of last tier otherwise.
# ---------------------------------------------------------------------------
rollback_cascade() {
    local t1="" t2="" t3="" t4=""
    for arg in "$@"; do
        case "$arg" in
            --tier1=*) t1="${arg#--tier1=}" ;;
            --tier2=*) t2="${arg#--tier2=}" ;;
            --tier3=*) t3="${arg#--tier3=}" ;;
            --tier4=*) t4="${arg#--tier4=}" ;;
        esac
    done
    local last_status=1 tier_exit
    for tier_cmd in "$t1" "$t2" "$t3" "$t4"; do
        [ -z "$tier_cmd" ] && continue
        eval "$tier_cmd"
        tier_exit=$?
        if [ "$tier_exit" -eq 0 ]; then
            return 0
        fi
        last_status=$tier_exit
    done
    return "$last_status"
}

# ---------------------------------------------------------------------------
# populate_app_dir --source=X --app-dir=Y --version=V [--mode=git|rsync|auto]
#   Populate APP_DIR from source.
#   mode=auto (default): git clone if source is a git tree/URL, else rsync.
#   mode=git: force git clone.
#   mode=rsync: force rsync (non-git source).
# ---------------------------------------------------------------------------
populate_app_dir() {
    local src="" dest="" version="" mode="auto"
    for arg in "$@"; do
        case "$arg" in
            --source=*)   src="${arg#--source=}" ;;
            --app-dir=*)  dest="${arg#--app-dir=}" ;;
            --version=*)  version="${arg#--version=}" ;;
            --mode=*)     mode="${arg#--mode=}" ;;
        esac
    done
    [ -z "$src" ] && { echo "populate_app_dir: missing --source" >&2; return 1; }
    [ -z "$dest" ] && { echo "populate_app_dir: missing --app-dir" >&2; return 1; }

    # Auto mode: choose git or rsync
    if [ "$mode" = "auto" ]; then
        if [ -d "$src/.git" ] || [[ "$src" == *://* ]]; then
            mode="git"
        else
            mode="rsync"
        fi
    fi

    if [ "$mode" = "git" ]; then
        if [ -d "$dest/.git" ]; then
            # Already a git tree — just fetch and checkout
            git -C "$dest" fetch --tags -q origin || true
            git -C "$dest" checkout -q "${version:-HEAD}"
        else
            # Fresh clone — preserve .env/tokens.json if they already exist
            local preserve="$(mktemp -d)"
            [ -f "$dest/.env" ]        && mv "$dest/.env" "$preserve/"
            [ -f "$dest/tokens.json" ] && mv "$dest/tokens.json" "$preserve/"
            rm -rf "$dest"
            mkdir -p "$dest"
            if [[ "$src" == *://* ]]; then
                git clone -q --branch "${version:-HEAD}" "$src" "$dest"
            else
                git clone -q --local "$src" "$dest"
                [ -n "$version" ] && git -C "$dest" checkout -q "$version"
            fi
            [ -f "$preserve/.env" ]        && mv "$preserve/.env" "$dest/"
            [ -f "$preserve/tokens.json" ] && mv "$preserve/tokens.json" "$dest/"
            rm -rf "$preserve"
        fi
    else
        # rsync fallback
        mkdir -p "$dest"
        rsync -a --exclude='.git' --exclude='__pycache__' --exclude='tests' \
              --exclude='.pytest_cache' --exclude='docs' "$src/" "$dest/"
    fi

    # Write .install-info
    printf '{"version":"%s","installed_at":"%s","mode":"%s"}\n' \
        "${version:-unknown}" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$mode" > "$dest/.install-info"
}
