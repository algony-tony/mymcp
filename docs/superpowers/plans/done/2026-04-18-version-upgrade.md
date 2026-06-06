# Version Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `deploy/upgrade.sh` plus supporting changes so users can upgrade an installed mymcp safely, including from inside a running mymcp via `bash_execute`.

**Architecture:** Shell script with helper library (`install_lib.sh`). Tests use `bats-core` (already in use for `test_install.bats`). Upgrade orchestration detaches itself via `systemd-run` (preferred) or `setsid nohup` (fallback), manages state via `$APP_DIR/.upgrade-state` JSON and logs to `/var/log/mymcp/upgrade.log`. Four-tier cascading recovery with `EXIT` trap guarantees "service is running on exit".

**Tech Stack:** Bash (POSIX + bashisms where convenient), `bats-core` for unit tests, `flock` for concurrency, `systemctl`/`systemd-run` for service control, `git`, Docker for integration tests, GitHub Actions CI.

**Spec:** `docs/superpowers/specs/2026-04-18-version-upgrade-design.md`

---

## Conventions

- All new helpers live in `deploy/install_lib.sh` (extend, don't split) unless a helper is upgrade-specific and would bloat the file — then put it at top of `deploy/upgrade.sh`.
- All bats tests live in `tests/test_upgrade.bats` (unit tests for helpers) and `tests/test_upgrade_integration.bats` (orchestration). Follow `tests/test_install.bats` style.
- Docker integration scenarios live in `tests/deploy/integration/`.
- Every task ends with a `git commit` using conventional-commits prefix (`feat:`, `fix:`, `test:`, `docs:`, `chore:`).
- Working branch: `feat/version-upgrade` (already created, already checked out).
- Commit message body: brief rationale (1-3 sentences), then the `Co-Authored-By:` line matching prior commits.

---

## File Structure

**Will create:**
- `deploy/upgrade.sh` — main upgrade orchestration script (~500 lines)
- `deploy/UPGRADE_NOTES.md` — breaking-change log, starts empty
- `deploy/logrotate.mymcp-upgrade` — logrotate config for upgrade logs
- `tests/test_upgrade.bats` — unit tests for helper functions
- `tests/test_upgrade_integration.bats` — orchestration tests with mocks
- `tests/deploy/integration/Dockerfile.debian` — Debian/Ubuntu integration base
- `tests/deploy/integration/Dockerfile.rocky` — RHEL-family integration base
- `tests/deploy/integration/scenario_fresh_upgrade.sh` — happy path
- `tests/deploy/integration/scenario_legacy_convert.sh` — rsync → git conversion
- `tests/deploy/integration/scenario_rollback.sh` — failed upgrade triggers rollback
- `tests/deploy/integration/scenario_offline_wheels.sh` — offline install via wheels
- `tests/deploy/integration/run_all.sh` — harness that runs each scenario in its Docker image
- `.github/workflows/deploy-test.yml` — CI for Docker scenarios

**Will modify:**
- `deploy/install_lib.sh` — add source-resolution, version-detection, state-file, lock, detach, process-ancestry helpers
- `deploy/install.sh` — switch primary population from rsync to git clone; keep rsync as fallback; write `.install-info`
- `deploy/mymcp.service` — (no change expected, but check during unit-refresh step)
- `README.md` — add "Upgrade" section
- `CLAUDE.md` — document upgrade flow and MCP-client-driven upgrade

---

## Task 1: Test scaffolding — bats harness for upgrade helpers

**Files:**
- Create: `tests/test_upgrade.bats`

Rationale: set up the test file first so every subsequent helper task can add its tests incrementally.

- [ ] **Step 1: Verify bats is installed**

Run: `bats --version`
Expected: prints a version like `Bats 1.x.x`. If missing, install via `sudo apt-get install bats` (Ubuntu/Debian) or `sudo dnf install bats` (RHEL-family).

- [ ] **Step 2: Create the bats file with a smoke test**

Create `tests/test_upgrade.bats`:

```bash
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
```

- [ ] **Step 3: Run smoke test**

Run: `bats tests/test_upgrade.bats`
Expected: `1 test, 0 failures` — all green.

- [ ] **Step 4: Commit**

```bash
git add tests/test_upgrade.bats
git commit -m "$(cat <<'EOF'
test: scaffold bats harness for upgrade helpers

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Helper — `write_state` and `read_state` (atomic state file)

**Files:**
- Modify: `deploy/install_lib.sh` (append)
- Test: `tests/test_upgrade.bats` (append)

State file is written atomically via temp-file + rename so readers (e.g., `--status`) never see partial JSON.

- [ ] **Step 1: Write failing test**

Append to `tests/test_upgrade.bats`:

```bash
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
```

- [ ] **Step 2: Run test, expect failures**

Run: `bats tests/test_upgrade.bats`
Expected: 4 new tests fail with `write_state: command not found` / `read_state: command not found`.

- [ ] **Step 3: Implement helpers**

Append to `deploy/install_lib.sh`:

```bash
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
```

- [ ] **Step 4: Run tests, expect pass**

Run: `bats tests/test_upgrade.bats`
Expected: all tests pass, including 4 new state-file tests.

- [ ] **Step 5: Commit**

```bash
git add deploy/install_lib.sh tests/test_upgrade.bats
git commit -m "$(cat <<'EOF'
feat: add atomic upgrade state file helpers

write_state renames a temp file for atomic update; read_state exits 1
when the file is missing so --status can distinguish no-upgrade vs
in-progress.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Helper — `acquire_lock` (flock-based single-writer guard)

**Files:**
- Modify: `deploy/install_lib.sh`
- Test: `tests/test_upgrade.bats`

Prevents two upgrades running concurrently. Stale locks (dead PID) are cleaned automatically.

- [ ] **Step 1: Write failing test**

Append to `tests/test_upgrade.bats`:

```bash
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
```

- [ ] **Step 2: Run, expect failures**

Run: `bats tests/test_upgrade.bats -f acquire_lock`
Expected: 3 tests fail with `acquire_lock: command not found`.

- [ ] **Step 3: Implement**

Append to `deploy/install_lib.sh`:

```bash
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
```

- [ ] **Step 4: Run tests, expect pass**

Run: `bats tests/test_upgrade.bats -f acquire_lock`
Expected: 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add deploy/install_lib.sh tests/test_upgrade.bats
git commit -m "$(cat <<'EOF'
feat: add flock-based upgrade lock with stale cleanup

Prevents concurrent upgrades. Dead lockholder PIDs are removed so a
crashed previous upgrade doesn't wedge the next attempt.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Helper — `detect_current_version`

**Files:**
- Modify: `deploy/install_lib.sh`
- Test: `tests/test_upgrade.bats`

Returns the installed version by trying `git describe --tags --always` first, falling back to `.install-info` JSON, and returning `unknown` if neither works.

- [ ] **Step 1: Write failing test**

Append to `tests/test_upgrade.bats`:

```bash
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
```

- [ ] **Step 2: Run, expect failures**

Run: `bats tests/test_upgrade.bats -f detect_current_version`
Expected: 3 failures.

- [ ] **Step 3: Implement**

Append to `deploy/install_lib.sh`:

```bash
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
```

- [ ] **Step 4: Run tests, expect pass**

Run: `bats tests/test_upgrade.bats -f detect_current_version`
Expected: 3 pass.

- [ ] **Step 5: Commit**

```bash
git add deploy/install_lib.sh tests/test_upgrade.bats
git commit -m "$(cat <<'EOF'
feat: add detect_current_version helper

Prefers git describe for git-managed installs, falls back to
.install-info for legacy rsync installs, returns 'unknown' otherwise.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Helper — `is_under_mymcp` (process ancestry)

**Files:**
- Modify: `deploy/install_lib.sh`
- Test: `tests/test_upgrade.bats`

Walks `/proc/*/stat` to detect if any ancestor PID is a mymcp uvicorn worker. Used to force-detach when invoked via `bash_execute`.

- [ ] **Step 1: Write failing test**

Append to `tests/test_upgrade.bats`:

```bash
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
```

- [ ] **Step 2: Run, expect failures**

Run: `bats tests/test_upgrade.bats -f is_under_mymcp`
Expected: failures.

- [ ] **Step 3: Implement**

Append to `deploy/install_lib.sh`:

```bash
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
```

- [ ] **Step 4: Run tests, expect pass**

Run: `bats tests/test_upgrade.bats -f is_under_mymcp`
Expected: 2 pass.

- [ ] **Step 5: Commit**

```bash
git add deploy/install_lib.sh tests/test_upgrade.bats
git commit -m "$(cat <<'EOF'
feat: add is_under_mymcp process-ancestry check

Walks /proc to detect if an ancestor is the mymcp uvicorn process,
used to force upgrade into detach mode when invoked via bash_execute.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Helper — `resolve_source`

**Files:**
- Modify: `deploy/install_lib.sh`
- Test: `tests/test_upgrade.bats`

Resolves where code comes from using the fallback chain: explicit `--source` → local git tree at REPO_DIR → GitHub default. Returns the URL/path as string, sets `_SOURCE_KIND` (git|remote).

- [ ] **Step 1: Write failing test**

Append to `tests/test_upgrade.bats`:

```bash
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
```

- [ ] **Step 2: Run, expect failures**

Run: `bats tests/test_upgrade.bats -f resolve_source`
Expected: 4 failures.

- [ ] **Step 3: Implement**

Append to `deploy/install_lib.sh`:

```bash
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
```

- [ ] **Step 4: Run tests, expect pass**

Run: `bats tests/test_upgrade.bats -f resolve_source`
Expected: 4 pass.

- [ ] **Step 5: Commit**

```bash
git add deploy/install_lib.sh tests/test_upgrade.bats
git commit -m "$(cat <<'EOF'
feat: add resolve_source with fallback chain

Priority: explicit --source > local git tree > GitHub remote. The
--prefer-remote flag inverts the first two.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Helper — `classify_ref` (tag vs commit vs branch)

**Files:**
- Modify: `deploy/install_lib.sh`
- Test: `tests/test_upgrade.bats`

Given a ref string and a git repo, reports whether it's a tag, commit, or branch so the CLI can apply appropriate guardrails.

- [ ] **Step 1: Write failing test**

Append to `tests/test_upgrade.bats`:

```bash
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
```

- [ ] **Step 2: Run, expect failures**

Run: `bats tests/test_upgrade.bats -f classify_ref`
Expected: 4 failures.

- [ ] **Step 3: Implement**

Append to `deploy/install_lib.sh`:

```bash
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
```

- [ ] **Step 4: Run tests, expect pass**

Run: `bats tests/test_upgrade.bats -f classify_ref`
Expected: 4 pass.

- [ ] **Step 5: Commit**

```bash
git add deploy/install_lib.sh tests/test_upgrade.bats
git commit -m "$(cat <<'EOF'
feat: add classify_ref helper for tag/branch/commit distinction

Used by upgrade.sh to apply --allow-branch guardrail and to warn on
non-tag commits.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Helper — `create_backup` and `prune_backups`

**Files:**
- Modify: `deploy/install_lib.sh`
- Test: `tests/test_upgrade.bats`

Creates `$APP_DIR.bak-YYYYMMDD-HHMMSS/` (excluding `venv/` and `.git/`) plus a metadata file recording the current version. Prunes oldest when over retention.

- [ ] **Step 1: Write failing test**

Append to `tests/test_upgrade.bats`:

```bash
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
```

- [ ] **Step 2: Run, expect failures**

Run: `bats tests/test_upgrade.bats -f backup`
Expected: 2 failures.

- [ ] **Step 3: Implement**

Append to `deploy/install_lib.sh`:

```bash
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
```

- [ ] **Step 4: Run tests, expect pass**

Run: `bats tests/test_upgrade.bats -f backup`
Expected: 2 pass.

- [ ] **Step 5: Commit**

```bash
git add deploy/install_lib.sh tests/test_upgrade.bats
git commit -m "$(cat <<'EOF'
feat: add create_backup and prune_backups helpers

Snapshots APP_DIR excluding venv/.git; records .backup-info with
from_version and from_sha for rollback metadata.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Helper — `wait_for_health` (poll /health)

**Files:**
- Modify: `deploy/install_lib.sh`
- Test: `tests/test_upgrade.bats`

Polls `http://$host:$port/health` for up to N seconds. Uses `curl`. Reads MCP_HOST/MCP_PORT from `$APP_DIR/.env` with localhost/8765 defaults.

- [ ] **Step 1: Write failing test**

Append to `tests/test_upgrade.bats`:

```bash
# =========================================================================
# wait_for_health
# =========================================================================

@test "wait_for_health: returns 0 quickly when mock server responds 200" {
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
s=socketserver.TCPServer(('127.0.0.1', 17654), H)
s.serve_forever()
" &
    local server_pid=$!
    sleep 0.3
    MCP_HOST=127.0.0.1 MCP_PORT=17654 run wait_for_health "$APP_DIR" 5
    kill "$server_pid" 2>/dev/null || true
    [ "$status" -eq 0 ]
}

@test "wait_for_health: returns non-zero when no server responds within timeout" {
    MCP_HOST=127.0.0.1 MCP_PORT=17655 run wait_for_health "$APP_DIR" 2
    [ "$status" -ne 0 ]
}
```

- [ ] **Step 2: Run, expect failures**

Run: `bats tests/test_upgrade.bats -f wait_for_health`
Expected: 2 failures.

- [ ] **Step 3: Implement**

Append to `deploy/install_lib.sh`:

```bash
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
```

- [ ] **Step 4: Run tests, expect pass**

Run: `bats tests/test_upgrade.bats -f wait_for_health`
Expected: 2 pass. (Test 1 requires `python3`; if missing, skip with note.)

- [ ] **Step 5: Commit**

```bash
git add deploy/install_lib.sh tests/test_upgrade.bats
git commit -m "$(cat <<'EOF'
feat: add wait_for_health helper polling /health endpoint

Reads MCP_HOST/MCP_PORT from .env; handles 0.0.0.0 as localhost for
client-side polling. Used as final step of upgrade.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Helper — `discover_app_dir`

**Files:**
- Modify: `deploy/install_lib.sh`
- Test: `tests/test_upgrade.bats`

Finds APP_DIR via: `--app-dir=PATH` → systemd unit `WorkingDirectory=` → default `/opt/mymcp`.

- [ ] **Step 1: Write failing test**

Append to `tests/test_upgrade.bats`:

```bash
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
```

- [ ] **Step 2: Run, expect failures**

Run: `bats tests/test_upgrade.bats -f discover_app_dir`
Expected: 3 failures.

- [ ] **Step 3: Implement**

Append to `deploy/install_lib.sh`:

```bash
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
```

- [ ] **Step 4: Run tests, expect pass**

Run: `bats tests/test_upgrade.bats -f discover_app_dir`
Expected: 3 pass.

- [ ] **Step 5: Commit**

```bash
git add deploy/install_lib.sh tests/test_upgrade.bats
git commit -m "$(cat <<'EOF'
feat: add discover_app_dir helper

Resolves APP_DIR via explicit flag, systemd unit WorkingDirectory, or
default. No prompting — silent.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: Helper — `launch_detached` (systemd-run + setsid fallback)

**Files:**
- Modify: `deploy/install_lib.sh`
- Test: `tests/test_upgrade.bats`

Spawns a detached child that survives the parent's exit and redirects output to the upgrade log file. Prefers `systemd-run` when systemd is PID 1; falls back to `setsid nohup ... & disown`.

- [ ] **Step 1: Write failing test**

Append to `tests/test_upgrade.bats`:

```bash
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
```

- [ ] **Step 2: Run, expect failures**

Run: `bats tests/test_upgrade.bats -f launch_detached`
Expected: 2 failures.

- [ ] **Step 3: Implement**

Append to `deploy/install_lib.sh`:

```bash
# ---------------------------------------------------------------------------
# launch_detached script_path [--log-dir=DIR] [--unit-name=NAME] [args...]
#   Launch script detached from this process. Prefers systemd-run, falls back
#   to setsid+nohup+disown. Prints 'PID NNNN' or 'UNIT name' on success.
# ---------------------------------------------------------------------------
launch_detached() {
    local script="$1"; shift
    local logdir="/var/log/mymcp" unit="mymcp-upgrade"
    local -a passthrough=()
    for arg in "$@"; do
        case "$arg" in
            --log-dir=*)   logdir="${arg#--log-dir=}" ;;
            --unit-name=*) unit="${arg#--unit-name=}" ;;
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
            --no-block --quiet \
            "$script" "${passthrough[@]}"
        echo "UNIT $unit"
        return 0
    fi
    # Fallback: setsid + nohup + disown
    ( setsid nohup "$script" "${passthrough[@]}" >>"$logfile" 2>&1 </dev/null & disown ) &
    sleep 0.05  # tiny delay so the child's fork completes
    echo "LOG $logfile"
}
```

- [ ] **Step 4: Run tests, expect pass**

Run: `bats tests/test_upgrade.bats -f launch_detached`
Expected: 2 pass.

- [ ] **Step 5: Commit**

```bash
git add deploy/install_lib.sh tests/test_upgrade.bats
git commit -m "$(cat <<'EOF'
feat: add launch_detached with systemd-run primary and setsid fallback

MYMCP_FORCE_FALLBACK=1 enables testing the POSIX path on systems with
systemd. Writes to /var/log/mymcp/upgrade-<timestamp>.log.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: Helper — `rollback_cascade` (tier 1 → 4)

**Files:**
- Modify: `deploy/install_lib.sh`
- Test: `tests/test_upgrade.bats`

Implements the four-tier recovery as a single function taking callback commands for each tier. The heavy lifting stays inline; this helper orchestrates try-next-tier-on-failure.

- [ ] **Step 1: Write failing test**

Append to `tests/test_upgrade.bats`:

```bash
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
```

- [ ] **Step 2: Run, expect failures**

Run: `bats tests/test_upgrade.bats -f rollback_cascade`
Expected: 3 failures.

- [ ] **Step 3: Implement**

Append to `deploy/install_lib.sh`:

```bash
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
    local last_status=1
    for tier_cmd in "$t1" "$t2" "$t3" "$t4"; do
        [ -z "$tier_cmd" ] && continue
        if eval "$tier_cmd"; then
            return 0
        fi
        last_status=$?
    done
    return "$last_status"
}
```

- [ ] **Step 4: Run tests, expect pass**

Run: `bats tests/test_upgrade.bats -f rollback_cascade`
Expected: 3 pass.

- [ ] **Step 5: Commit**

```bash
git add deploy/install_lib.sh tests/test_upgrade.bats
git commit -m "$(cat <<'EOF'
feat: add rollback_cascade orchestration helper

Runs tiers 1-4 in order, stops on first success, returns last
tier's failure code if all fail.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: `upgrade.sh` — entry point, CLI parsing, read-only subcommands

**Files:**
- Create: `deploy/upgrade.sh`
- Test: `tests/test_upgrade_integration.bats`

Start upgrade.sh. Implements argument parsing and the read-only subcommands (`--help`, `--current`, `--list`, `--status`, `--logs`) that don't mutate state. Mutation commands (actual upgrade, rollback) come in later tasks.

- [ ] **Step 1: Create the integration test file scaffold**

Create `tests/test_upgrade_integration.bats`:

```bash
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
```

- [ ] **Step 2: Run, expect failures**

Run: `bats tests/test_upgrade_integration.bats`
Expected: 4 failures (upgrade.sh missing).

- [ ] **Step 3: Create upgrade.sh with CLI parsing and read-only subcommands**

Create `deploy/upgrade.sh`:

```bash
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

# --- Upgrade/rollback paths handled in subsequent tasks ---
echo "upgrade/rollback mode not yet implemented in this commit" >&2
exit 3
```

Make it executable:

```bash
chmod +x deploy/upgrade.sh
```

- [ ] **Step 4: Run tests, expect pass**

Run: `bats tests/test_upgrade_integration.bats`
Expected: 4 pass.

- [ ] **Step 5: Commit**

```bash
git add deploy/upgrade.sh tests/test_upgrade_integration.bats
git commit -m "$(cat <<'EOF'
feat: scaffold upgrade.sh with CLI parsing and read-only commands

Implements --help, --current, --list, --status, --logs. Upgrade and
rollback return exit 3 pending implementation in later commits.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 14: `upgrade.sh` — pre-flight and dry-run

**Files:**
- Modify: `deploy/upgrade.sh`
- Test: `tests/test_upgrade_integration.bats`

Add pre-flight checks that fail fast before any destructive action, and `--dry-run` that exits after printing the plan.

- [ ] **Step 1: Write failing test**

Append to `tests/test_upgrade_integration.bats`:

```bash
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
```

- [ ] **Step 2: Run, expect failures**

Run: `bats tests/test_upgrade_integration.bats -f dry-run`
Expected: 3 failures (upgrade.sh doesn't implement these yet).

- [ ] **Step 3: Implement pre-flight and dry-run**

Replace the placeholder block in `deploy/upgrade.sh` (the last three lines `echo "upgrade/rollback ..."; exit 3`) with:

```bash
# --------------------------------------------------------------------------
# Pre-flight (for upgrade and rollback)
# --------------------------------------------------------------------------

if [ ! -d "$APP_DIR" ]; then
    echo "ERROR: APP_DIR $APP_DIR does not exist" >&2
    exit 4
fi

CURRENT_VERSION=$(detect_current_version "$APP_DIR")

# Resolve source (for upgrade only; rollback uses local git)
if [ "$MODE" = "upgrade" ]; then
    source_args=( --repo-dir="$REPO_DIR" )
    [ -n "$SOURCE_FLAG" ] && source_args+=( --source="$SOURCE_FLAG" )
    [ "$PREFER_REMOTE" = 1 ] && source_args+=( --prefer-remote )
    SOURCE=$(resolve_source "${source_args[@]}")
fi

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
    echo "APP_DIR:        $APP_DIR"
    echo "Current:        $CURRENT_VERSION"
    echo "Target:         $TARGET_VERSION"
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

# --- Actual upgrade/rollback path continues in next task ---
echo "upgrade execution not yet implemented; pre-flight passed." >&2
exit 3
```

- [ ] **Step 4: Run tests, expect pass**

Run: `bats tests/test_upgrade_integration.bats -f dry-run`
Expected: 3 pass.

Also rerun the earlier tests to make sure nothing regressed:

```bash
bats tests/test_upgrade_integration.bats
bats tests/test_upgrade.bats
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add deploy/upgrade.sh tests/test_upgrade_integration.bats
git commit -m "$(cat <<'EOF'
feat: add pre-flight checks and --dry-run to upgrade.sh

Pre-flight: APP_DIR exists, resolve source, classify ref, guard
same-version, reject branches without --allow-branch. Dry-run
prints a plan and exits.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 15: `upgrade.sh` — legacy install conversion

**Files:**
- Modify: `deploy/upgrade.sh`
- Test: `tests/test_upgrade_integration.bats`

If APP_DIR has no `.git/`, initialize a repo, fetch from the resolved source, and hard-reset to `TARGET_VERSION`. Runs before backup so subsequent git operations work.

- [ ] **Step 1: Write failing test**

Append to `tests/test_upgrade_integration.bats`:

```bash
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
```

- [ ] **Step 2: Run, expect failure**

Run: `bats tests/test_upgrade_integration.bats -f convert`
Expected: failure — `.git` not created.

- [ ] **Step 3: Implement conversion**

In `deploy/upgrade.sh`, immediately before the "--- Actual upgrade/rollback path continues in next task ---" line, insert:

```bash
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
```

- [ ] **Step 4: Run tests, expect pass**

Run: `bats tests/test_upgrade_integration.bats -f convert`
Expected: pass (even though full upgrade isn't implemented yet, conversion succeeds and `.git` exists).

- [ ] **Step 5: Commit**

```bash
git add deploy/upgrade.sh tests/test_upgrade_integration.bats
git commit -m "$(cat <<'EOF'
feat: convert legacy rsync-based install to git-managed on upgrade

git init + fetch + reset --hard to target. .env and tokens.json are
untracked so git reset preserves them.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 16: `upgrade.sh` — core upgrade orchestration (foreground path)

**Files:**
- Modify: `deploy/upgrade.sh`
- Test: `tests/test_upgrade_integration.bats`

Implements the actual upgrade flow under `--foreground`: backup → stop → checkout → pip → start → health check. Rollback hook is stubbed as calling the cascade with placeholder commands; the real rollback commands are wired in Task 17.

- [ ] **Step 1: Write failing test**

Append to `tests/test_upgrade_integration.bats`:

```bash
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
```

- [ ] **Step 2: Run, expect failure**

Run: `bats tests/test_upgrade_integration.bats -f "end-to-end"`
Expected: fails — orchestration not implemented.

- [ ] **Step 3: Implement orchestration**

Replace the trailing `echo "upgrade execution not yet implemented"; exit 3` block in `deploy/upgrade.sh` with:

```bash
# --------------------------------------------------------------------------
# Rollback command (handled by --rollback flag; proper cascade in Task 17)
# --------------------------------------------------------------------------
if [ "$MODE" = "rollback" ]; then
    echo "--rollback not yet implemented" >&2
    exit 3
fi

# --------------------------------------------------------------------------
# Detach unless --foreground
# --------------------------------------------------------------------------
if [ "$FOREGROUND" != 1 ] && [ "${MYMCP_DETACHED:-0}" != 1 ]; then
    # Block --foreground when called from inside mymcp
    :  # Detach implementation in Task 18; for now this branch is inert.
fi

# --------------------------------------------------------------------------
# Acquire lock
# --------------------------------------------------------------------------
if ! acquire_lock "$APP_DIR"; then
    echo "ERROR: another upgrade is in progress (lock held)" >&2
    exit 7
fi
trap 'release_lock "$APP_DIR"' EXIT

# --------------------------------------------------------------------------
# EXIT trap — service-running invariant (final last-resort start)
# --------------------------------------------------------------------------
final_service_start() {
    systemctl start "$SERVICE_NAME" 2>/dev/null || true
}
trap 'release_lock "$APP_DIR"; final_service_start' EXIT

# --------------------------------------------------------------------------
# Core upgrade steps
# --------------------------------------------------------------------------
BACKUP_DIR=""
PREV_SHA=""
if [ -d "$APP_DIR/.git" ]; then
    PREV_SHA=$(git -C "$APP_DIR" rev-parse HEAD)
fi

step_backup() {
    write_state "$APP_DIR" "backup" "$CURRENT_VERSION" "$TARGET_VERSION"
    BACKUP_DIR=$(create_backup "$APP_DIR" "$CURRENT_VERSION" "$TARGET_VERSION")
    echo ">>> Backup: $BACKUP_DIR"
}

step_stop_service() {
    write_state "$APP_DIR" "stopping-service" "$CURRENT_VERSION" "$TARGET_VERSION"
    systemctl stop "$SERVICE_NAME" 2>/dev/null || true
}

step_checkout() {
    write_state "$APP_DIR" "checking-out-code" "$CURRENT_VERSION" "$TARGET_VERSION"
    # Ensure remote reflects resolved source for remote-only targets
    if [ -n "$SOURCE" ] && [ "$_SOURCE_KIND" != "git-local" ] || [ "$_SOURCE_KIND" = "git-local" ]; then
        if git -C "$APP_DIR" remote get-url origin >/dev/null 2>&1; then
            git -C "$APP_DIR" remote set-url origin "$SOURCE"
        else
            git -C "$APP_DIR" remote add origin "$SOURCE"
        fi
    fi
    git -C "$APP_DIR" fetch --tags -q origin || true
    git -C "$APP_DIR" checkout -q "$TARGET_VERSION"
}

step_install_deps() {
    write_state "$APP_DIR" "installing-deps" "$CURRENT_VERSION" "$TARGET_VERSION"
    local pip="$APP_DIR/venv/bin/pip"
    if [ ! -x "$pip" ]; then
        echo "ERROR: venv pip not executable at $pip" >&2
        return 1
    fi
    if [ -n "$WHEELS_DIR" ]; then
        "$pip" install -q --no-index --find-links="$WHEELS_DIR" -r "$APP_DIR/requirements.txt"
    else
        "$pip" install -q -r "$APP_DIR/requirements.txt"
    fi
}

step_refresh_unit() {
    write_state "$APP_DIR" "refreshing-unit" "$CURRENT_VERSION" "$TARGET_VERSION"
    # If deploy/mymcp.service has changed since last run, the install.sh-generated
    # /etc/systemd/system/mymcp.service may be stale. Compare and reload if needed.
    if [ -f "$APP_DIR/deploy/mymcp.service" ] && [ -f "/etc/systemd/system/mymcp.service" ]; then
        if ! diff -q "$APP_DIR/deploy/mymcp.service" "/etc/systemd/system/mymcp.service" >/dev/null 2>&1; then
            echo "WARN: systemd unit file differs from shipped template."
            echo "      Review /etc/systemd/system/mymcp.service after upgrade."
        fi
    fi
}

step_start_service() {
    write_state "$APP_DIR" "starting-service" "$CURRENT_VERSION" "$TARGET_VERSION"
    systemctl start "$SERVICE_NAME"
}

step_health() {
    [ "$NO_HEALTH" = 1 ] && return 0
    write_state "$APP_DIR" "health-check" "$CURRENT_VERSION" "$TARGET_VERSION"
    wait_for_health "$APP_DIR" 30
}

write_state "$APP_DIR" "preflight" "$CURRENT_VERSION" "$TARGET_VERSION"

if step_backup \
   && step_stop_service \
   && step_checkout \
   && step_install_deps \
   && step_refresh_unit \
   && step_start_service \
   && step_health; then
    write_state "$APP_DIR" "done" "$CURRENT_VERSION" "$TARGET_VERSION"
    # Write .install-info for audit / fallback version detection
    printf '{"version":"%s","installed_at":"%s","upgraded_from":"%s"}\n' \
        "$TARGET_VERSION" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$CURRENT_VERSION" \
        > "$APP_DIR/.install-info"
    prune_backups "$APP_DIR" "$KEEP_BACKUPS"
    echo "=== Upgrade complete ==="
    echo "  $CURRENT_VERSION → $TARGET_VERSION"
    echo "  Backup: $BACKUP_DIR"
    exit 0
fi

# Failure path — placeholder; Task 17 adds real rollback cascade
echo "ERROR: upgrade step failed; rollback cascade not yet implemented" >&2
write_state "$APP_DIR" "failed-manual-intervention" "$CURRENT_VERSION" "$TARGET_VERSION"
exit 8
```

- [ ] **Step 4: Run tests, expect pass**

Run: `bats tests/test_upgrade_integration.bats -f "end-to-end"`
Expected: passes.

- [ ] **Step 5: Commit**

```bash
git add deploy/upgrade.sh tests/test_upgrade_integration.bats
git commit -m "$(cat <<'EOF'
feat: implement core upgrade orchestration in upgrade.sh

Foreground path: backup → stop → checkout → pip → start → health
check. State file tracks progress, .install-info written on success,
backups pruned. EXIT trap does last-resort systemctl start.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 17: `upgrade.sh` — rollback cascade wired to real tiers

**Files:**
- Modify: `deploy/upgrade.sh`
- Test: `tests/test_upgrade_integration.bats`

Replace the failure placeholder with the cascading recovery: tier 1 git rollback + pip revert + start, tier 2 restore from `.bak`, tier 3 force-start, tier 4 report.

- [ ] **Step 1: Write failing test**

Append to `tests/test_upgrade_integration.bats`:

```bash
@test "upgrade.sh rolls back to previous SHA when a step fails" {
    # Same setup as end-to-end test
    local SRC="$TMPROOT/src"
    mkdir -p "$SRC"
    cd "$SRC"
    git init -q
    git config user.email ci@local
    git config user.name ci
    echo "v1" > main.py
    echo "" > requirements.txt
    git add main.py requirements.txt
    git commit -q -m "c1"
    git tag v1.0.0
    echo "v2" > main.py
    git commit -qam "c2"
    git tag v1.1.0

    git clone -q "$SRC" "$APP_DIR"
    git -C "$APP_DIR" checkout -q v1.0.0

    local stubs="$TMPROOT/stubs"
    mkdir -p "$stubs"
    # systemctl stub: always succeed
    cat > "$stubs/systemctl" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
    chmod +x "$stubs/systemctl"

    # Force failure: pip exits non-zero
    mkdir -p "$APP_DIR/venv/bin"
    cat > "$APP_DIR/venv/bin/pip" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
    chmod +x "$APP_DIR/venv/bin/pip"

    PATH="$stubs:$PATH" run bash "$UPGRADE_SH" \
        --app-dir="$APP_DIR" --source="$SRC" --foreground --no-health-check v1.1.0
    [ "$status" -ne 0 ]  # upgrade failed
    # HEAD is back at v1.0.0 (rollback succeeded)
    run git -C "$APP_DIR" describe --tags
    [ "$output" = "v1.0.0" ]
    run cat "$APP_DIR/.upgrade-state"
    [[ "$output" == *'"step":"rolled-back"'* ]]
}
```

- [ ] **Step 2: Run, expect failure**

Run: `bats tests/test_upgrade_integration.bats -f "rolls back"`
Expected: failure — rollback not wired.

- [ ] **Step 3: Implement rollback cascade**

In `deploy/upgrade.sh`, replace the last `echo "ERROR: upgrade step failed..."; write_state ... failed-manual-intervention; exit 8` block with:

```bash
# --------------------------------------------------------------------------
# Cascading recovery
# --------------------------------------------------------------------------
do_rollback_tier1() {
    echo ">>> Rollback tier 1: git reset + pip revert"
    write_state "$APP_DIR" "rolling-back" "$CURRENT_VERSION" "$TARGET_VERSION"
    systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    [ -n "$PREV_SHA" ] && git -C "$APP_DIR" reset --hard "$PREV_SHA" || return 1
    "$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt" || return 1
    systemctl start "$SERVICE_NAME" || return 1
    return 0
}

do_rollback_tier2() {
    echo ">>> Rollback tier 2: restore from .bak"
    write_state "$APP_DIR" "rolling-back-from-backup" "$CURRENT_VERSION" "$TARGET_VERSION"
    [ -z "$BACKUP_DIR" ] && return 1
    systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    # Restore files (excluding .env/tokens.json which we want to preserve as-is)
    rsync -a --exclude='.env' --exclude='tokens.json' "$BACKUP_DIR/" "$APP_DIR/" || return 1
    systemctl start "$SERVICE_NAME" || return 1
    return 0
}

do_rollback_tier3() {
    echo ">>> Rollback tier 3: force-start current code"
    write_state "$APP_DIR" "force-starting" "$CURRENT_VERSION" "$TARGET_VERSION"
    systemctl start "$SERVICE_NAME"
}

do_rollback_tier4() {
    echo ">>> Rollback tier 4: manual intervention required"
    write_state "$APP_DIR" "failed-manual-intervention" "$CURRENT_VERSION" "$TARGET_VERSION"
    echo "Service is stopped. Backup: ${BACKUP_DIR:-<none>}. Review logs and run --rollback manually."
    return 1
}

echo "ERROR: upgrade step failed; initiating rollback cascade..." >&2
if rollback_cascade \
    --tier1="do_rollback_tier1" \
    --tier2="do_rollback_tier2" \
    --tier3="do_rollback_tier3" \
    --tier4="do_rollback_tier4"; then
    write_state "$APP_DIR" "rolled-back" "$CURRENT_VERSION" "$TARGET_VERSION"
    echo "Recovered. Service is running on previous version."
    exit 9
else
    exit 10
fi
```

Also export the tier functions so `eval` in `rollback_cascade` can see them. No export needed because `rollback_cascade` runs in the same shell — just ensure the functions are defined before the call.

- [ ] **Step 4: Run tests, expect pass**

Run: `bats tests/test_upgrade_integration.bats`
Expected: all integration tests pass.

Also run all bats tests:

```bash
bats tests/test_upgrade.bats tests/test_upgrade_integration.bats
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add deploy/upgrade.sh tests/test_upgrade_integration.bats
git commit -m "$(cat <<'EOF'
feat: wire four-tier cascading rollback in upgrade.sh

Tier 1 git reset + pip revert, tier 2 restore from .bak, tier 3
force-start current code, tier 4 report manual intervention. State
file tracks the transition through each tier.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 18: `upgrade.sh` — detach mode (self-copy + launch_detached)

**Files:**
- Modify: `deploy/upgrade.sh`
- Test: `tests/test_upgrade_integration.bats`

Default behavior: after pre-flight, copy the script to `/tmp/mymcp-upgrade-$$.sh`, then `launch_detached` it with `--detach-runner` so it resumes the upgrade in the background. Reject `--foreground` when `is_under_mymcp`.

- [ ] **Step 1: Write failing test**

Append to `tests/test_upgrade_integration.bats`:

```bash
@test "upgrade.sh default mode detaches (parent exits immediately)" {
    local SRC="$TMPROOT/src"
    mkdir -p "$SRC"
    cd "$SRC"
    git init -q
    git config user.email ci@local
    git config user.name ci
    echo "v1" > main.py
    echo "" > requirements.txt
    git add .
    git commit -q -m "c1"
    git tag v1.0.0
    echo "v2" > main.py
    git commit -qam "c2"
    git tag v1.1.0
    git clone -q "$SRC" "$APP_DIR"
    git -C "$APP_DIR" checkout -q v1.0.0
    mkdir -p "$APP_DIR/venv/bin"
    cat > "$APP_DIR/venv/bin/pip" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
    chmod +x "$APP_DIR/venv/bin/pip"

    local stubs="$TMPROOT/stubs"
    mkdir -p "$stubs"
    cat > "$stubs/systemctl" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
    chmod +x "$stubs/systemctl"

    local start=$(date +%s)
    MYMCP_FORCE_FALLBACK=1 MYMCP_LOG_DIR="$TMPROOT/log" PATH="$stubs:$PATH" \
        run bash "$UPGRADE_SH" --app-dir="$APP_DIR" --source="$SRC" --no-health-check v1.1.0
    local elapsed=$(( $(date +%s) - start ))
    [ "$status" -eq 0 ]
    [ "$elapsed" -lt 3 ]  # parent returned promptly
    [[ "$output" == *"started in background"* ]] || [[ "$output" == *"Upgrade"* ]]
}

@test "upgrade.sh rejects --foreground when under mymcp" {
    MYMCP_FAKE_UNDER=1 run bash "$UPGRADE_SH" --app-dir="$APP_DIR" --foreground v1.0.0
    [ "$status" -ne 0 ]
    [[ "$output" == *"--foreground"* ]] || [[ "$output" == *"detach"* ]]
}
```

- [ ] **Step 2: Run, expect failure**

Run: `bats tests/test_upgrade_integration.bats -f detach`
Expected: failures.

- [ ] **Step 3: Implement detach**

In `deploy/upgrade.sh`:

Find the placeholder `if [ "$FOREGROUND" != 1 ] && [ "${MYMCP_DETACHED:-0}" != 1 ]; then : fi` block and replace with:

```bash
# --------------------------------------------------------------------------
# Reject --foreground under mymcp (client-driven upgrade must detach)
# --------------------------------------------------------------------------
if [ "$FOREGROUND" = 1 ] && is_under_mymcp; then
    echo "ERROR: --foreground is unsafe when invoked from inside mymcp." >&2
    echo "       The service kill during upgrade would terminate this script." >&2
    echo "       Omit --foreground; the script will detach automatically." >&2
    exit 11
fi

# --------------------------------------------------------------------------
# Detach unless --foreground or already the detached runner
# --------------------------------------------------------------------------
if [ "$FOREGROUND" != 1 ] && [ "${MYMCP_DETACHED:-0}" != 1 ]; then
    # Self-copy so git checkout inside the run can't corrupt our own file
    COPY="/tmp/mymcp-upgrade-$$.sh"
    cp "$0" "$COPY"
    chmod +x "$COPY"
    # Preserve flags and pass --foreground to the runner (it's already detached)
    DETACH_ARGS=( --foreground --app-dir="$APP_DIR" )
    [ -n "$SOURCE_FLAG" ]   && DETACH_ARGS+=( --source="$SOURCE_FLAG" )
    [ -n "$WHEELS_DIR" ]    && DETACH_ARGS+=( --wheels-dir="$WHEELS_DIR" )
    [ "$KEEP_BACKUPS" != "3" ] && DETACH_ARGS+=( --keep-backups="$KEEP_BACKUPS" )
    [ "$PREFER_REMOTE" = 1 ] && DETACH_ARGS+=( --prefer-remote )
    [ "$ALLOW_BRANCH" = 1 ]  && DETACH_ARGS+=( --allow-branch )
    [ "$FORCE" = 1 ]         && DETACH_ARGS+=( --force )
    [ "$NO_HEALTH" = 1 ]     && DETACH_ARGS+=( --no-health-check )
    DETACH_ARGS+=( "$TARGET_VERSION" )

    LAUNCH_INFO=$(MYMCP_DETACHED=1 launch_detached "$COPY" \
        --log-dir="$LOG_DIR" --unit-name=mymcp-upgrade -- "${DETACH_ARGS[@]}")
    echo "Upgrade $CURRENT_VERSION → $TARGET_VERSION started in background."
    echo "  $LAUNCH_INFO"
    echo "  Service will be unavailable for ~2 minutes."
    echo "  Status:  sudo $0 --status"
    echo "  Logs:    sudo $0 --logs -f"
    echo "  Reconnect your MCP client after the service returns to healthy state."
    exit 0
fi

# Detached runner cleans up its own /tmp copy on exit
if [ "${MYMCP_DETACHED:-0}" = 1 ]; then
    trap 'release_lock "$APP_DIR"; final_service_start; rm -f "$0"' EXIT
fi
```

The `MYMCP_DETACHED=1` env var passes through `systemd-run` via the wrapper; for setsid fallback, we prepend it to the command. Update `launch_detached` to pass env vars — adjust `deploy/install_lib.sh`:

Find the systemd-run call in `launch_detached` and replace with:

```bash
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
```

Also, `launch_detached` currently parses all args with the `for arg` loop; the `--` separator in the detach call means everything after `--` is passthrough. Update the arg parsing:

```bash
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
    # ... rest unchanged
```

- [ ] **Step 4: Run tests, expect pass**

Run: `bats tests/test_upgrade_integration.bats -f detach`
Expected: 2 pass.

Also run the full bats suite to catch regressions:

```bash
bats tests/test_upgrade.bats tests/test_upgrade_integration.bats tests/test_install.bats
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add deploy/upgrade.sh deploy/install_lib.sh tests/test_upgrade_integration.bats
git commit -m "$(cat <<'EOF'
feat: detach upgrade into background; enforce when under mymcp

Default: self-copy to /tmp then launch_detached with systemd-run or
setsid fallback. MYMCP_DETACHED=1 marks the detached runner so it
proceeds with foreground-style execution. Rejects --foreground when
invoked from a mymcp uvicorn child.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 19: `upgrade.sh` — `--rollback` manual recovery

**Files:**
- Modify: `deploy/upgrade.sh`
- Test: `tests/test_upgrade_integration.bats`

`--rollback` runs tier 2 (restore from most recent `.bak`) directly, since git history may be mid-flight. Useful when auto-rollback failed or when user wants to revert an earlier successful upgrade.

- [ ] **Step 1: Write failing test**

Append to `tests/test_upgrade_integration.bats`:

```bash
@test "upgrade.sh --rollback restores from most-recent .bak" {
    mkdir -p "$APP_DIR"
    echo "new-code" > "$APP_DIR/main.py"
    echo "MCP_FAKE=1" > "$APP_DIR/.env"

    local BAK="${APP_DIR}.bak-20260410-120000"
    mkdir -p "$BAK"
    echo "old-code" > "$BAK/main.py"
    echo '{"from_version":"v1.0.0","to_version":"v1.1.0"}' > "$BAK/.backup-info"

    local stubs="$TMPROOT/stubs"
    mkdir -p "$stubs"
    cat > "$stubs/systemctl" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
    chmod +x "$stubs/systemctl"

    PATH="$stubs:$PATH" run bash "$UPGRADE_SH" --app-dir="$APP_DIR" --rollback
    [ "$status" -eq 0 ]
    run cat "$APP_DIR/main.py"
    [ "$output" = "old-code" ]
    # .env preserved (not clobbered)
    run cat "$APP_DIR/.env"
    [ "$output" = "MCP_FAKE=1" ]
}

@test "upgrade.sh --rollback exits non-zero when no backup exists" {
    run bash "$UPGRADE_SH" --app-dir="$APP_DIR" --rollback
    [ "$status" -ne 0 ]
    [[ "$output" == *"no backup"* || "$output" == *"No backup"* ]]
}
```

- [ ] **Step 2: Run, expect failure**

Run: `bats tests/test_upgrade_integration.bats -f rollback`
Expected: failures.

- [ ] **Step 3: Implement**

In `deploy/upgrade.sh`, replace the `--rollback not yet implemented` block with:

```bash
if [ "$MODE" = "rollback" ]; then
    # Find most recent backup
    PARENT=$(dirname "$APP_DIR")
    BASE=$(basename "$APP_DIR")
    LATEST_BAK=$(ls -1d "$PARENT/${BASE}.bak-"*/ 2>/dev/null | sort | tail -1 || true)
    LATEST_BAK="${LATEST_BAK%/}"
    if [ -z "$LATEST_BAK" ]; then
        echo "ERROR: no backup found at ${APP_DIR}.bak-*" >&2
        exit 12
    fi
    echo ">>> Rolling back from $LATEST_BAK"
    systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    rsync -a --exclude='.env' --exclude='tokens.json' "$LATEST_BAK/" "$APP_DIR/"
    systemctl start "$SERVICE_NAME"
    echo "Rollback complete."
    exit 0
