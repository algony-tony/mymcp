# install-bootstrap: One-Command Deploy and Post-Install Guidance

**Status:** Draft (awaiting review)
**Date:** 2026-08-26
**Owner:** algony-tony

## Problem

`pipx install algony-mymcp` puts a `mymcp` binary on the machine and then says
nothing. There is no `/etc/mymcp/.env`, no systemd unit, no admin token, and no
hint about what to do next. v3 deleted the v2 `install-service` / `doctor`
subcommands as "Python-CLI machinery" and replaced them with README prose that
tells the operator to hand-write an env file and a unit.

Concretely, a fresh server today is broken in these ways:

1. **No default config file.** `.env.example` lives only in the git repo; a
   `pipx` user never sees it.
2. **No systemd unit.** README asks the operator to author
   `/etc/systemd/system/mymcp.service` by hand.
3. **No admin token path.** `MYMCP_ADMIN_TOKEN` can only come from config;
   `mymcp token add` cannot create it. So the one credential that gates
   `/admin/tokens` must be generated and installed manually.
4. **No next-step guidance.** `mymcp` with no arguments prints
   `usage: mymcp {serve|version|token}` and exits 2. `mymcp --help` prints
   `unknown command: --help` and exits 2.
5. **Stale instructions.** `scripts/install-offline.sh` ends by telling the user
   to run `sudo mymcp install-service --yes` — a command deleted in v3.
6. **Silent recorder loss on upgrade.** README documents that a v2 → v3 upgrade
   drops the recorder sidecar unless three manual steps are run, a failure that
   went unnoticed for four weeks on a production host.

## Goals & Non-Goals

**Goals**

1. One command takes a fresh Linux host from nothing to a running, authenticated
   mymcp service with a pasteable client config.
2. The same flow is safely re-runnable (idempotent) and usable non-interactively
   for automation.
3. A `pipx`-only user is actively guided to the next step by the binary itself.
4. A single `doctor` command proves the install actually works, rather than
   merely that files exist.
5. Works for strangers: multiple distros, offline installs, hosts with no
   Python, hosts with no systemd.

**Non-Goals**

- **No `mymcp uninstall`.** Idempotent re-run covers the common need; the
  wizard prints the four teardown commands. Not worth a `--purge` semantic and
  its test surface.
- **No config management integration.** No Ansible module, no Helm chart. The
  non-interactive flag surface is the integration point.
- **No TLS termination or reverse-proxy provisioning.** The wizard advises;
  it does not configure nginx/caddy.
- **No multi-host orchestration.** One run configures one host.

## Decisions Taken

These were settled during design and are not open in implementation:

| Decision | Choice | Rationale |
|---|---|---|
| Audience | Open-source users, not just the maintainer | Drives multi-distro handling, non-systemd degradation, offline path |
| Default service user | `root`, with an explicit in-wizard security warning | Matches the product's purpose (LLM operates the host). `bash_execute` is not subject to protected paths, so a token is equivalent to a root shell — the wizard says so out loud and recommends `ro` tokens for untrusted clients |
| Scope | Main service + `doctor` + client-config output + recorder + ripgrep + network-exposure advice | All four optional areas were requested |
| Uninstall | Out of scope | YAGNI |

## Architecture

Three layers, with configuration logic concentrated in Go so it is testable:

```
curl … | sudo bash
        │
        ▼
scripts/install.sh          "get the binary onto the box" ONLY
  arch detect → pipx or raw binary + sha256 → exec mymcp init "$@"
        │
        ▼
mymcp init                  the only configuration engine (Go)
  wizard.go ──Plan──> apply.go ──> env.go / unit.go / system.go
        │
        ▼
mymcp doctor                proves it works
```

### Component responsibilities

