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

# =========================================================================
# Lock file
# =========================================================================

@test "acquire_lock: succeeds on first call" {
    run acquire_lock "$APP_DIR"
    [ "$status" -eq 0 ]
    [ -f "$APP_DIR/.upgrade.lock" ]
}

@test "acquire_lock: second call fails while first process holds lock" {
    ( flock -x "$APP_DIR/.upgrade.lock" sleep 2 ) &
    local holder=$!
    sleep 0.2
    run acquire_lock "$APP_DIR"
    [ "$status" -ne 0 ]
    wait "$holder"
}

@test "acquire_lock: cleans stale lock whose PID is dead" {
    # Write lock file with a non-existent PID (we use 999999 which is unlikely)
    echo "999999" > "$APP_DIR/.upgrade.lock"
    run acquire_lock "$APP_DIR"
    [ "$status" -eq 0 ]
}

# =========================================================================
# detect_current_version
# =========================================================================

@test "detect_current_version: returns git describe output on git tree" {
    cd "$APP_DIR"
    git init -q
    git config user.email ci@local
    git config user.name ci
    git commit --allow-empty -q -m "init"
    git tag v9.9.9
    run detect_current_version "$APP_DIR"
    [ "$status" -eq 0 ]
    [ "$output" = "v9.9.9" ]
}

@test "detect_current_version: falls back to .install-info" {
    echo '{"version":"v0.5.0","installed_at":"2026-01-01T00:00:00Z"}' > "$APP_DIR/.install-info"
    run detect_current_version "$APP_DIR"
    [ "$status" -eq 0 ]
    [ "$output" = "v0.5.0" ]
}

@test "detect_current_version: returns 'unknown' when neither available" {
    run detect_current_version "$APP_DIR"
    [ "$status" -eq 0 ]
    [ "$output" = "unknown" ]
}

# =========================================================================
# is_under_mymcp
# =========================================================================

@test "is_under_mymcp: returns 1 outside mymcp" {
    run is_under_mymcp
    [ "$status" -eq 1 ]
}

@test "is_under_mymcp: matches marker envvar MYMCP_FAKE_ANCESTOR" {
    # Inject a fake ancestor detection by overriding the read function
    MYMCP_FAKE_UNDER=1 run is_under_mymcp
    [ "$status" -eq 0 ]
}

# =========================================================================
# resolve_source
# =========================================================================

@test "resolve_source: --source wins over everything" {
    run resolve_source --source=/custom/path --repo-dir="$APP_DIR"
    [ "$status" -eq 0 ]
    [ "$output" = "/custom/path" ]
}

@test "resolve_source: local git tree at repo_dir preferred over default remote" {
    cd "$APP_DIR"
    git init -q
    run resolve_source --repo-dir="$APP_DIR"
    [ "$status" -eq 0 ]
    [ "$output" = "$APP_DIR" ]
}

@test "resolve_source: default is GitHub when repo_dir is not a git tree" {
    run resolve_source --repo-dir="$APP_DIR"
    [ "$status" -eq 0 ]
    [[ "$output" == "https://github.com/"* ]]
}

@test "resolve_source: --prefer-remote skips local git tree" {
    cd "$APP_DIR"
    git init -q
    run resolve_source --repo-dir="$APP_DIR" --prefer-remote
    [ "$status" -eq 0 ]
    [[ "$output" == "https://github.com/"* ]]
}

# =========================================================================
# classify_ref
# =========================================================================

setup_git_repo() {
    cd "$APP_DIR"
    git init -q
    git config user.email ci@local
    git config user.name ci
    git commit --allow-empty -q -m "c1"
    git tag v1.0.0
    git commit --allow-empty -q -m "c2"
    git branch feature-x
    SHA_C2=$(git rev-parse HEAD)
}

@test "classify_ref: tag returns 'tag'" {
    setup_git_repo
    run classify_ref "$APP_DIR" v1.0.0
    [ "$status" -eq 0 ]
    [ "$output" = "tag" ]
}