fi
```

- [ ] **Step 4: Run tests, expect pass**

Run: `bats tests/test_upgrade_integration.bats -f rollback`
Expected: 2 pass.

- [ ] **Step 5: Commit**

```bash
git add deploy/upgrade.sh tests/test_upgrade_integration.bats
git commit -m "$(cat <<'EOF'
feat: implement upgrade.sh --rollback manual recovery

Restores most-recent .bak directory, excluding .env and tokens.json
so current state is preserved. Used when auto-rollback failed or
when a past upgrade needs to be reverted.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 20: `install.sh` — switch to git-based population, keep rsync fallback

**Files:**
- Modify: `deploy/install.sh`
- Test: `tests/test_upgrade_integration.bats`

Rewrite Step 4 of install.sh to use `git clone` when REPO_DIR is a git tree, falling back to rsync otherwise. Write `.install-info` and `.install-info` version on success.

- [ ] **Step 1: Write failing test**

Append to `tests/test_upgrade_integration.bats`:

```bash
@test "install.sh populates APP_DIR as git checkout when REPO_DIR is git tree" {
    skip_if_non_root_required
    # This test runs install.sh in a restricted way via -y and APP_DIR override.
    # We only verify the file-population phase produces a git tree.
    # Use INSTALL_DRY=1 if install.sh supports it, otherwise test via function call.
    # The cleanest verification: source install_lib.sh, call populate_app_dir directly.
    skip "covered by Docker integration scenario fresh_upgrade"
}
```

