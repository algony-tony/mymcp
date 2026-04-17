# Version Upgrade Design

**Date:** 2026-04-18
**Status:** Draft — pending review
**Goal:** Enable users to upgrade an installed MyMCP server to the latest or a specified version safely, with support for offline environments and legacy installs.

## Background

v1.0.0 ships with `deploy/install.sh` only. It uses `rsync` to copy files from a cloned repo (`REPO_DIR`) into an install directory (`APP_DIR`, default `/opt/mymcp`). There is no upgrade path — users must re-run `install.sh` from an updated clone, and there is no version tracking, rollback, or pre-flight validation.

Since the project has no database, upgrade concerns are limited to: code, Python dependencies, configuration (`MCP_*` env vars), and two stateful files (`.env`, `tokens.json`). Audit logs live in a separate directory and are not touched by upgrade.

## Goals & Non-Goals

**In scope:**
- Target any git ref: tag (recommended), commit SHA (allowed with warning), branch (requires explicit flag).
- Three-tier source fallback: local git → GitHub remote → local rsync (offline tarball).
- Offline / air-gapped dependency install via local wheel directory.
- Pre-flight checks: Python version, env var compatibility, disk space.
- Atomic-ish upgrade: stop service → backup → upgrade → install deps → start → health check → rollback on failure.
- Version introspection (`--current`, `--list`) and dry-run.
- Automatic conversion of legacy rsync-based installs into git-managed layout on first upgrade.

**Out of scope (this release):**
- GPG signature verification of tags.
- Automatic notification of available upgrades.
- Cross-host orchestration for distributed deployments (each host upgrades independently).
- Config format migration tooling (breaking env var changes are communicated via `UPGRADE_NOTES.md`, applied manually).

## Architecture

### Layout model

After this change, `APP_DIR` is always a git working tree:

```
/opt/mymcp/
├── .git/              ← added (tracks installed version)
├── main.py
├── mcp_server.py
├── tools/
├── requirements.txt
├── deploy/
├── venv/              ← not in git (gitignored at runtime; .git tracks sources only)
├── .env               ← not in git (state, preserved across upgrades)
├── tokens.json        ← not in git (state, preserved across upgrades)
```

The current `.gitignore` already excludes `.env`, `tokens.json`, and `venv/`, so `git checkout` of a new ref never touches state files.

### Source-of-truth fallback chain

Both `install.sh` and `upgrade.sh` resolve "where do I get version X's code?" through this chain:

1. **`--source=<path-or-url>`** — user-specified (highest priority)
2. **`$REPO_DIR/.git` exists** — the directory the script runs from is a git clone; use it as source
3. **GitHub remote** (`https://github.com/algony-tony/mymcp.git`) — fetch from origin
4. **`$REPO_DIR` without `.git`** — treat as an extracted tarball; rsync files only (no version history, degraded upgrade experience for next time)

Rationale: a user who uploaded a git repo to an internal server probably wants *that* code, not fresh-from-GitHub. Explicit `--prefer-remote` flips the priority for users who want the opposite.

**Note:** Rule 4 (rsync fallback) applies only to `install.sh`. `upgrade.sh` never rsyncs; if `APP_DIR` is not yet a git tree, upgrade first converts it (see "Convert legacy install" in upgrade workflow).

### Version identifier handling

| Ref type     | Command               | Behavior                                    |
|--------------|-----------------------|---------------------------------------------|
| Tag          | `upgrade.sh v1.1.0`   | Preferred. No extra flags.                  |
| Commit SHA   | `upgrade.sh abc1234`  | Allowed. Prints warning "not a tagged release". |
| Branch       | `upgrade.sh main`     | Rejected unless `--allow-branch` given.     |
| Latest tag   | `upgrade.sh --latest` | Resolves to `git tag -l 'v*' \| sort -V \| tail -1`. |
| (none)       | `upgrade.sh`          | Prints current version + recent tags; no action. |

Version displayed via `git -C $APP_DIR describe --tags --always` — gives `v1.1.0` for tags, `v1.0.0-3-gabc1234` for post-tag commits, raw SHA when no tags reachable.

### install.sh redesign

