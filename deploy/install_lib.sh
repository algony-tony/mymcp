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