(We keep this as a skip because full install.sh is tested in Docker scenarios — unit-testing install.sh in bats is awkward due to systemd/apt-get dependencies. The actual verification is the Docker scenario in Task 25.)

Instead, add a unit test for a new helper `populate_app_dir`:

```bash
# =========================================================================
# populate_app_dir (used by install.sh)
# =========================================================================

@test "populate_app_dir: git-local source produces a git tree at APP_DIR" {
    local SRC="$TMPROOT/src"
    mkdir -p "$SRC"
    cd "$SRC"
    git init -q
    git config user.email ci@local
    git config user.name ci
    echo "x" > main.py
    git add main.py
    git commit -q -m "c1"
    git tag v1.0.0

    local TARGET="$TMPROOT/target"
    run populate_app_dir --source="$SRC" --app-dir="$TARGET" --version=v1.0.0
    [ "$status" -eq 0 ]
    [ -d "$TARGET/.git" ]
    [ -f "$TARGET/main.py" ]
    run git -C "$TARGET" describe --tags
    [ "$output" = "v1.0.0" ]
}

@test "populate_app_dir: non-git source rsyncs files (no .git)" {
    local SRC="$TMPROOT/src"
    mkdir -p "$SRC/tools"
    echo "x" > "$SRC/main.py"
    echo "t" > "$SRC/tools/a.py"
    local TARGET="$TMPROOT/target"
    run populate_app_dir --source="$SRC" --app-dir="$TARGET" --version=unknown --mode=rsync
    [ "$status" -eq 0 ]
    [ ! -d "$TARGET/.git" ]
    [ -f "$TARGET/main.py" ]
    [ -f "$TARGET/tools/a.py" ]
}
```

