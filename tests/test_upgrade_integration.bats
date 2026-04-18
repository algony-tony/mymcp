#!/usr/bin/env bats
# Integration-ish tests for deploy/upgrade.sh.
# Run: bats tests/test_upgrade_integration.bats

setup() {
    export AUTO_YES=true
    TMPROOT="$(mktemp -d)"
    export APP_DIR="$TMPROOT/mymcp"
    mkdir -p "$APP_DIR"
    export UPGRADE_SH="$BATS_TEST_DIRNAME/../deploy/upgrade.sh"
}

teardown() {
    rm -rf "$TMPROOT"
}

@test "upgrade.sh --help prints usage" {
    run bash "$UPGRADE_SH" --help
    [ "$status" -eq 0 ]
    [[ "$output" == *"Usage: upgrade.sh"* ]]
}

@test "upgrade.sh --current on legacy install prints unknown" {
    run bash "$UPGRADE_SH" --app-dir="$APP_DIR" --current
    [ "$status" -eq 0 ]
    [ "$output" = "unknown" ]
}

@test "upgrade.sh --current on git-managed install prints git describe" {
    cd "$APP_DIR"
    git init -q
    git config user.email ci@local
    git config user.name ci
    git commit --allow-empty -q -m "c1"
    git tag v7.7.7
    run bash "$UPGRADE_SH" --app-dir="$APP_DIR" --current
    [ "$status" -eq 0 ]
    [ "$output" = "v7.7.7" ]
}

@test "upgrade.sh --status prints 'no upgrade in progress' when no state file" {
    run bash "$UPGRADE_SH" --app-dir="$APP_DIR" --status
    [ "$status" -eq 0 ]
    [[ "$output" == *"no upgrade in progress"* ]]
}