| Component | Responsibility | Dependencies | Status |
|---|---|---|---|
| `scripts/install.sh` | Detect arch; install the binary (pipx if present, else raw release binary verified against `SHA256SUMS`); `exec mymcp init "$@"`. **Contains no configuration logic.** | `curl`/`wget`, `uname` | New (~100 lines) |
| `go/internal/setup/` | The configuration engine: render `.env`, render the main unit, create dirs/user, generate tokens, drive `systemctl`, idempotent diffing | stdlib + `os/exec` only | New package |
| `go/internal/setup/doctor.go` | Check list plus remediation commands; run automatically at the end of `init`, also standalone | same | New |
| `go/cmd/mymcp/main.go` | `init`, `doctor`, `config example` subcommands; status-aware no-arg output; `-h`/`--help` | `setup` | Changed |
| `src/mymcp/recorder/templates/mymcp-recorder.service.in` | Remains the single source of truth for the recorder unit | — | Template unchanged; `--install-unit` gains `--service-user` and `--env-file` |
| `.github/workflows/release.yml` | Additionally upload raw binaries, `SHA256SUMS`, and `install.sh` | — | Changed (binaries are already built, never uploaded) |

### Boundary decisions

1. **The main unit template is owned by Go (`go:embed`); the recorder unit
   template stays owned by Python.** When the recorder is enabled, `mymcp init`
   shells out to
   `mymcp-recorder --install-unit --service-user <user> --env-file <path>`
   rather than embedding a second copy of that template. Keeping one template in
   two languages guarantees drift. The cost is cross-process coupling at
   configure time; the failure is loud (command absent → "recorder dependencies
   are not installed").

2. **The `.env` template is embedded in the Go binary.** This is the fix for
   "no default config file". `mymcp init` renders a fully commented
   `/etc/mymcp/.env` (mode 0600); `mymcp config example` prints it to stdout.

3. **The raw-binary install path has no recorder.** A host that had no `pipx`
   may have no Python at all. `init` decides by an explicit three-way rule:
   `mymcp-recorder` already on `PATH` → offer the recorder and skip the
   dependency install; else `pipx` on `PATH` → offer it and run `pipx inject`;
   else → *skip the question with an explanation* rather than asking and then
   failing. (The README-documented plain-`pip`-into-a-venv install lands in the
   first branch once the extra is installed, and in the third otherwise.)

### `setup` package layout

One concern per file, so the wizard can be tested without a TTY:

```
setup/
  plan.go     Plan struct — the product of every decision
  wizard.go   TTY interaction → Plan; non-interactive flags → Plan
  apply.go    Plan → ordered idempotent steps; each returns created/updated/unchanged
  env.go      embedded .env template, render, line-merge into an existing file
  unit.go     embedded mymcp.service.in, render
  system.go   the ONLY file that execs external commands (systemctl/useradd/ss/apt|dnf|pacman)
  doctor.go   checks
```

`wizard.go` and `apply.go` communicate only through `Plan`. Every apply step is
therefore testable against `t.TempDir()` with no TTY, and `system.go` is the
single stub boundary.

## `mymcp init`

### Flag surface

| Flag | Default | Notes |
|---|---|---|
| `-yes` | false | Non-interactive; take every default |
| `-bind` / `-port` | `0.0.0.0` / `8765` | |
| `-service-user` | `root` | `mymcp` triggers `useradd -r` + `chown` |
| `-config-dir` | `/etc/mymcp` | |
| `-log-dir` | `/var/log/mymcp` | |
| `-recorder-data-dir` | `/var/lib/mymcp/recorder` | |
| `-audit` | true | Writes `MYMCP_AUDIT_ENABLED=true` explicitly |
| `-metrics-token` | generated | Empty string leaves `/metrics` unauthenticated |
| `-client-name` / `-client-role` | `default` / `rw` | First client token |
| `-recorder` | false | Only available when `pipx` is present |
| `-recorder-provider` / `-recorder-model` / `-recorder-api-key` | `anthropic` / empty / empty | Key also readable from the environment to keep it out of shell history |
| `-install-ripgrep` | true iff `rg` missing | |
| `-ripgrep-binary` | empty | Offline bundle passes its bundled binary here |
| `-start` | true | `enable --now`; `restart` if already running |
| `-dry-run` | false | Print every diff, write nothing |

Non-TTY without `-yes` is an error, not a silent default-take. Exit codes:
`0` success, `1` apply failure, `2` usage error.

**Prompts read from `/dev/tty`, never from stdin.** Under
`curl … | sudo bash`, stdin is the pipe, so a stdin-reading prompt sees EOF and
the interactive mode of the headline install command is dead on arrival. If
`/dev/tty` cannot be opened, `init` errors and asks for `-yes`.

### Preflight (before any prompt)

Failing after a questionnaire is hostile, so these run first:

1. **Not root** → error with `sudo mymcp init`.
2. **No systemd** (`/run/systemd/system` absent) → **degraded mode**: skip every
   unit/start step and the questions that feed them, do everything else, and
   print the foreground run command at the end. This is not a failure —
   containers, WSL and OpenRC hosts still get a correct `.env` and tokens.
3. **Existing install** (readable `.env` or unit) → **update mode**: every
   wizard default is seeded from the current configuration.
4. **`pipx`/Python presence** → decides whether the recorder question appears.

### Wizard questions

Seven questions, each with a default, so pressing Enter throughout is a valid
install.

| # | Question | Attached behaviour |
|---|---|---|
| 1 | Bind address + port | Choosing `0.0.0.0` prints the exposure warning (reachable port + token ⇒ root shell) and offers `127.0.0.1` + reverse proxy; emits concrete `ufw`/`firewalld` allow commands when either is detected; `ss -tlnp` conflict check re-asks on a busy port |
| 2 | Service user | Default root; prints the security warning above |
| 3 | Audit logging | Default on |
| 4 | First client token name + role | Default `default` / `rw`; notes `ro` is safer |
| 5 | Enable recorder | Default no; if yes, asks provider / model / API key (echo disabled via `stty -echo`, no new dependency; echoes with a warning if no tty) |
| 6 | Install ripgrep | Only shown when `rg` is missing; probes apt/dnf/pacman; prefers `-ripgrep-binary` when given |
| 7 | Confirmation summary | Lists every file to be written and command to be run |

**No logrotate config is installed.** `go/internal/audit/audit.go` rotates itself
on `maxBytes`/`backupCount` with `RotatingFileHandler` semantics; adding
logrotate would fight the writer for the same files. The v2 spec's logrotate
step is actively harmful under the Go core and is deliberately dropped.

**`MYMCP_AUDIT_ENABLED=true` is written explicitly** because
`config.go` defaults it to `false`. Relying on the default would silently ship
an unaudited server, violating the project's own stated SOC red line.

### Apply order and idempotency

Each step reports `created` / `updated` / `unchanged`:

1. `useradd -r` when `-service-user` is not root
2. Create config, log and recorder data dirs (0750, chown to the service user)
3. Merge-write `<config-dir>/.env` (0600)
4. Ensure `<config-dir>/tokens.json` exists (0600)
5. Create the first client token
6. Write `/etc/systemd/system/mymcp.service`, `systemctl daemon-reload`
7. *(recorder)* `pipx inject algony-mymcp "algony-mymcp[recorder]"` →
   `mymcp-recorder --install-unit …` → write the second unit
8. `systemctl enable --now` (or `restart` when already active)
9. Run `doctor`
10. Print the summary

Three idempotency requirements are load-bearing:

- **`.env` is line-merged, never overwritten.** Keys the wizard owns get their
  value replaced in place; unrelated keys and user comments are preserved
  verbatim; missing keys are appended to the matching section. A backup is
  written to `.env.bak-<timestamp>` first. A full rewrite would eat hand-edited
  `MYMCP_PROTECTED_PATHS` / `MYMCP_PUBLIC_BASE_URL` values.
- **The admin token is generated only when absent.** A non-empty existing
  `MYMCP_ADMIN_TOKEN` is preserved; regenerating it on re-run would instantly
  break every existing admin client.
- **The first client token is deduplicated by name.** Otherwise each re-run
  leaves another zombie token in the store.

**No automatic rollback on mid-apply failure.** A partial rollback is more
dangerous than none. Instead `init` prints which steps completed, which one
failed, and that re-running `mymcp init` resumes from the failure — which is
exactly what per-step idempotency buys.

### Closing output

```
✓ mymcp 3.x.x is running on 10.0.0.7:8765

  URL     http://10.0.0.7:8765/mcp
  Token   tok_a1b2…   (rw, name=default)

  claude mcp add --transport http ucloud http://10.0.0.7:8765/mcp \
      --header "Authorization: Bearer tok_a1b2…"

  Generic MCP client JSON: { "mcpServers": { … } }

  Admin token: tok_9f8e…   (shown once; recoverable from /etc/mymcp/.env)

  Next: mymcp doctor  |  journalctl -u mymcp -f
```

When bound to `0.0.0.0` the URL prints the host's real primary address rather
than `0.0.0.0`, which is not usable in a client config. This is the last metre
of "paste and it works".

## `mymcp doctor`

### Checks

Five layers, each item reporting `✓ / ⚠ / ✗` with a pasteable remediation.

**INSTALL**

1. Binary path and version
2. **Number of `mymcp` binaries on `PATH`** — approach C creates two install
   channels (pipx `~/.local/bin` vs raw `/usr/local/bin`); upgrading one while
   systemd runs the other presents as "I upgraded but nothing changed"
3. `ripgrep` present (⚠ only — `grep` has a native fallback)

**CONFIG**

4. `.env` exists, mode 0600 (looser is `✗` — it holds the admin token)
5. `.env` parses via `config.Load()` (reuse the production path, do not write a
   second parser)
6. `MYMCP_ADMIN_TOKEN` non-empty
7. `tokens.json` exists, 0600, at least one enabled token
8. All three directories exist, are writable, owned by the service user
9. `MYMCP_AUDIT_ENABLED=true` (⚠ referencing the SOC red line)

**RUNTIME**

10. systemd present → unit present → `is-enabled` / `is-active`
11. **The unit's `ExecStart` binary equals the `PATH`-resolved one** (pairs with
    check 2 to pin down which copy is actually running)