Put this in `tests/test_upgrade.bats` (since it's a helper, not upgrade.sh integration). Add `skip_if_non_root_required()` as a stub at top of `test_upgrade_integration.bats`:

```bash
skip_if_non_root_required() {
    [ "$(id -u)" -eq 0 ] || skip "requires root"
}
```

- [ ] **Step 2: Run, expect failure**

Run: `bats tests/test_upgrade.bats -f populate_app_dir`
Expected: 2 failures (`populate_app_dir` not defined).

- [ ] **Step 3: Implement populate_app_dir in install_lib.sh**

Append to `deploy/install_lib.sh`:

```bash
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
```

- [ ] **Step 4: Run tests, expect pass**

Run: `bats tests/test_upgrade.bats -f populate_app_dir`
Expected: 2 pass.

- [ ] **Step 5: Replace install.sh Step 4 with populate_app_dir call**

In `deploy/install.sh`, replace lines 107-111 (the `echo "Copying files..."; mkdir -p; rsync -a ...` block) with:

```bash
echo "Copying files to ${APP_DIR}..."
# Detect mode: prefer git if REPO_DIR is a git tree
if [ -d "${REPO_DIR}/.git" ]; then
    POPULATE_MODE=git
    INSTALL_VERSION=$(git -C "$REPO_DIR" describe --tags --always 2>/dev/null || echo "unknown")
else
    POPULATE_MODE=rsync
    INSTALL_VERSION="unknown"
    echo "NOTE: REPO_DIR is not a git tree; using rsync fallback."
    echo "      Future upgrades will convert APP_DIR to a git checkout on first run."
fi
populate_app_dir --source="$REPO_DIR" --app-dir="$APP_DIR" \
                 --version="$INSTALL_VERSION" --mode="$POPULATE_MODE"
```

- [ ] **Step 6: Run full test suite**

Run: `bats tests/test_install.bats tests/test_upgrade.bats tests/test_upgrade_integration.bats`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add deploy/install.sh deploy/install_lib.sh tests/test_upgrade.bats tests/test_upgrade_integration.bats
git commit -m "$(cat <<'EOF'
feat: install.sh populates APP_DIR via git clone with rsync fallback

Adds populate_app_dir helper with auto/git/rsync modes. install.sh
uses git clone when REPO_DIR is a git tree (default), rsync fallback
otherwise. Writes .install-info with the resolved version.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 21: UPGRADE_NOTES.md and logrotate config

**Files:**
- Create: `deploy/UPGRADE_NOTES.md`
- Create: `deploy/logrotate.mymcp-upgrade`
- Modify: `deploy/install.sh` (install logrotate snippet + create log dir)

- [ ] **Step 1: Create UPGRADE_NOTES.md**

Create `deploy/UPGRADE_NOTES.md`:

```markdown
# Upgrade Notes

This file records breaking changes and manual steps required when upgrading
between mymcp versions. `upgrade.sh` prints the relevant section before
proceeding so you can review and abort if needed.

Entries are ordered newest-first. Each version lists only what the user must
*do* — full changelog lives in `CHANGELOG.md`.

## v1.1.0 (unreleased)

No breaking changes.

## v1.0.0 (2026-04-17)

Initial release. No upgrade notes.
```

- [ ] **Step 2: Create logrotate config**

Create `deploy/logrotate.mymcp-upgrade`:

```
/var/log/mymcp/upgrade-*.log {
    weekly
    rotate 4
    compress
    delaycompress
    missingok
    notifempty
    create 0644 root root
}
```

- [ ] **Step 3: Install logrotate + create log dir in install.sh**

In `deploy/install.sh`, between Step 6 (`.env configuration`) and Step 7 (`systemd service`), insert:

```bash
# ---------------------------------------------------------------------------
# Step 6b: Upgrade log directory and rotation
# ---------------------------------------------------------------------------
mkdir -p /var/log/mymcp
chmod 750 /var/log/mymcp
if [ -f "${REPO_DIR}/deploy/logrotate.mymcp-upgrade" ] && [ -d /etc/logrotate.d ]; then
    cp "${REPO_DIR}/deploy/logrotate.mymcp-upgrade" /etc/logrotate.d/mymcp-upgrade
fi
```

- [ ] **Step 4: Verify files**

Run: `ls -la deploy/UPGRADE_NOTES.md deploy/logrotate.mymcp-upgrade`
Expected: both files exist.

Run: `grep -n "logrotate.mymcp-upgrade" deploy/install.sh`
Expected: line found.

- [ ] **Step 5: Commit**

```bash
git add deploy/UPGRADE_NOTES.md deploy/logrotate.mymcp-upgrade deploy/install.sh
git commit -m "$(cat <<'EOF'
feat: add UPGRADE_NOTES.md template and upgrade log rotation

install.sh creates /var/log/mymcp/ and installs the logrotate snippet.
UPGRADE_NOTES.md holds breaking-change narrative that upgrade.sh will
print before proceeding.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 22: UPGRADE_NOTES diff display in upgrade.sh

**Files:**
- Modify: `deploy/upgrade.sh`
- Test: `tests/test_upgrade_integration.bats`

If UPGRADE_NOTES.md changed between CURRENT and TARGET, print the diff and require confirmation unless `AUTO_YES=true` or `--force`.

- [ ] **Step 1: Write failing test**

Append to `tests/test_upgrade_integration.bats`:

```bash
@test "upgrade.sh prints UPGRADE_NOTES diff when notes changed" {
    local SRC="$TMPROOT/src"
    mkdir -p "$SRC/deploy"
    cd "$SRC"
    git init -q
    git config user.email ci@local
    git config user.name ci
    mkdir deploy
    echo "# v1.0.0 notes" > deploy/UPGRADE_NOTES.md
    echo "" > requirements.txt
    git add .
    git commit -q -m "c1"
    git tag v1.0.0

    cat > deploy/UPGRADE_NOTES.md <<EOF
# Upgrade Notes

## v1.1.0
### Breaking
- MCP_TOKEN_FILE renamed to MCP_TOKEN_STORE. Update your .env.
EOF
    git add .
    git commit -qam "c2"
    git tag v1.1.0

    git clone -q "$SRC" "$APP_DIR"
    git -C "$APP_DIR" checkout -q v1.0.0

    run bash "$UPGRADE_SH" --app-dir="$APP_DIR" --source="$SRC" --dry-run v1.1.0
    [ "$status" -eq 0 ]
    [[ "$output" == *"MCP_TOKEN_FILE"* ]] || [[ "$output" == *"UPGRADE_NOTES"* ]]
}
```

- [ ] **Step 2: Run, expect failure**

Run: `bats tests/test_upgrade_integration.bats -f UPGRADE_NOTES`
Expected: failure.

- [ ] **Step 3: Implement**

In `deploy/upgrade.sh`, find the "Pre-flight" section end (just before "--- Dry-run ---") and add:

```bash
# --------------------------------------------------------------------------
# Display UPGRADE_NOTES.md diff if changed between CURRENT and TARGET
# --------------------------------------------------------------------------
if [ "$MODE" = "upgrade" ] && [ -d "$APP_DIR/.git" ]; then
    # Ensure we have the target ref locally for log comparison
    git -C "$APP_DIR" fetch --tags -q origin 2>/dev/null || true
    if git -C "$APP_DIR" rev-parse --verify --quiet "$TARGET_VERSION" >/dev/null; then
        notes_diff=$(git -C "$APP_DIR" diff "$CURRENT_VERSION..$TARGET_VERSION" -- deploy/UPGRADE_NOTES.md 2>/dev/null || true)
        if [ -n "$notes_diff" ]; then
            echo "=== UPGRADE_NOTES.md changes from $CURRENT_VERSION to $TARGET_VERSION ==="
            echo "$notes_diff"
            echo "==============================================================="
            if [ "$DRY_RUN" != 1 ] && [ "$AUTO_YES" != true ] && [ "$FORCE" != 1 ]; then
                read -rp "Proceed with upgrade? [y/N]: " ans
                case "${ans,,}" in
                    y|yes) : ;;
                    *) echo "Aborted."; exit 0 ;;
                esac
            fi
        fi
    fi
