# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- (Go core M1) Go core M1 (read-only): `go/` module serving MCP over Streamable
  HTTP with token auth and `read_file` / `glob` / `grep`, behavior-compatible
  with the Python core; black-box compat suite (`tests/compat/`) runs against
  both implementations in CI. Part of the v3 Go rewrite
  (`docs/superpowers/specs/2026-07-04-go-core-rewrite-design.md`).

## [2.5.0] - 2026-07-04

### Changed
- (#65) Recorder LLM transport rewritten: the openai/anthropic SDK adapters
  are replaced by direct httpx clients speaking `/chat/completions` and
  `/v1/messages` (`openai_compat.py`, `anthropic_http.py`). Measured ~13 MB
  RSS reduction (~30% of the process footprint) on a low-memory VPS.
- (#65) The `recorder` / `recorder-anthropic` / `recorder-openai` extras are
  now empty stubs kept only for install-command compatibility — the recorder
  works with the base install (httpx is a core dependency); enable with
  `MYMCP_RECORDER_ENABLED=true`.
- (#65) No client-level HTTP retries anymore (the SDKs retried transient
  failures twice by default); resilience stays at the merge-cycle level
  (circuit breaker, event-driven retry).

### Fixed
- (#65) The recorder supervisor now closes the LLM client's HTTP connection
  pool on shutdown (`LLMClient.aclose()`), instead of leaking it to GC.

## [2.4.0] - 2026-06-09

### Added
- (#52) Security & observability hardening:
  - systemd unit ships with `NoNewPrivileges=true` by default; stronger
    isolation directives (`ProtectSystem`, `ProtectHome`, `PrivateTmp`,
    `ReadWritePaths`, `CapabilityBoundingSet`, `RestrictAddressFamilies`)
    are templated as commented opt-ins for high-security deployments.
  - Token store writes are atomic (`tokens.json.tmp` + `os.replace`) so a
    crash mid-write no longer locks out admin; `last_used` is tracked
    in-memory and flushed on shutdown.
  - New Grafana **Audit Log Integrity** row backed by
    `mymcp_audit_write_failures_total`; tool calls return `InternalError`
    when the audit writer fails (silent audit loss is treated as a SOC red
    line).
  - Path label added to file-tool metrics for finer-grained scoping.
- (#51) Recorder event-driven retry and backlog-based staleness:
  - `RecorderStatus.last_merge_attempt_ts` decouples "we tried" from "we
    succeeded".
  - Idle servers no longer call the LLM — the supervisor reads
    `pending_count()` each tick and skips when zero.
  - Circuit-open recovery is event-driven, not restart-only: the breaker
    retries automatically once `pending_count` grows past the high-water
    mark recorded at trip time.
  - Stale-recorder alert recipe revised to combine `pending_events > 0` with
    `time() - merge_last_attempt_timestamp > 1800` (plus
    `circuit_open == 1`), eliminating the historical false positive on idle
    servers.
- (#50) Quick wins:
  - `.env.example` now uses the `MYMCP_` prefix (post-2.0.0) and documents
    `MYMCP_RECORDER_LLM_MAX_TOKENS` /
    `MYMCP_RECORDER_CIRCUIT_BREAKER_THRESHOLD`.
  - CI enforces a coverage floor (`--cov-fail-under=85`).
  - CI runs `pip-audit` against `requirements-dev.txt` as a separate job.
- (#54) Hypothesis property tests for audit writer↔tailer round-tripping,
  transfer-ticket single-consume + TTL invariants, plus a real-uvicorn
  end-to-end suite. New `docs/runbooks/backup-and-dr.md` runbook documents
  the backup and disaster-recovery procedure.
- (#49) Project assessment plus five themed implementation plans under
  `docs/superpowers/plans/2026-06-06-*`.

### Changed
- (#53) Tool config defaults are now resolved at call time instead of being
  captured in `__defaults__` at import — `patch("mymcp.config.X")` and env
  overrides via `get_settings()` now take effect on every call. File I/O in
  `tools/files.py` was moved off the asyncio event loop (`anyio.to_thread`).
  `prepare_upload` / `prepare_download` audit entries now reflect the
  true outcome of the transfer rather than the ticket mint.
- (#55) Python minor/patch dependency group bumped (7 updates).

### Docs
- (#56) Archived 2026-06-06 plans whose PRs have shipped.

## [2.3.0] - 2026-06-06

### Added
- (#48) Recorder reason labels on merge-cycle metrics
  (`mymcp_recorder_merge_cycles_total{reason}`, duration histogram labelled
  with the same `reason`); SLO gauges (`mymcp_recorder_circuit_open`,
  `mymcp_recorder_merge_last_success_timestamp`,
  `mymcp_recorder_pending_events`); Recorder Health row in the Grafana
  dashboard; `server_overview` banner surfaces circuit/stale/error state in
  priority order. The `recorder.supervisor.cycle` span carries
  `trace_id`/`span_id` for Loki↔Tempo correlation.
- (#43) 57 mutation-killer tests covering audit/dispatch/bash/files paths
  alongside a 5-shard `mutation-full` CI matrix that publishes a mutation
  score badge.

### Changed
- (#47) Recorder resilience overhaul — supervisor circuit breaker, structured
  output protocol so the LLM returns section-level edits (header + Recent
  Changes are Python-owned), JSON-schema enforcement per provider, and a
  prioritised banner in `server_overview`.
- (#45) Tool definitions split out of `mcp_server.py` into
  `mymcp/tool_definitions.py`.
- (#44) Routine dependency bumps grouped by Dependabot.

## [2.2.0] - 2026-05-31

### Added
- (#37) Optional `llm-recorder` module: when installed
  (`pip install algony-mymcp[recorder]`, or `[recorder-anthropic]` /
  `[recorder-openai]`) and enabled (`MYMCP_RECORDER_ENABLED=true`), a
  background task consumes successful mutating audit events and folds them
  into `overview.md` + `changelog.md` via Anthropic or
  OpenAI-compatible LLMs. Exposed through the new MCP tool
  `server_overview`; the overview directory is registered as
  write-protected so external LLMs can read it via `read_file` but not
  overwrite it. Auto-bootstraps the initial overview via a self-contained
  agent loop with internal `bash_probe` / `read_file_probe` tools.
- (#31) Grafana logs/traces dashboard (`mymcp-logs-dashboard.json`) with
  Loki error rate, recent errors, per-`request_id` stream, and Tempo
  slow-trace panel; the main dashboard gained header links and metric→logs
  data links passing the clicked `tool` label. README documents Loki/Tempo
  setup, Promtail journal scrape, and Loki derived-field hint for
  `trace_id` → Tempo.

### Changed
- (#42) Removed the legacy 1.x→2.x upgrade path: bash-based install/upgrade
  scripts, bats tests, Docker integration scenarios, the
  `migrate-from-legacy` CLI subcommand, and the CI job that ran them are
  all gone. README + CLAUDE.md were synced with current behavior (tool
  count bumped to 9, `server_overview` documented, `MYMCP_RECORDER_*` env
  vars listed).
- (#33) OpenTelemetry packages bumped as a group (12 updates) to stay in
  lockstep across the API/SDK/instrumentation surface.
- (#40, #32, #36, #41) Routine python-minor-patch and individual dependency
  bumps grouped by Dependabot.
- (#39) Dependabot ignores configured for `pydantic-core`, `protobuf`
  (major), and `importlib-metadata` (major).
- (#24, #25, #26, #27) GitHub Actions versions bumped (`actions/checkout`,
  `softprops/action-gh-release`, `actions/upload-artifact`,
  `actions/download-artifact`).

## [2.1.1] - 2026-05-15

### Fixed
- `mymcp --help`/subcommand help: every `install-service`, `token`, and
  `migrate` flag now has help text (previously most showed blank).
- `mymcp doctor` reports the OTLP endpoint as `configured` (not `active`) —
  `doctor` never starts the exporter — and reads it without mutating
  `os.environ` or the `get_settings()` singleton.
- `serve` epilog: `/metrics` is described as exposed only when a metrics
  token is set, matching `server.py`'s 503 behavior.

### Added
- `install-service` `--yes` is now functional: confirms interactively, or
  prints the install summary (including metrics/audit/ripgrep flags) plus an
  explicit notice in non-interactive shells.
- Dependabot config for pip (`pyproject.toml`, incl. dev/otlp extras) and
  GitHub Actions; OpenTelemetry packages grouped to move in lockstep.
- `requirements-dev.txt` pip-compile lockfile used as a constraints file so
  local and CI installs match exactly.

## [2.1.0] - 2026-05-13

### Added
- OpenTelemetry three-pillar observability (metrics + traces + logs) with
  optional OTLP push via `OTEL_*` env vars and the `[otlp]` extra.
- File transfer for binary and large files via two new MCP tools
  (`prepare_upload`, `prepare_download`) and bypass HTTP endpoints
  (`PUT /files/raw/{ticket}`, `GET /files/raw/{ticket}`). File bytes
  never enter the LLM context. One-time signed tickets with 5-minute
  default TTL; configurable via `MYMCP_TRANSFER_*` env vars
  (`MYMCP_TRANSFER_ENABLED`, `MYMCP_TRANSFER_MAX_BYTES`,
  `MYMCP_TRANSFER_DEFAULT_TTL_SEC`, `MYMCP_TRANSFER_MAX_TTL_SEC`,
  `MYMCP_PUBLIC_BASE_URL`). See
  `docs/superpowers/specs/2026-05-04-file-transfer-design.md`.

## [2.0.2] - 2026-05-02

### Fixed
- Fixed bug where relative `TOKEN_FILE` paths became invalid after migrating from v1 to v2.
- Added missing `WorkingDirectory` to the systemd service template to ensure proper path resolution.

## [2.0.1] - 2026-04-28

### Fixed
- Fixed mutation testing 0% score caused by Python compatibility issues and config splitting bug.
- Improved Python < 3.11 compatibility (replaced `datetime.UTC` with `timezone.utc`).
- Broadened `TimeoutError` handling in bash and file tools for cross-version consistency.
- Corrected `protected_paths` delimiter in configuration.

### Optimized
- Speed up mutation testing in CI using `--use-coverage`.

### Added
- Documented Prometheus and Grafana monitoring in main README.

## [2.0.0] - 2026-04-28

### Breaking changes
- Environment variables: `MCP_*` → `MYMCP_*` (no compat). Migrate with `mymcp migrate-from-legacy`.
- Install layout: code via `pipx`; config moved from `/opt/mymcp/` to `/etc/mymcp/`.
- Install method: `pipx install algony-mymcp` replaces `git clone + deploy/install.sh`. (PyPI distribution name is `algony-mymcp`; CLI command and import path are still `mymcp`.)
- `MCP_APP_DIR` is removed. Protected paths now derive from the audit log dir + `MYMCP_PROTECTED_PATHS` only.

### Added
- `mymcp` CLI with subcommands: `serve`, `install-service`, `uninstall-service`,
  `token list/add/revoke/rotate-admin/rotate-metrics/disable-metrics`,
  `migrate-from-legacy`, `doctor`, `version`.
- `pipx install algony-mymcp` workflow with `setuptools-scm`-derived versions.
- pydantic-settings-based config with typed defaults.
- Bash subprocess SIGTERM cleanup: in-flight bash children get TERM/KILL with
  configurable grace via `MYMCP_SHUTDOWN_GRACE_SEC`.
- Offline bundle (`mymcp-X.Y.Z-offline-bundle.tar.gz`) attached to GitHub Releases
  for air-gapped installs.
- ruff + mypy + pre-commit configuration; CI matrix on Python 3.11/3.12/3.13.
- Tag-triggered release workflow: build wheel + offline bundle, publish to PyPI
  via OIDC Trusted Publisher, attach artifacts to GitHub Release.

### Changed
- `main.py` split into `src/mymcp/server.py` (FastAPI factory, no import
  side-effects) and `src/mymcp/cli.py` (argparse + logging + signal handlers).
- Logging is configured at CLI entry, not module import. Supports `--log-level`
  and `--log-format text|json`.

### Removed
- `VERSION`, `requirements.txt`, `requirements-dev.txt` (replaced by `pyproject.toml`).
- The flat-layout source files at the repo root.

### Deprecated
- `deploy/install.sh` and `deploy/upgrade.sh` remain in-repo through the 2.0.x
  series for 1.x users; new installs should use the `mymcp` CLI.

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

[Unreleased]: https://github.com/algony-tony/mymcp/compare/v2.4.0...HEAD
[2.4.0]: https://github.com/algony-tony/mymcp/compare/v2.3.0...v2.4.0
[2.3.0]: https://github.com/algony-tony/mymcp/compare/v2.2.0...v2.3.0
[2.2.0]: https://github.com/algony-tony/mymcp/compare/v2.1.1...v2.2.0
[2.1.1]: https://github.com/algony-tony/mymcp/compare/v2.1.0...v2.1.1
[2.1.0]: https://github.com/algony-tony/mymcp/compare/v2.0.2...v2.1.0
[2.0.2]: https://github.com/algony-tony/mymcp/compare/v2.0.1...v2.0.2
[2.0.1]: https://github.com/algony-tony/mymcp/compare/v2.0.0...v2.0.1
[2.0.0]: https://github.com/algony-tony/mymcp/compare/v1.1.1...v2.0.0
[1.1.1]: https://github.com/algony-tony/mymcp/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/algony-tony/mymcp/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/algony-tony/mymcp/releases/tag/v1.0.0
