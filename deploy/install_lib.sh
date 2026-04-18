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
