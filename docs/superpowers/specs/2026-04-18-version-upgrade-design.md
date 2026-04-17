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

Triggers:
- Automatic on step 6–9 failure.
- Manual via `upgrade.sh --rollback` (uses most recent backup).

Rollback steps:
1. `systemctl stop mymcp`
2. `git -C $APP_DIR checkout <backup.previous_sha>` (recorded in backup metadata)
3. `venv/bin/pip install -r requirements.txt` (revert deps)
4. `systemctl start mymcp`
5. Health check again; if still fails, print manual recovery instructions and exit nonzero.

Rationale for git-based rollback (vs. restoring from `.bak` directory): git rollback is atomic for code and preserves state files untouched. The `.bak` directory is a safety net in case git-level rollback fails (e.g., disk corruption). We keep both.

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

- **Modify:** `deploy/install.sh` — switch from `rsync` to git-based population; add `--source`, `--version` flags; write `.install-info`.
- **Modify:** `deploy/install_lib.sh` — add helper functions for version resolution, source detection.
- **Create:** `deploy/upgrade.sh` — new script, main logic.
- **Create:** `deploy/UPGRADE_NOTES.md` — breaking-change log (starts with v1.0.0 as "initial release, no notes").
- **Create:** `tests/deploy/` — bash test harness with mocked tools and Docker scenarios.
- **Create:** `.github/workflows/deploy-test.yml` — CI for upgrade scenarios.
- **Modify:** `README.md` — add Upgrade section with common commands.
- **Modify:** `CLAUDE.md` — document upgrade.sh location and intent.

## Open questions (for reviewer)

1. Should `install.sh` still support pure-rsync mode (for users who explicitly don't want `.git` in `APP_DIR`)? Current design says yes as fallback-of-last-resort; alternative is to hard-require a git source.
2. Backup retention default of 3 — appropriate? Each backup is ~10 MB without venv.
3. `--allow-branch` naming — also considered `--unsafe-ref`, `--dev-mode`. Happy to take suggestions.
4. Offline wheel support in v1.1 or defer to v1.2? Adds complexity to upgrade.sh; not blocking for most users.

---

**Next step after approval:** invoke `superpowers:writing-plans` to produce the task-by-task implementation plan.