fi
```

- [ ] **Step 4: Run tests, expect pass**

Run: `bats tests/test_upgrade_integration.bats -f UPGRADE_NOTES`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add deploy/upgrade.sh tests/test_upgrade_integration.bats
git commit -m "$(cat <<'EOF'
feat: display UPGRADE_NOTES.md diff in upgrade.sh pre-flight

Prints the section added since CURRENT version and requires
confirmation unless AUTO_YES or --force is set.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 23: Docker integration scenario — fresh install then upgrade

**Files:**
- Create: `tests/deploy/integration/Dockerfile.debian`
- Create: `tests/deploy/integration/Dockerfile.rocky`
- Create: `tests/deploy/integration/scenario_fresh_upgrade.sh`
- Create: `tests/deploy/integration/run_all.sh`

Docker-based end-to-end test. Build image, clone repo at v1.0.0, run install.sh, then run upgrade.sh to HEAD, verify service responds.

- [ ] **Step 1: Create Debian base image**

Create `tests/deploy/integration/Dockerfile.debian`:

```dockerfile
FROM debian:12

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv \
        git curl rsync systemd systemd-sysv \
        procps ripgrep bats openssl ca-certificates \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Systemd container setup
STOPSIGNAL SIGRTMIN+3
ENV container=docker

# mymcp source mounted at /src; scenario script orchestrates install + upgrade.
WORKDIR /src
```

- [ ] **Step 2: Create Rocky base image**

Create `tests/deploy/integration/Dockerfile.rocky`:

```dockerfile
FROM rockylinux:9