12. The port is listening (`ss`)

**FUNCTIONAL**

13. `doctor` reads an enabled token from `tokens.json` — **preferring an `rw`
    token** — and **actually issues `POST /mcp` with `tools/list`**. With an
    `rw` token it asserts all 9 tools; with only an `ro` token available it
    asserts a non-empty list whose names are all in `readTools`, because
    `tools/list` is role-filtered. Read-only, no side effects. This is the only
    check that proves the install works rather than that files exist.
14. `/metrics` reachable (with the metrics token when set)

**RECORDER** (only when `MYMCP_RECORDER_ENABLED=true`)

15. `mymcp-recorder` present and its dependencies importable
16. Recorder unit `is-active`
17. **Read `mymcp_recorder_pending_events` and
    `mymcp_recorder_merge_last_attempt_timestamp` from `/metrics` and apply the
    project's own stall predicate**, rather than README's current
    `sleep 310`-and-compare-`cursor.json` ritual. This turns the four-week
    production blindspot into a single check.
18. `mymcp_recorder_circuit_open == 1` → `✗`; `overview.md` exists with a
    recent mtime

### Output and exit codes

```
mymcp doctor

INSTALL
  ✓ binary       /usr/local/bin/mymcp (3.1.0)
  ✗ duplicates   2 mymcp on PATH: /usr/local/bin/mymcp, ~/.local/bin/mymcp
                 → the unit runs /usr/local/bin/mymcp; remove the other:
                   pipx uninstall algony-mymcp
  ⚠ ripgrep      not installed (grep falls back to a native scan)
                 → apt install -y ripgrep
CONFIG   ✓ ×5   ⚠ audit disabled → /etc/mymcp/.env: MYMCP_AUDIT_ENABLED=true
RUNTIME  ✓ ×3
FUNCTIONAL
  ✓ tools/list   9/9 tools (rw token, 12ms)

1 problem, 2 warnings.
```

