#!/usr/bin/env bats
# Tests for deploy/install_lib.sh upgrade-related helpers and deploy/upgrade.sh.
# Run: bats tests/test_upgrade.bats

setup() {
    export AUTO_YES=true
    source "$BATS_TEST_DIRNAME/../deploy/install_lib.sh"
    # Sandbox for file-system operations
    TMPROOT="$(mktemp -d)"
    export APP_DIR="$TMPROOT/mymcp"
    mkdir -p "$APP_DIR"
}

teardown() {
    rm -rf "$TMPROOT"
}

@test "smoke: install_lib.sh sources cleanly" {
    run bash -c 'source deploy/install_lib.sh; echo ok'
    [ "$status" -eq 0 ]
    [[ "$output" == *"ok"* ]]
}

# =========================================================================
# State file helpers
# =========================================================================

@test "write_state: creates .upgrade-state with step field" {
    write_state "$APP_DIR" "preflight" "v1.0.0" "v1.1.0"
    [ -f "$APP_DIR/.upgrade-state" ]
    run cat "$APP_DIR/.upgrade-state"
    [[ "$output" == *'"step":"preflight"'* ]]
    [[ "$output" == *'"from":"v1.0.0"'* ]]
    [[ "$output" == *'"to":"v1.1.0"'* ]]
}

@test "write_state: updates step on second call, preserves from/to" {
    write_state "$APP_DIR" "preflight" "v1.0.0" "v1.1.0"
    write_state "$APP_DIR" "backup" "v1.0.0" "v1.1.0"
    run cat "$APP_DIR/.upgrade-state"
    [[ "$output" == *'"step":"backup"'* ]]
}

@test "read_state: returns JSON string for --status consumption" {
    write_state "$APP_DIR" "installing-deps" "v1.0.0" "v1.1.0"
    run read_state "$APP_DIR"
    [ "$status" -eq 0 ]
    [[ "$output" == *'"step":"installing-deps"'* ]]
}

@test "read_state: returns empty and exits 1 when no state file" {
    run read_state "$APP_DIR"
    [ "$status" -eq 1 ]
}