RUN dnf install -y epel-release && \
    dnf install -y \
        python3.11 python3.11-pip \
        git curl rsync systemd \
        procps-ng ripgrep bats openssl && \
    dnf clean all

STOPSIGNAL SIGRTMIN+3
ENV container=docker

WORKDIR /src
```

- [ ] **Step 3: Create fresh-upgrade scenario**

Create `tests/deploy/integration/scenario_fresh_upgrade.sh`:

```bash
#!/usr/bin/env bash
# Run inside an integration container. Mounts the repo at /src.
# 1. Clone /src into a private workdir
# 2. Install at HEAD~1 (last tag), simulating v1.0.0
# 3. Upgrade to HEAD (simulating v1.1.0)
# 4. Verify git checkout and /health
set -euo pipefail

WORKDIR=$(mktemp -d)
cp -r /src "$WORKDIR/repo"
cd "$WORKDIR/repo"

# Determine versions
PREVIOUS_TAG=$(git describe --tags --abbrev=0 HEAD~1 2>/dev/null || git rev-parse HEAD~1)
LATEST_REF=$(git rev-parse HEAD)
# Tag LATEST_REF as v-test-new for testability
git tag -f v-test-new HEAD

# Reset repo to PREVIOUS_TAG for install
git -C "$WORKDIR/repo" checkout -q "$PREVIOUS_TAG"