Exit `0` when everything passes or only warnings remain, `1` on any `✗`.
`-strict` promotes warnings to failures (CI/Ansible). `-json` emits a
machine-readable structure.

## Post-install guidance

`pipx` has no post-install hook, so guidance must come from the binary. Five
small changes:

**A. Bare `mymcp` becomes status-aware** instead of printing usage and exiting 2:

```
mymcp 3.1.0

  ✗ not initialised (/etc/mymcp/.env does not exist)

  Next:
    sudo mymcp init      install as a systemd service (recommended)
    mymcp serve          foreground trial run; tokens vanish on exit

  Also: mymcp doctor | mymcp token list | mymcp -h
```

Initialised but not running → suggest `systemctl start mymcp`. All healthy →
print the URL and a one-line doctor summary. This single change resolves most of
the reported confusion.

**B. Temporary-token mode** currently ends at "tokens are in-memory; they vanish
on exit". Add the way out: `to install as a persistent service: sudo mymcp init`.

**C. `serve` with a present `.env` but empty admin token** currently fails with
`MYMCP_ADMIN_TOKEN environment variable is required`, which points nowhere.
Point it at `mymcp init` / `mymcp doctor`.

**D. Add `-h` / `--help`.** Today `mymcp --help` falls through to
`unknown command: --help` and exits 2 — a poor first impression.

