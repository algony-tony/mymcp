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

@test "upgrade.sh --dry-run on git install prints plan and exits 0" {
    cd "$APP_DIR"
    git init -q
    git config user.email ci@local
    git config user.name ci
    git commit --allow-empty -q -m "c1"
    git tag v1.0.0
    git commit --allow-empty -q -m "c2"
    git tag v1.1.0
    # Fake service discovery: simulate no systemd lookup needed
    run bash "$UPGRADE_SH" --app-dir="$APP_DIR" --source="$APP_DIR" --dry-run v1.1.0
    [ "$status" -eq 0 ]
    [[ "$output" == *"Current: v1.0.0"* ]]
    [[ "$output" == *"Target: v1.1.0"* ]]
    [[ "$output" == *"DRY RUN"* ]]
}

@test "upgrade.sh aborts when target == current without --force" {
    cd "$APP_DIR"
    git init -q
    git config user.email ci@local
    git config user.name ci
    git commit --allow-empty -q -m "c1"
    git tag v1.0.0
    run bash "$UPGRADE_SH" --app-dir="$APP_DIR" --source="$APP_DIR" --dry-run v1.0.0
    [ "$status" -ne 0 ]
    [[ "$output" == *"same version"* || "$output" == *"already"* ]]
}

@test "upgrade.sh rejects branch without --allow-branch" {
    cd "$APP_DIR"
    git init -q
    git config user.email ci@local
    git config user.name ci
    git commit --allow-empty -q -m "c1"
    git checkout -q -b dev
    run bash "$UPGRADE_SH" --app-dir="$APP_DIR" --source="$APP_DIR" --dry-run dev
    [ "$status" -ne 0 ]
    [[ "$output" == *"--allow-branch"* ]]
}

@test "upgrade.sh converts non-git APP_DIR and reaches backup step" {
    # Set up a "source" repo with two tags
    local SRC="$TMPROOT/src"
    mkdir -p "$SRC"
    cd "$SRC"
    git init -q
    git config user.email ci@local
    git config user.name ci
    echo "v1" > main.py
    git add main.py
    git commit -q -m "c1"
    git tag v1.0.0
    echo "v2" > main.py
    git commit -qam "c2"
    git tag v1.1.0

    # Populate APP_DIR as if via rsync (no .git)
    mkdir -p "$APP_DIR"
    echo "v1" > "$APP_DIR/main.py"
    echo "# state" > "$APP_DIR/.env"
    # We want to ensure we don't reach the "not implemented" phase due to the legacy-missing-git guard.
    # The run will fail at actual systemctl — that's OK for this task; we just want conversion to happen.
    run bash "$UPGRADE_SH" --app-dir="$APP_DIR" --source="$SRC" --foreground v1.1.0 || true
    # After conversion, .git should exist
    [ -d "$APP_DIR/.git" ]
    # .env preserved
    [ -f "$APP_DIR/.env" ]
}