Current: `rsync REPO_DIR → APP_DIR`.

New flow:

1. Prompt for `APP_DIR` (unchanged).
2. Prompt for `PYTHON` (unchanged).
3. Prompt for ripgrep (unchanged).
4. **Resolve source** via fallback chain. Determine target version:
   - If `REPO_DIR` is git tree: use `git describe --tags --exact-match` or current HEAD.
   - If user passed `--version=v1.2.0`: use that.
   - Default from GitHub: latest tag.
5. **Populate `APP_DIR`**:
   - If source is a local git tree and `REPO_DIR == APP_DIR`: no-op.
   - If source is a local git tree and `REPO_DIR != APP_DIR`: `git clone --local $REPO_DIR $APP_DIR && cd $APP_DIR && git checkout $TARGET_VERSION`.
   - If source is GitHub: `git clone --branch $TARGET_VERSION $REPO_URL $APP_DIR`.
   - If source is non-git tarball: `rsync` as today (degraded; logs a warning that future upgrades will need to convert).
6. venv, `.env`, systemd — unchanged from current install.sh.

### upgrade.sh design

#### CLI surface

```
Usage: upgrade.sh [VERSION] [OPTIONS]

VERSION:
  v1.1.0              Target tag (recommended)
  --latest            Latest tag available
  <commit-sha>        Specific commit (requires confirmation)
  <branch>            Branch tip (requires --allow-branch)

Options:
  --app-dir=PATH      Override install dir (default: auto-detect from systemd)
  --source=URL|PATH   Explicit source (URL or local git path)
  --prefer-remote     Try GitHub before local paths
  --wheels-dir=PATH   Offline pip install from local wheel directory
  --keep-backups=N    Backup retention (default: 3)
  --dry-run           Print plan, don't execute
  --allow-branch      Permit branch checkout (dangerous)
  --no-health-check   Skip post-start /health probe
  --rollback          Revert to last backup (see Rollback section)
  --current           Print current version and exit
  --list              Print available tags and exit
  --status            Print status of an in-progress upgrade (see Self-upgrade)
  --logs [-f]         Print recent upgrade logs (with -f: follow)
  --foreground        Run synchronously (default: detach, see Self-upgrade)
  --no-detach         Alias for --foreground
  -h, --help          Show help
```

#### APP_DIR discovery

Order:
1. `--app-dir=PATH` flag
2. `/etc/systemd/system/mymcp.service` → parse `WorkingDirectory=`
3. Default `/opt/mymcp` (warn if missing)

#### Workflow

```
┌─ 1. Parse args, discover APP_DIR, resolve TARGET_VERSION
│
├─ 2. Pre-flight (fail fast, non-destructive)
│    ├─ APP_DIR exists and looks like mymcp install
│    ├─ Current version detected → CURRENT_VERSION
│    ├─ TARGET_VERSION ≠ CURRENT_VERSION (warn + confirm if same)
│    ├─ Python in venv meets new MIN_PYTHON_MINOR requirement
│    ├─ Disk space for backup + new deps
│    ├─ Print UPGRADE_NOTES.md diff CURRENT..TARGET if any, ask confirmation
│    └─ --dry-run exits here after printing plan
│
├─ 3. Convert legacy install if $APP_DIR/.git missing
│    ├─ git init; git remote add origin <resolved-source>
│    ├─ git fetch --tags
│    ├─ git reset --hard $TARGET_VERSION  (directly to target, skip interim)
│    └─ .env, tokens.json are untracked → preserved
│
├─ 4. Backup
│    ├─ $APP_DIR.bak-$(date +%Y%m%d-%H%M%S)/ → full copy (excluding venv/, .git/)
│    ├─ Record CURRENT_VERSION in backup metadata file
│    └─ Prune older backups per --keep-backups
│
├─ 5. Stop service
│    └─ systemctl stop mymcp
│
├─ 6. Apply upgrade
│    ├─ cd $APP_DIR && git fetch --tags
│    ├─ git checkout $TARGET_VERSION
│    ├─ diff requirements.txt vs previous → log what changed
│    └─ venv/bin/pip install [--no-index --find-links=$WHEELS_DIR] -r requirements.txt
│
├─ 7. systemd unit refresh
│    ├─ Re-render mymcp.service template if deploy/mymcp.service changed
│    └─ systemctl daemon-reload if unit file changed
│
├─ 8. Start service
│    └─ systemctl start mymcp
│
├─ 9. Health check
│    ├─ Poll GET http://$MCP_HOST:$MCP_PORT/health for up to 30s
│    └─ Expect 200 {"status":"ok"}
│
└─ 10. Success or rollback
     ├─ Success: print new version, backup path, done
     └─ Failure (any step 6–9): trigger rollback (below)
```

