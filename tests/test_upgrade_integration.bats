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

@test "upgrade.sh --foreground end-to-end on mock (no systemctl, no pip)" {
    # Set up a "source" repo
    local SRC="$TMPROOT/src"
    mkdir -p "$SRC"
    cd "$SRC"
    git init -q
    git config user.email ci@local
    git config user.name ci
    echo "v1" > main.py
    cat > requirements.txt <<EOF
EOF
    git add main.py requirements.txt
    git commit -q -m "c1"
    git tag v1.0.0
    echo "v2" > main.py
    git commit -qam "c2"
    git tag v1.1.0

    # Clone source into APP_DIR at v1.0.0
    git clone -q "$SRC" "$APP_DIR"
    git -C "$APP_DIR" checkout -q v1.0.0

    # Provide stubs for systemctl and curl to make foreground path complete
    local stubs="$TMPROOT/stubs"
    mkdir -p "$stubs"
    cat > "$stubs/systemctl" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
    chmod +x "$stubs/systemctl"
    cat > "$stubs/curl" <<'EOF'
#!/usr/bin/env bash
# Fake /health 200
exit 0
EOF
    chmod +x "$stubs/curl"
    # Fake venv's pip
    mkdir -p "$APP_DIR/venv/bin"
    cat > "$APP_DIR/venv/bin/pip" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
    chmod +x "$APP_DIR/venv/bin/pip"

    PATH="$stubs:$PATH" run bash "$UPGRADE_SH" \
        --app-dir="$APP_DIR" --source="$SRC" --foreground --no-health-check v1.1.0
    [ "$status" -eq 0 ]
    run git -C "$APP_DIR" describe --tags
    [ "$output" = "v1.1.0" ]
    # Backup exists
    run ls -d "${APP_DIR}.bak-"*
    [ "$status" -eq 0 ]
    # State file says done
    run cat "$APP_DIR/.upgrade-state"
    [[ "$output" == *'"step":"done"'* ]]
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
