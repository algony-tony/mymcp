# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/algony-tony/mymcp/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/algony-tony/mymcp/releases/tag/v1.0.0