#### Rollback

**Invariant: the service is running when the script exits.** This is a hard guarantee. Whether the upgrade succeeded, the rollback succeeded, or the rollback itself failed, the script's final act is to ensure `systemctl is-active mymcp` returns active (or to clearly tell the user that automated recovery is impossible and the service is down).

This matters especially for MCP-client-driven upgrades: the AI client detached and disconnected; if an error leaves the service down silently, the user only discovers it when they try to reconnect. So we structure the script with an `EXIT` trap that performs a "last-resort start" regardless of how execution reached the end.

Triggers for rollback:
- Automatic on step 6–9 failure.
- Manual via `upgrade.sh --rollback` (uses most recent backup).

Rollback steps (happy path):
1. `systemctl stop mymcp` (idempotent; no-op if already stopped)
2. `git -C $APP_DIR checkout <backup.previous_sha>` (recorded in backup metadata)
3. `venv/bin/pip install -r requirements.txt` (revert deps)
4. `systemctl start mymcp`
5. Health check; log success

**Cascading recovery** — each later tier runs only if the previous one failed:

```
Tier 1: git rollback + pip revert + start    (happy path above)
  │ fail
  ▼
Tier 2: restore from .bak directory + start
  │ (rsync the most-recent $APP_DIR.bak-* back over APP_DIR, excluding .env/tokens.json
  │  which were preserved in place; then systemctl start)
  │ fail
  ▼
Tier 3: force-start whatever code is currently in APP_DIR
  │ (systemctl start mymcp even if we don't know which version — better a broken
  │  new version answering /health than a silent outage)
  │ fail
  ▼
Tier 4: report to user
  (Print: "Upgrade FAILED and auto-recovery FAILED. Service is stopped.
   Manual recovery required. Backup at $APP_DIR.bak-*. Logs: $LOG_FILE."
   Exit non-zero. State file marked 'failed-manual-intervention'.)
```

The final-tier `systemctl start` runs unconditionally in an `EXIT` trap. Even if the script itself crashes with an unhandled error, the trap fires and tries to start the service.

Rationale for git-based rollback as tier 1 (vs. restoring from `.bak` first): git rollback is faster and preserves state files cleanly. The `.bak` directory is tier 2 safety net in case git-level operations fail (corrupt `.git`, missing remote, disk pressure mid-checkout). Tier 3 handles cases where both tiers failed but the service might still boot. Tier 4 is the escape hatch — we never silently leave the service down.

**State-file transitions during failure:**
- Upgrade failure: state → `rolling-back` (tier 1) → `rolling-back-from-backup` (tier 2) → `force-starting` (tier 3) → `failed-manual-intervention` (tier 4) or `rolled-back` (any tier that recovers the service).
- The state file always reflects the current attempt, so `--status` from the client shows the truth.

### Self-upgrade scenario (MCP client triggering upgrade via `bash_execute`)

**Problem.** A common usage is an AI client (Claude Code etc.) connected to mymcp, being asked to upgrade mymcp itself. The client calls `bash_execute` to run `upgrade.sh`. Two issues arise:

1. **Process tree kill**. `bash_execute` runs as a subprocess of the uvicorn mymcp process. Step 5 of the workflow (`systemctl stop mymcp`) kills uvicorn, which kills all its descendants — including `upgrade.sh` itself. The upgrade dies mid-way, leaving the service down and code half-updated.
2. **Script-file rewrite during self-execution**. Bash reads a script as it executes. If `git checkout v1.1.0` overwrites `deploy/upgrade.sh` with a new version while the script is still running, behavior is undefined (bash may read garbage, mis-seek, etc.).
3. **Blocking client**. Even ignoring the above, a 2–3 minute synchronous `bash_execute` call is a poor UX; the HTTP client likely times out.

