# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.1.1] - 2026-04-20

### Fixed
- `upgrade.sh`: detached runner now survives legacy (rsync-mode) install
  conversion. Two bugs were silently causing the post-conversion service
  restart to never run, leaving the old in-memory process alive even
  though the disk had advanced to the target version.
  ([#3](https://github.com/algony-tony/mymcp/pull/3))
  - The self-copy to `/tmp` only copied `upgrade.sh`, not `install_lib.sh`,
    so the detached child died on its `source` line before parsing args.
    Both files are now copied into a per-invocation `mktemp -d` directory.
  - Legacy conversion in the parent pre-advanced the disk to the target,
    so the detached runner re-detected `CURRENT == TARGET` and exited at
    the same-version guard without running stop/install/start. The
    parent now propagates `--force` through `DETACH_ARGS` after a legacy
    conversion.

### Changed
- Release source archives now exclude dev-only paths (`tests/`, `docs/`,
  `.github/`, `CLAUDE.md`, `pytest.ini`, `requirements-dev.txt`) via
  `.gitattributes` `export-ignore`. The auto-generated "Source code
  (tar.gz)" asset shrank from ~146 KB to ~32 KB. Forks/contributors
  still get the full repo via `git clone`.
  ([#4](https://github.com/algony-tony/mymcp/pull/4))

## [1.1.0] - 2026-04-19

### Added
- `deploy/upgrade.sh`: end-to-end upgrade orchestration with pre-flight checks,
  `--dry-run`, `--rollback`, `--wheels-dir` offline install, and `--foreground`
  / background detach modes.
- Four-tier cascading rollback: git reset, backup restore, emergency snapshot,
  and manual recovery instructions written to `.upgrade-state`.
- Upgrade lock via `flock` with stale-lock cleanup; atomic `.upgrade-state`
  writes for safe concurrent observation.
- `install.sh` now populates `APP_DIR` via `git clone` when a working tree is
  available, falling back to `rsync` for tarball installs. Legacy rsync-based
  installs are auto-converted to git-managed on first upgrade.
- Pre-flight diff of `UPGRADE_NOTES.md` between current and target refs, plus
  log rotation for `/var/log/mymcp/upgrade.log`.
- Process-ancestry detection: upgrades invoked under the running mymcp server
  detach into the background via `systemd-run` (with `setsid` fallback) so the
  caller disconnects cleanly.
- CI: `deploy-test` workflow running bats unit tests and Docker integration
  scenarios (fresh upgrade, legacy convert, rollback, offline wheels) across
  Debian and Rocky images.

### Changed
- `install.sh` no longer copies the entire source tree; git clone is the
  primary path and the install metadata records `"mode":"git"` vs `"rsync"`.
- Upgrade refuses `--foreground` when invoked from inside the running mymcp
  process tree on a legacy install that needs git conversion (prevents the
  upgrade from killing its own parent mid-conversion).

### Fixed
- Pre-conversion guard in `upgrade.sh` correctly rejects `--foreground` before
  any filesystem changes are made.

## [1.0.0] - 2026-04-16

Initial tagged release. See git history for details.

[Unreleased]: https://github.com/algony-tony/mymcp/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/algony-tony/mymcp/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/algony-tony/mymcp/releases/tag/v1.0.0