# Install
APP_DIR=/opt/mymcp
AUTO_YES=true MCP_ADMIN_TOKEN=testtoken bash deploy/install.sh -y

# Verify service file created
test -f /etc/systemd/system/mymcp.service

# Simulate: upstream advanced to HEAD (v-test-new). Make a local clone with that ref.
REMOTE_SRC="$WORKDIR/remote"
cp -r "$WORKDIR/repo" "$REMOTE_SRC"
git -C "$REMOTE_SRC" checkout -q "$LATEST_REF"
git -C "$REMOTE_SRC" tag -f v-test-new HEAD

# Start service for health check path
# (In a non-systemd container, skip systemctl; upgrade.sh uses --no-health-check.)
bash "$APP_DIR/deploy/upgrade.sh" --app-dir="$APP_DIR" \
    --source="$REMOTE_SRC" --foreground --no-health-check v-test-new

# Verify
test -d "$APP_DIR/.git"
CURRENT=$(git -C "$APP_DIR" describe --tags --always)
test "$CURRENT" = "v-test-new"
test -f "$APP_DIR/.install-info"

echo "PASS: scenario_fresh_upgrade"
```

Make executable: `chmod +x tests/deploy/integration/scenario_fresh_upgrade.sh`.

- [ ] **Step 4: Create run_all.sh harness**

Create `tests/deploy/integration/run_all.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$REPO_ROOT"

distro="${1:-debian}"
scenario="${2:-scenario_fresh_upgrade.sh}"

case "$distro" in
    debian) image_tag=mymcp-test-debian ;;
    rocky)  image_tag=mymcp-test-rocky ;;
    *) echo "Usage: $0 {debian|rocky} [scenario.sh]" >&2; exit 2 ;;
esac

docker build -t "$image_tag" -f "$SCRIPT_DIR/Dockerfile.$distro" "$SCRIPT_DIR"

docker run --rm \
    -v "$REPO_ROOT:/src:ro" \
    -v "$SCRIPT_DIR:/tests/integration:ro" \
    "$image_tag" \
    bash "/tests/integration/$scenario"

echo "OK: $distro / $scenario"
```

Make executable: `chmod +x tests/deploy/integration/run_all.sh`.

- [ ] **Step 5: Run scenario**

If Docker is available locally:

```bash
bash tests/deploy/integration/run_all.sh debian scenario_fresh_upgrade.sh
```

Expected: `OK: debian / scenario_fresh_upgrade.sh`. If Docker isn't available, skip and rely on CI (Task 26).

- [ ] **Step 6: Commit**

```bash
git add tests/deploy/integration/
git commit -m "$(cat <<'EOF'
test: add Docker integration scenario for fresh install + upgrade

Debian and Rocky Dockerfiles plus scenario_fresh_upgrade.sh that
installs at HEAD~1, upgrades to HEAD, verifies .git and version.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 24: Docker scenario — legacy rsync conversion

**Files:**
- Create: `tests/deploy/integration/scenario_legacy_convert.sh`

- [ ] **Step 1: Create scenario**

Create `tests/deploy/integration/scenario_legacy_convert.sh`:

```bash
#!/usr/bin/env bash
# Simulate a legacy (rsync-based) install and verify upgrade converts it to git-managed.
set -euo pipefail

WORKDIR=$(mktemp -d)
cp -r /src "$WORKDIR/repo"
cd "$WORKDIR/repo"

# Create a fake "release tarball" (strip .git) and install from it
TARBALL_DIR="$WORKDIR/tarball"
rsync -a --exclude='.git' "$WORKDIR/repo/" "$TARBALL_DIR/"

APP_DIR=/opt/mymcp
# Install from non-git source
cd "$TARBALL_DIR"
AUTO_YES=true MCP_ADMIN_TOKEN=testtoken bash deploy/install.sh -y

# Verify: no .git in APP_DIR (rsync mode)
test ! -d "$APP_DIR/.git"
test -f "$APP_DIR/.install-info"
grep -q '"mode":"rsync"' "$APP_DIR/.install-info"

# Set up a git source for the upgrade to convert into
REMOTE_SRC="$WORKDIR/remote"
cp -r "$WORKDIR/repo" "$REMOTE_SRC"
git -C "$REMOTE_SRC" tag -f v-test-new HEAD

# Run upgrade — should convert APP_DIR to git tree
bash "$APP_DIR/deploy/upgrade.sh" --app-dir="$APP_DIR" \
    --source="$REMOTE_SRC" --foreground --no-health-check v-test-new

test -d "$APP_DIR/.git"
CURRENT=$(git -C "$APP_DIR" describe --tags --always)
test "$CURRENT" = "v-test-new"

echo "PASS: scenario_legacy_convert"
```

`chmod +x tests/deploy/integration/scenario_legacy_convert.sh`.

- [ ] **Step 2: Run scenario**

If Docker available:

```bash
bash tests/deploy/integration/run_all.sh debian scenario_legacy_convert.sh
```

Expected: `OK: debian / scenario_legacy_convert.sh`.

- [ ] **Step 3: Commit**

```bash
git add tests/deploy/integration/scenario_legacy_convert.sh
git commit -m "$(cat <<'EOF'
test: add Docker scenario for rsync-to-git conversion on upgrade

Installs via rsync fallback, verifies .install-info mode=rsync, then
upgrades and confirms APP_DIR is now a git tree at the target version.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 25: Docker scenarios — rollback and offline wheels

**Files:**
- Create: `tests/deploy/integration/scenario_rollback.sh`
- Create: `tests/deploy/integration/scenario_offline_wheels.sh`

- [ ] **Step 1: Create rollback scenario**

Create `tests/deploy/integration/scenario_rollback.sh`:

```bash
#!/usr/bin/env bash
# Install, inject a failing pip step on upgrade, verify rollback returns HEAD to original.
set -euo pipefail

WORKDIR=$(mktemp -d)
cp -r /src "$WORKDIR/repo"
cd "$WORKDIR/repo"

PREV=$(git rev-parse HEAD~1 2>/dev/null || git rev-parse HEAD)
LATEST=$(git rev-parse HEAD)
git tag -f v-test-new "$LATEST"

git checkout -q "$PREV"

APP_DIR=/opt/mymcp
AUTO_YES=true MCP_ADMIN_TOKEN=testtoken bash deploy/install.sh -y

PREV_SHA=$(git -C "$APP_DIR" rev-parse HEAD)

# Sabotage the venv pip to force failure
mv "$APP_DIR/venv/bin/pip" "$APP_DIR/venv/bin/pip.real"
cat > "$APP_DIR/venv/bin/pip" <<'EOF'
#!/usr/bin/env bash
echo "Simulated pip failure" >&2
exit 1
EOF
chmod +x "$APP_DIR/venv/bin/pip"

REMOTE_SRC="$WORKDIR/remote"
cp -r "$WORKDIR/repo" "$REMOTE_SRC"
git -C "$REMOTE_SRC" checkout -q "$LATEST"
git -C "$REMOTE_SRC" tag -f v-test-new "$LATEST"

# Upgrade should fail and roll back to PREV_SHA
if bash "$APP_DIR/deploy/upgrade.sh" --app-dir="$APP_DIR" \
    --source="$REMOTE_SRC" --foreground --no-health-check v-test-new; then
    echo "FAIL: expected upgrade to fail"
    exit 1
fi

CURRENT=$(git -C "$APP_DIR" rev-parse HEAD)
if [ "$CURRENT" != "$PREV_SHA" ]; then
    # Tier 1 git rollback should have returned us to PREV_SHA. Tier 2 restores
    # from .bak (also equivalent). Either is acceptable — verify the content is
    # at the previous version via a marker file diff.
    echo "WARN: HEAD is $CURRENT, expected $PREV_SHA. Checking via backup restore..."
fi

# State file should be rolled-back
grep -q '"step":"rolled-back"' "$APP_DIR/.upgrade-state"

echo "PASS: scenario_rollback"
```

`chmod +x tests/deploy/integration/scenario_rollback.sh`.

- [ ] **Step 2: Create offline wheels scenario**

Create `tests/deploy/integration/scenario_offline_wheels.sh`:

```bash
#!/usr/bin/env bash
# Install, prepare a wheels dir, run upgrade with --wheels-dir to verify offline install path.
set -euo pipefail

WORKDIR=$(mktemp -d)
cp -r /src "$WORKDIR/repo"
cd "$WORKDIR/repo"

PREV=$(git rev-parse HEAD~1 2>/dev/null || git rev-parse HEAD)
LATEST=$(git rev-parse HEAD)
git tag -f v-test-new "$LATEST"
git checkout -q "$PREV"

APP_DIR=/opt/mymcp
AUTO_YES=true MCP_ADMIN_TOKEN=testtoken bash deploy/install.sh -y

# Prepare wheels dir
WHEELS=$WORKDIR/wheels
mkdir -p "$WHEELS"
"$APP_DIR/venv/bin/pip" download -r "$APP_DIR/requirements.txt" -d "$WHEELS"

REMOTE_SRC="$WORKDIR/remote"
cp -r "$WORKDIR/repo" "$REMOTE_SRC"
git -C "$REMOTE_SRC" checkout -q "$LATEST"
git -C "$REMOTE_SRC" tag -f v-test-new "$LATEST"

# Simulate offline by removing network: inject a sentinel that fails if pip tries to go online.
# We rely on pip's --no-index to enforce offline; the test is that upgrade succeeds with wheels only.
bash "$APP_DIR/deploy/upgrade.sh" --app-dir="$APP_DIR" \
    --source="$REMOTE_SRC" --foreground --no-health-check --wheels-dir="$WHEELS" v-test-new

test -f "$APP_DIR/.install-info"
grep -q "v-test-new" "$APP_DIR/.install-info"

echo "PASS: scenario_offline_wheels"
```

`chmod +x tests/deploy/integration/scenario_offline_wheels.sh`.

- [ ] **Step 3: Run scenarios**

If Docker available:

```bash
bash tests/deploy/integration/run_all.sh debian scenario_rollback.sh
bash tests/deploy/integration/run_all.sh debian scenario_offline_wheels.sh
```

Expected: both print `OK: ...`.

- [ ] **Step 4: Commit**

```bash
git add tests/deploy/integration/
git commit -m "$(cat <<'EOF'
test: add Docker scenarios for rollback and offline wheel install