**Write-protection impact.** None. `bash_execute` bypasses `check_protected_path` by design. `git checkout`, `rsync`, `pip`, `systemctl` all run as shell commands, not through MCP file tools. Write protection only blocks `read_file`/`write_file`/`edit_file`/`glob`/`grep` against protected paths — upgrade doesn't use those.

#### Design: detach by default

The upgrade script detaches itself on startup, unless `--foreground` is given:

```
upgrade.sh entry
  ├─ (a) Parse args, run pre-flight (synchronous, fast — returns errors to caller)
  ├─ (b) If --status / --logs / --current / --list / --dry-run / --rollback: execute synchronously and exit
  ├─ (c) Self-copy: cp $0 /tmp/mymcp-upgrade-$$.sh (defends against script rewrite at git checkout)
  ├─ (d) Detach: spawn a background runner via one of the mechanisms below, redirect stdout+stderr to log file
  ├─ (e) Print to caller:
  │       "Upgrade v1.0.0 → v1.1.0 started in background (PID NNNN, unit mymcp-upgrade).
  │        Service will be unavailable for ~2 minutes.
  │        Check status:  sudo /opt/mymcp/deploy/upgrade.sh --status
  │        Follow logs:   sudo /opt/mymcp/deploy/upgrade.sh --logs -f
  │        Reconnect your MCP client after the service returns to healthy state."
  └─ (f) Exit 0 immediately — the detached runner continues independently
```

The AI client receives step (e)'s message and can advise the human user to reconnect after a few minutes.

#### Detach mechanism (cross-distro)

Primary: `systemd-run` (transient scope, journal integration).
Fallback: `setsid nohup bash ... &>log &` + `disown` (pure POSIX).

Order of preference per distro:

```
if command -v systemd-run >/dev/null && systemctl is-system-running &>/dev/null; then
    systemd-run --unit=mymcp-upgrade --property=After=network.target \
        --no-block --quiet \
        /tmp/mymcp-upgrade-$$.sh --detach-runner "$@"
else
    setsid nohup /tmp/mymcp-upgrade-$$.sh --detach-runner "$@" \
        >>/var/log/mymcp/upgrade.log 2>&1 </dev/null &
    disown
fi
```

**Target distros and compatibility:**

| Distro | Version | systemd-run | Notes |
|---|---|---|---|
| RHEL / CentOS / Rocky / AlmaLinux | 7, 8, 9 | ✓ | systemd since 7 |
| Fedora | all modern | ✓ | |
| Debian | 10 (buster) + | ✓ | |
| Ubuntu | 18.04 + | ✓ | LTS releases |
| openSUSE / SLES | 15+ | ✓ | |
| Arch / Manjaro | rolling | ✓ | |
| Alpine | all | — | Uses OpenRC; fallback path (`setsid nohup`) covers it. Not a primary target. |

The fallback (`setsid nohup ... & disown`) works on any POSIX shell environment, so distros without systemd (Alpine, containers without PID 1 = systemd) still function — they just lose journal integration.

#### Detection: "am I being called from inside mymcp?"

If the upgrade script's ancestor process tree includes the mymcp uvicorn worker, forced-foreground (`--foreground`) is rejected with an explanation. Check:

```bash
is_under_mymcp() {
    local pid=$PPID
    while [ "$pid" -gt 1 ]; do
        local cmd
        cmd=$(cat "/proc/$pid/cmdline" 2>/dev/null | tr '\0' ' ')
        if [[ "$cmd" == *"uvicorn"*"main:app"* ]] || [[ "$cmd" == *"/opt/mymcp/venv"* ]]; then
            return 0
        fi
        pid=$(awk '{print $4}' "/proc/$pid/stat" 2>/dev/null)
        [ -z "$pid" ] && break
    done
    return 1
}
```

When `is_under_mymcp && --foreground`: print error and exit. When `is_under_mymcp && !--foreground`: proceed with detach (expected path).

#### State & logs