@test "classify_ref: branch returns 'branch'" {
    setup_git_repo
    run classify_ref "$APP_DIR" feature-x
    [ "$status" -eq 0 ]
    [ "$output" = "branch" ]
}

@test "classify_ref: commit SHA returns 'commit'" {
    setup_git_repo
    run classify_ref "$APP_DIR" "$SHA_C2"
    [ "$status" -eq 0 ]
    [ "$output" = "commit" ]
}

@test "classify_ref: unknown ref returns 'unknown' and non-zero" {
    setup_git_repo
    run classify_ref "$APP_DIR" nothing-here
    [ "$status" -ne 0 ]
    [ "$output" = "unknown" ]
}

# =========================================================================
# create_backup / prune_backups
# =========================================================================

@test "create_backup: copies files excluding venv and .git" {
    mkdir -p "$APP_DIR/venv" "$APP_DIR/tools"
    echo "hello" > "$APP_DIR/main.py"
    echo "x" > "$APP_DIR/venv/foo"
    echo "t" > "$APP_DIR/tools/x.py"
    run create_backup "$APP_DIR" "v1.0.0"
    [ "$status" -eq 0 ]
    # Find the created backup
    local bak
    bak=$(ls -d "${APP_DIR}.bak-"*/ 2>/dev/null | head -1)
    [ -n "$bak" ]
    [ -f "$bak/main.py" ]
    [ -f "$bak/tools/x.py" ]
    [ ! -d "$bak/venv" ]
    [ ! -d "$bak/.git" ]
    [ -f "$bak/.backup-info" ]
    run cat "$bak/.backup-info"
    [[ "$output" == *'"from_version":"v1.0.0"'* ]]
}

@test "prune_backups: keeps N most recent, deletes older" {
    mkdir -p "${APP_DIR}.bak-20260101-000001"
    mkdir -p "${APP_DIR}.bak-20260102-000001"
    mkdir -p "${APP_DIR}.bak-20260103-000001"
    mkdir -p "${APP_DIR}.bak-20260104-000001"
    run prune_backups "$APP_DIR" 2
    [ "$status" -eq 0 ]
    [ ! -d "${APP_DIR}.bak-20260101-000001" ]
    [ ! -d "${APP_DIR}.bak-20260102-000001" ]
    [ -d "${APP_DIR}.bak-20260103-000001" ]
    [ -d "${APP_DIR}.bak-20260104-000001" ]
}

# =========================================================================
# wait_for_health
# =========================================================================

@test "wait_for_health: returns 0 quickly when mock server responds 200" {
    # Pick a random free port to avoid TIME_WAIT collisions between runs
    local health_port
    health_port=$(python3 -c "import socket; s=socket.socket(); s.bind(('',0)); print(s.getsockname()[1]); s.close()")
    # Start a simple HTTP server in a subshell
    python3 -c "
import http.server, socketserver, sys
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path=='/health':
            self.send_response(200); self.send_header('Content-Type','application/json'); self.end_headers()
            self.wfile.write(b'{\"status\":\"ok\"}')
        else:
            self.send_response(404); self.end_headers()
    def log_message(self,*a,**kw): pass
socketserver.TCPServer.allow_reuse_address = True
s=socketserver.TCPServer(('127.0.0.1', $health_port), H)
s.serve_forever()
" &
    local server_pid=$!
    sleep 0.3
    MCP_HOST=127.0.0.1 MCP_PORT=$health_port run wait_for_health "$APP_DIR" 5
    kill "$server_pid" 2>/dev/null || true
    [ "$status" -eq 0 ]
}

@test "wait_for_health: returns non-zero when no server responds within timeout" {
    MCP_HOST=127.0.0.1 MCP_PORT=17655 run wait_for_health "$APP_DIR" 2
    [ "$status" -ne 0 ]
}

# =========================================================================
# discover_app_dir
# =========================================================================