**E. Fix `scripts/install-offline.sh`**, which still advertises the deleted
`mymcp install-service --yes`, to `exec mymcp init`.

## Packaging and release

### New release assets

`release.yml` already builds `bin/mymcp-linux-{amd64,arm64}` and has never
uploaded them. Add:

- `mymcp-linux-amd64`, `mymcp-linux-arm64`
- `SHA256SUMS`
- `install.sh` itself

Distribution URL is
`https://github.com/algony-tony/mymcp/releases/latest/download/<asset>`. Two
concrete reasons: the script and the binaries it fetches are always from the
same release, and no GitHub API call is needed — unauthenticated API rate limits
behind NAT are the classic failure mode for `curl | bash` installers.

### `install.sh` contract

```bash
curl -fsSL https://github.com/algony-tony/mymcp/releases/latest/download/install.sh | sudo bash
curl -fsSL … | sudo bash -s -- --yes --port 9000     # args pass through to mymcp init
```

Require root → `uname -s`/`-m` detection (anything other than Linux
amd64/arm64 errors explicitly) → choose method (`auto`: pipx when present, else
raw binary; `--method` forces) → for the raw path download to a temp file,
**verify SHA256**, `install -m 0755` into `/usr/local/bin/mymcp` →
`exec mymcp init "$@"`.

### Single source of truth for `.env.example`

The embedded Go template is authoritative. The repository's `.env.example`
becomes generated output (`mymcp config example`), and CI adds a
`git diff --exit-code .env.example` step. Without this the embedded template and
the repo sample drift apart at the next config change.

### Offline bundle

Add the raw binary to the bundle; change `install-offline.sh` to `exec mymcp
init`; pass the bundled ripgrep through `-ripgrep-binary` so an air-gapped host
completes the same flow.

### Documentation

- Delete README's hand-written-unit "Production install (systemd)" prose;
  replace with `sudo mymcp init`.
- Lead with the one-line `curl`; `pipx install` + `sudo mymcp init` second.
- Replace the manual three-step "From v2.x" recorder migration with
  `sudo mymcp init` — being idempotent, it installs the missing recorder unit,
  which removes the silent-recorder-loss failure README currently documents.
- Update CLAUDE.md (Commands section, `internal/setup` package).

## Testing

Deployment code is the easiest to ship untested. Four layers:

1. **`setup` package unit tests (the bulk).** Everything runs against
   `t.TempDir()`; `system.go` is an interface with a fake. Required cases:
   `.env` line-merge (preserves comments and user keys, replaces owned keys,
   appends missing ones), admin token not regenerated, client token deduplicated
   by name, update mode seeds defaults from an existing `.env`, degraded mode
   skips the right steps, `-dry-run` leaves `TempDir` empty.
2. **`doctor` unit tests.** Construct bad states (`.env` at 0644, missing
   `tokens.json`, two binaries on `PATH`, audit disabled, recorder backlog) and
   assert the severity classification and exit code.
3. **End-to-end smoke in CI.** At least one real chain:
   `init -no-start` → `serve` → `doctor -strict` → `tools/list` returns all 9
   tools via the generated `rw` token.
   Every unit test above can pass while the pieces fail to connect; this is the
   backstop.
4. **`install.sh`.** shellcheck in CI, plus a mock-server run covering the
   download, checksum-pass and checksum-fail branches.

Per the project's TDD convention, these tests precede their implementation in
the plan.

## Risks

| Risk | Mitigation |
|---|---|
| Two install channels diverge (pipx vs raw binary) | doctor checks 2 and 11 detect it explicitly and name the remedy |
| `curl \| bash` breaks interactive prompts | Prompts read `/dev/tty`; explicit error demanding `-yes` when unavailable |
| Recorder unit template drift between Go and Python | Python keeps sole ownership; Go shells out to `--install-unit` |
| `.env` merge corrupts a hand-edited file | Timestamped backup before write; merge is line-scoped; `-dry-run` shows the diff |
| Default-root install widens blast radius | Explicit in-wizard warning, `ro` token recommendation, bind/firewall advice at question 1 |