- **State file:** `$APP_DIR/.upgrade-state` (JSON, single line for atomic write via rename):
  ```json
  {"pid": 12345, "from": "v1.0.0", "to": "v1.1.0", "step": "installing-deps",
   "started_at": "2026-04-18T12:00:00Z", "updated_at": "2026-04-18T12:01:30Z"}
  ```
  Happy-path steps: `preflight` → `backup` → `stopping-service` → `checking-out-code` → `installing-deps` → `refreshing-unit` → `starting-service` → `health-check` → `done`.
  Failure-path steps: `rolling-back` → `rolling-back-from-backup` → `force-starting` → `rolled-back` | `failed-manual-intervention`.

- **Log file:** `/var/log/mymcp/upgrade.log` (single file, rotated via `logrotate` snippet installed by install.sh; or rely on `journalctl -u mymcp-upgrade` when systemd-run is used).

- **`--status`**: prints the state file plus last 10 log lines.
- **`--logs`**: `tail -n 200` of log file; `-f` for follow.

#### Concurrency & safety

- `$APP_DIR/.upgrade.lock` acquired via `flock` — refuses concurrent invocations.
- Stale lock detection: if lockholder PID is dead (`kill -0 $PID` fails), auto-clean.
- The self-copy in `/tmp/mymcp-upgrade-$$.sh` is cleaned up on the detached runner's `trap EXIT`.

### Version metadata

Two sources of truth:

1. **`git describe --tags --always`** on `$APP_DIR/.git` — primary, always accurate for git-managed installs.
2. **`$APP_DIR/.install-info`** — JSON file, written by install/upgrade:
   ```json
   {"version": "v1.1.0", "installed_at": "2026-04-18T10:22:00Z", "upgraded_from": "v1.0.0"}
   ```
   Used when `.git` is missing (legacy installs) and for audit. Not a replacement for git metadata.

### Breaking change communication

`deploy/UPGRADE_NOTES.md` in the repo lists breaking changes per version:

```markdown
## v1.1.0 (2026-05-01)

### Breaking
- `MCP_TOKEN_FILE` renamed to `MCP_TOKEN_STORE`. Update your .env.

### Notes
- New required dependency: redis>=5.0 (...)
```

Upgrade script:
1. `git log $CURRENT..$TARGET -- deploy/UPGRADE_NOTES.md` to check if file changed.
2. If yes: print the diff, require interactive confirmation (or `--yes` flag).

This is low-tech but reliable — migration complexity lives in prose, not scripts.

### Offline / air-gapped support

Two orthogonal concerns:

1. **Source code**: handled by fallback chain (step 4 of install, step 3 of upgrade can consume local git tree).
2. **Python dependencies**: `--wheels-dir=/path/to/wheels` flag. User on a connected machine runs `pip download -r requirements.txt -d wheels/` and uploads. Upgrade invokes `pip install --no-index --find-links=$WHEELS_DIR ...`.

Documented in README with copy-paste commands.

Not handling:
- ripgrep binary in air-gapped env — existing install.sh already downloads from GitHub; for offline users, document "install ripgrep via OS package manager in advance, or accept the Python regex fallback".
- Python interpreter itself — user's problem, as today.

## Edge cases

