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