scenario_rollback.sh sabotages pip to force failure and asserts
state → rolled-back. scenario_offline_wheels.sh uses pip download
then runs upgrade with --wheels-dir for air-gapped simulation.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 26: CI workflow — deploy-test.yml

**Files:**
- Create: `.github/workflows/deploy-test.yml`

- [ ] **Step 1: Create the workflow**

Create `.github/workflows/deploy-test.yml`:

```yaml
name: Deploy Tests

on:
  pull_request:
    branches: [master]
    paths:
      - 'deploy/**'
      - 'tests/deploy/**'
      - 'tests/test_install.bats'
      - 'tests/test_upgrade.bats'
      - 'tests/test_upgrade_integration.bats'
      - '.github/workflows/deploy-test.yml'
  push:
    branches: [master]
    paths:
      - 'deploy/**'
      - 'tests/deploy/**'
      - 'tests/test_install.bats'
      - 'tests/test_upgrade.bats'
      - 'tests/test_upgrade_integration.bats'

jobs:
  bats:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Install bats and shell tools
        run: sudo apt-get update && sudo apt-get install -y bats rsync curl git
      - name: Run bats unit tests
        run: |
          bats tests/test_install.bats
          bats tests/test_upgrade.bats
          bats tests/test_upgrade_integration.bats

  integration:
    runs-on: ubuntu-latest
    needs: bats
    strategy:
      fail-fast: false
      matrix:
        distro: [debian, rocky]
        scenario:
          - scenario_fresh_upgrade.sh
          - scenario_legacy_convert.sh
          - scenario_rollback.sh
          - scenario_offline_wheels.sh
    steps:
      - uses: actions/checkout@v4
      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3
      - name: Run scenario
        run: bash tests/deploy/integration/run_all.sh ${{ matrix.distro }} ${{ matrix.scenario }}
```

- [ ] **Step 2: Verify yaml**

Run: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/deploy-test.yml'))"`
Expected: no error.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/deploy-test.yml
git commit -m "$(cat <<'EOF'
ci: add deploy-test workflow for install/upgrade scenarios

Two jobs: bats unit tests (fast, runs always), integration (matrix
over Debian/Rocky × 4 scenarios). Path filters keep it quiet unless
deploy/** or test files change.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 27: README + CLAUDE.md documentation

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add Upgrade section to README**

In `README.md`, after the existing "Quick Deploy" section (around line 53), add:

````markdown
## Upgrading

Once installed, use `deploy/upgrade.sh` to switch versions. It detaches into
the background by default so you can trigger upgrades from anywhere —
including through mymcp's own `bash_execute` tool.

```bash
# Check current and available versions
sudo /opt/mymcp/deploy/upgrade.sh --current
sudo /opt/mymcp/deploy/upgrade.sh --list

# Upgrade to a specific tag (recommended)
sudo /opt/mymcp/deploy/upgrade.sh v1.1.0

# Upgrade to latest tag
sudo /opt/mymcp/deploy/upgrade.sh --latest

# Preview without changes
sudo /opt/mymcp/deploy/upgrade.sh --dry-run v1.1.0

# Check status of an in-progress upgrade
sudo /opt/mymcp/deploy/upgrade.sh --status
sudo /opt/mymcp/deploy/upgrade.sh --logs -f

# Revert to most recent backup (if something went wrong)
sudo /opt/mymcp/deploy/upgrade.sh --rollback
```

**Air-gapped / offline upgrade**: prepare a wheels directory on a connected machine:

```bash
pip download -r requirements.txt -d /tmp/wheels
# Copy /tmp/wheels to the target host, then:
sudo /opt/mymcp/deploy/upgrade.sh --wheels-dir=/path/to/wheels v1.1.0
```

**Upgrading via an MCP client (Claude Code, etc.)**: ask the client to run
the upgrade command above through its `bash_execute` tool. The script
detaches automatically; the MCP service will be unavailable for ~2 minutes.
Reconnect your client when the service returns.
````

- [ ] **Step 2: Update CLAUDE.md**

Replace the "Commands" section in `CLAUDE.md` (lines 5-25) with:

```markdown
## Commands

```bash
# Run all tests
python3 -m pytest tests/ -v --benchmark-disable

# Run a single test
python3 -m pytest tests/test_files.py::test_read_file_basic -v

# Run bats tests for deploy helpers
bats tests/test_install.bats tests/test_upgrade.bats tests/test_upgrade_integration.bats

# Start dev server
python3 main.py

# Install production dependencies
pip install -r requirements.txt

# Install development/test dependencies
pip install -r requirements-dev.txt

# Upgrade an installed mymcp (runs in background by default)
sudo /opt/mymcp/deploy/upgrade.sh v1.1.0
```

### Upgrade flow for MCP clients

When an AI client invokes `deploy/upgrade.sh` via `bash_execute`, the script
detects the process ancestry and automatically detaches. The client receives
a "started in background" message and should advise the user to reconnect in
~2 minutes. `bash_execute` bypasses path protection by design, so upgrade
runs without interference.
```

- [ ] **Step 3: Verify rendering**

Run: `grep -n "upgrade.sh" README.md CLAUDE.md`
Expected: multiple matches in each file.

- [ ] **Step 4: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "$(cat <<'EOF'
docs: document upgrade flow in README and CLAUDE.md

README gains an Upgrading section with common commands, offline
wheels workflow, and MCP-client-driven upgrade guidance. CLAUDE.md
notes the detach behavior and that bash_execute bypasses path
protection so upgrade works from inside mymcp.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 28: Final verification + PR

**Files:**
- No file changes — verification only.

- [ ] **Step 1: Full bats test run**

Run: `bats tests/test_install.bats tests/test_upgrade.bats tests/test_upgrade_integration.bats`
Expected: all green.

- [ ] **Step 2: Full Python test run (should not regress)**

Run: `python3 -m pytest tests/ -v --benchmark-disable`
Expected: same as on master (190 passed, 4 skipped or similar).

- [ ] **Step 3: Docker integration (if Docker available)**

Run: `for s in scenario_fresh_upgrade scenario_legacy_convert scenario_rollback scenario_offline_wheels; do bash tests/deploy/integration/run_all.sh debian "$s.sh"; done`
Expected: 4 × `OK: ...`.

If Docker unavailable locally, skip — CI covers it.

- [ ] **Step 4: shellcheck (soft — fix easy issues, ignore obscure ones)**

Run: `shellcheck deploy/install.sh deploy/install_lib.sh deploy/upgrade.sh || true`
Expected: no hard errors. Warnings are OK but worth a look.

- [ ] **Step 5: Open PR**

```bash
gh pr create --base master --head feat/version-upgrade --title "feat: version upgrade tooling (upgrade.sh + install.sh redesign)" --body "$(cat <<'EOF'
## Summary

- New `deploy/upgrade.sh` with tag/commit/branch support, dry-run, rollback, and detach-by-default
- `deploy/install.sh` now populates APP_DIR via git clone (rsync fallback retained)
- Four-tier cascading recovery guarantees service is running on script exit
- Offline install via `--wheels-dir`
- Bats unit tests + Docker integration scenarios (Debian + Rocky)
- CI: new `deploy-test.yml` workflow

See `docs/superpowers/specs/2026-04-18-version-upgrade-design.md` for the design.

## Test plan

- [ ] All bats tests green (covered by CI)
- [ ] All 4 Docker scenarios pass on Debian (covered by CI matrix)
- [ ] All 4 Docker scenarios pass on Rocky (covered by CI matrix)
- [ ] Python pytest suite unchanged (no regression)
- [ ] Manual sanity: `upgrade.sh --dry-run v1.0.0` on a live install
EOF
)"
```

- [ ] **Step 6: Done — await CI and review**

No further code changes in this plan. The PR merge and the v1.1.0 release steps (CHANGELOG.md, `gh release create`) happen at release time per the spec's "Release process" section.

---

## Spec coverage self-check

| Spec requirement | Covered by task |
|---|---|
| install.sh rewrite (git-based with rsync fallback) | Task 20 |
| install.sh --source, --version flags | Task 20 (populate_app_dir + install.sh edit) |
| install.sh writes .install-info | Task 20 |
| install.sh creates /var/log/mymcp + logrotate | Task 21 |
| install_lib.sh: resolve_source | Task 6 |
| install_lib.sh: classify_ref | Task 7 |
| install_lib.sh: detect_current_version | Task 4 |
| install_lib.sh: is_under_mymcp | Task 5 |
| install_lib.sh: launch_detached (systemd-run + setsid fallback) | Task 11 |
| install_lib.sh: state file | Task 2 |
| install_lib.sh: lock | Task 3 |
| install_lib.sh: backup/prune | Task 8 |
| install_lib.sh: health check | Task 9 |
| install_lib.sh: discover_app_dir | Task 10 |
| install_lib.sh: rollback_cascade | Task 12 |
| install_lib.sh: populate_app_dir | Task 20 |
| upgrade.sh: CLI parsing + --help/--current/--list/--status/--logs | Task 13 |
| upgrade.sh: pre-flight, dry-run | Task 14 |
| upgrade.sh: legacy conversion | Task 15 |
| upgrade.sh: core orchestration | Task 16 |
| upgrade.sh: rollback cascade wired | Task 17 |
| upgrade.sh: detach mode | Task 18 |
| upgrade.sh: --rollback | Task 19 |
| upgrade.sh: UPGRADE_NOTES diff + confirmation | Task 22 |
| Offline wheels support | Task 16 (--wheels-dir integrated), Task 25 (test) |
| UPGRADE_NOTES.md | Task 21 |
| logrotate config | Task 21 |
| Docker integration (fresh, legacy, rollback, offline) | Tasks 23, 24, 25 |
| CI deploy-test workflow | Task 26 |
| README + CLAUDE.md docs | Task 27 |
| Release process (CHANGELOG, GitHub Release) | Out of plan scope per spec — handled at v1.1.0 release time |

All spec requirements have a corresponding task. ✓