| Situation | Handling |
|---|---|
| Upgrade to same version | Warn, require `--force` to proceed (re-runs dep install) |
| Downgrade (older tag) | Allowed, warn "downgrading from X to Y" |
| `APP_DIR` is dirty git tree (local modifications) | Refuse to proceed unless `--force-discard-local-changes` |
| `APP_DIR/.git` exists but has different remote | Warn; `--source` flag overrides |
| venv's Python no longer meets minimum | Prompt to recreate venv (user confirms) |
| `.env` missing | Treat as misconfigured, abort with guidance |
| systemd unit file structure change | Diff old vs new; if changed, regenerate |
| Concurrent upgrade invocations | File lock in `$APP_DIR/.upgrade.lock` |
| `.git` exists but corrupt | Fall back to manual: print instructions, do not auto-recover |
| Service stopped before upgrade starts | Skip step 5, flag as unusual, continue |
| Health check fails but service is running | Rollback regardless; a running-but-unhealthy service is not "upgraded" |
| Wheels dir missing expected package | Fail fast with "missing <pkg> in wheels dir" |
| Invoked from mymcp's own `bash_execute` | Auto-detect via process ancestry; force detach; reject `--foreground` with clear message |
| systemd-run unavailable (Alpine, minimal container) | Fall back to `setsid nohup ... & disown` automatically |
| Detached runner fails before writing first state | Parent prints generic "failed to start" message with log path for post-mortem |
| State file says `rolling-back` but rollback process died | `--status` flags as stuck; user runs `--rollback` manually or restores from `.bak` |
| Log file hits rotation during upgrade | Runner writes to a specific timestamped file (`upgrade-YYYYMMDD-HHMMSS.log`) and updates a `current.log` symlink to avoid mid-flight rotation |
| Rollback itself fails (git corrupt, pip network error, etc.) | Cascading recovery: git rollback → backup dir restore → force-start current code → manual-intervention report. EXIT trap ensures a final start attempt no matter how the script terminates. |
| Script crashes unexpectedly (kill -9, OOM, etc.) | `EXIT` trap runs `systemctl start mymcp` unconditionally as last resort. Incomplete upgrade state is visible via `--status` for post-mortem. |

## Testing strategy

- **Unit-ish**: bash test harness with mocked `git`, `systemctl`, `curl`. Verify argument parsing, fallback order, error paths.
- **Integration**: Docker-based scenarios:
  - Fresh install → upgrade to newer tag
  - Legacy (rsync) install → upgrade (exercises conversion path)
  - Upgrade to non-tag commit
  - Upgrade with `--wheels-dir` offline simulation
  - Upgrade failure triggering rollback
  - `--dry-run` outputs match actual execution plan
- **Manual pre-release**: run upgrade on a live dev install before cutting each release.

CI addition: a `deploy-test` workflow that runs the Docker scenarios on master pushes. Separate from the existing test/mutation jobs.

## Files & Changes

- **Modify:** `deploy/install.sh` — keep rsync path as explicit fallback; add git-based population; add `--source`, `--version` flags; write `.install-info`; create `/var/log/mymcp/` and install logrotate snippet.
- **Modify:** `deploy/install_lib.sh` — add helpers for version resolution, source detection, `is_under_mymcp` process-ancestry check, detach launcher with systemd-run + setsid fallback.
- **Create:** `deploy/upgrade.sh` — new script, main logic plus detach entry point.
- **Create:** `deploy/UPGRADE_NOTES.md` — breaking-change log (starts with v1.0.0 as "initial release, no notes").
- **Create:** `deploy/logrotate.mymcp-upgrade` — rotation config for upgrade logs (installed to `/etc/logrotate.d/` by install.sh).
- **Create:** `tests/deploy/` — bash test harness with mocked tools and Docker scenarios (RHEL-family + Debian-family images).
- **Create:** `.github/workflows/deploy-test.yml` — CI for upgrade scenarios including detach verification.
- **Modify:** `README.md` — add Upgrade section with common commands (including the "upgrade via MCP client" flow).
- **Modify:** `CLAUDE.md` — document upgrade.sh location, detach model, and that `bash_execute`-driven upgrade is the expected path for AI clients.

## Resolved design decisions

1. **Pure-rsync install mode:** kept as explicit last-resort fallback. `upgrade.sh` converts such installs to git-managed on first run.
2. **Backup retention:** default 3, `--keep-backups=N` overrides.
3. **Branch checkout naming:** `--allow-branch`.
4. **Offline wheel support:** in-scope for v1.1 (this release).
5. **Detach behavior:** default detach (safe for MCP-driven upgrade); `--foreground` opt-in for interactive SSH debug.
6. **Detach mechanism:** prefer `systemd-run`, fall back to `setsid nohup & disown`; covers RHEL/CentOS/Rocky/Alma 7+, Debian 10+, Ubuntu 18.04+, openSUSE 15+, Arch, Fedora, and non-systemd (Alpine) via fallback.

---

**Next step after approval:** invoke `superpowers:writing-plans` to produce the task-by-task implementation plan.