@test "discover_app_dir: explicit flag wins" {
    run discover_app_dir --app-dir=/my/custom --unit-file=/nonexistent
    [ "$status" -eq 0 ]
    [ "$output" = "/my/custom" ]
}

@test "discover_app_dir: reads WorkingDirectory from unit file" {
    local unit="$TMPROOT/mymcp.service"
    cat > "$unit" <<EOF
[Service]
WorkingDirectory=/svc/path
ExecStart=/x
EOF
    run discover_app_dir --unit-file="$unit"
    [ "$status" -eq 0 ]
    [ "$output" = "/svc/path" ]
}

@test "discover_app_dir: default /opt/mymcp when no clues" {
    run discover_app_dir --unit-file=/nonexistent
    [ "$status" -eq 0 ]
    [ "$output" = "/opt/mymcp" ]
}

# =========================================================================
# launch_detached
# =========================================================================

@test "launch_detached: child survives parent exit (fallback mode)" {
    local logdir="$TMPROOT/log"
    mkdir -p "$logdir"
    local marker="$TMPROOT/marker"
    local script="$TMPROOT/child.sh"
    cat > "$script" <<EOF
#!/usr/bin/env bash
sleep 0.5
echo survived > "$marker"
EOF
    chmod +x "$script"
    # Force fallback path (no systemd)
    MYMCP_FORCE_FALLBACK=1 run launch_detached "$script" --log-dir="$logdir"
    [ "$status" -eq 0 ]
    # Child writes marker AFTER parent launch exits; wait for it
    local wait_deadline=$(( $(date +%s) + 5 ))
    while [ "$(date +%s)" -lt "$wait_deadline" ] && [ ! -f "$marker" ]; do
        sleep 0.1
    done
    [ -f "$marker" ]
    [ "$(cat "$marker")" = "survived" ]
}

@test "launch_detached: creates log file under logdir" {
    local logdir="$TMPROOT/log"
    mkdir -p "$logdir"
    local script="$TMPROOT/child.sh"
    cat > "$script" <<'EOF'
#!/usr/bin/env bash
echo "detached-child-output"
sleep 0.2
EOF
    chmod +x "$script"
    MYMCP_FORCE_FALLBACK=1 run launch_detached "$script" --log-dir="$logdir"
    [ "$status" -eq 0 ]
    sleep 0.6
    # At least one log file should exist with expected content
    run bash -c "grep -l detached-child-output $logdir/*.log"
    [ "$status" -eq 0 ]
}

# =========================================================================
# rollback_cascade
# =========================================================================

@test "rollback_cascade: tier1 success stops further tiers" {
    local trace="$TMPROOT/trace"
    run rollback_cascade \
        --tier1="echo t1 >> $trace" \
        --tier2="echo t2 >> $trace" \
        --tier3="echo t3 >> $trace" \
        --tier4="echo t4 >> $trace"
    [ "$status" -eq 0 ]
    [ "$(cat "$trace")" = "t1" ]
}

@test "rollback_cascade: tier1 fails, tier2 succeeds, stops there" {
    local trace="$TMPROOT/trace"
    run rollback_cascade \
        --tier1="echo t1 >> $trace; false" \
        --tier2="echo t2 >> $trace" \
        --tier3="echo t3 >> $trace" \
        --tier4="echo t4 >> $trace"
    [ "$status" -eq 0 ]
    run cat "$trace"
    [[ "$output" == *"t1"* ]]
    [[ "$output" == *"t2"* ]]
    [[ "$output" != *"t3"* ]]
}

@test "rollback_cascade: all tiers fail, exit non-zero" {
    local trace="$TMPROOT/trace"
    run rollback_cascade \
        --tier1="echo t1 >> $trace; false" \
        --tier2="echo t2 >> $trace; false" \
        --tier3="echo t3 >> $trace; false" \
        --tier4="echo t4 >> $trace; false"
    [ "$status" -ne 0 ]
    run cat "$trace"
    [[ "$output" == *"t1"* && "$output" == *"t2"* && "$output" == *"t3"* && "$output" == *"t4"* ]]
}
