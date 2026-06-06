# mymcp 2.0 Plan 3: Public Release

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Wire up the release pipeline (tag-triggered GitHub Actions → PyPI via OIDC + offline bundle on GitHub Release), update README/CHANGELOG for the 2.0 user-facing story, and prepare for the 2.0.0 tag.

**Architecture:** Plan 1 produced a buildable wheel; Plan 2 added the production install commands. Plan 3 only adds CI/release plumbing and docs — no application code changes. The first PyPI upload is performed manually (or via test.pypi.org dry-run) so the project name `mymcp` can be claimed and Trusted Publisher configured. Subsequent releases are fully automated via OIDC.

**Tech Stack:** GitHub Actions, `pypa/gh-action-pypi-publish@release/v1` (OIDC), `softprops/action-gh-release@v2`, bash scripts for ripgrep + offline bundle, plain markdown for README/CHANGELOG.

**Source spec:** `docs/superpowers/specs/2026-04-26-pip-distribution-design.md` (§5 release strategy, §6 release.yml).

**Out of scope:** Actually pushing the `v2.0.0` tag (the user controls when the release happens). Configuring PyPI Trusted Publisher (web-UI step, documented but not automated).

---

## File Structure

**Create:**
- `.github/workflows/release.yml` — tag-triggered build + PyPI upload + GH Release
- `scripts/fetch-ripgrep.sh` — download ripgrep static binaries for x86_64+aarch64
- `scripts/install-offline.sh` — runs inside an unpacked offline bundle on the target machine

**Modify:**
- `README.md` — quickstart, install instructions, upgrade-from-1.x guide
- `CHANGELOG.md` — add 2.0.0 entry summarizing all three plans

---

### Task 1: Release workflow

**Files:** `.github/workflows/release.yml`

- [ ] **Step 1: Write the workflow**

```yaml
name: Release

on:
  push:
    tags: ["v*"]

jobs:
  build:
    runs-on: ubuntu-latest
    outputs:
      version: ${{ steps.ver.outputs.version }}
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: |
          python -m pip install --upgrade pip build
          python -m build
      - id: ver
        run: echo "version=${GITHUB_REF_NAME#v}" >> "$GITHUB_OUTPUT"
      - uses: actions/upload-artifact@v4
        with:
          name: dist
          path: dist/*

  offline-bundle:
    runs-on: ubuntu-latest
    needs: build
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - uses: actions/download-artifact@v4
        with:
          name: dist
          path: dist
      - name: Build offline bundle
        run: |
          set -euo pipefail
          BUNDLE=mymcp-${{ needs.build.outputs.version }}-offline-bundle
          mkdir -p $BUNDLE/wheels
          # Pull all wheels (mymcp's own + transitive deps) for x86_64
          pip download dist/*.whl -d $BUNDLE/wheels \
              --python-version 3.11 \
              --platform manylinux2014_x86_64 \
              --only-binary=:all:
          # ripgrep static binaries
          bash scripts/fetch-ripgrep.sh $BUNDLE
          cp scripts/install-offline.sh $BUNDLE/
          chmod +x $BUNDLE/install-offline.sh
          tar czf ${BUNDLE}.tar.gz $BUNDLE
          ls -la ${BUNDLE}.tar.gz
      - uses: actions/upload-artifact@v4
        with:
          name: offline-bundle
          path: mymcp-*-offline-bundle.tar.gz

  publish-pypi:
    runs-on: ubuntu-latest
    needs: build
    permissions:
      id-token: write
    environment: pypi
    steps:
      - uses: actions/download-artifact@v4
        with:
          name: dist
          path: dist
      - uses: pypa/gh-action-pypi-publish@release/v1

  github-release:
    runs-on: ubuntu-latest
    needs: [build, offline-bundle, publish-pypi]
    permissions:
      contents: write
    steps:
      - uses: actions/download-artifact@v4
        with:
          name: dist
          path: dist
      - uses: actions/download-artifact@v4
        with:
          name: offline-bundle
          path: .
      - uses: softprops/action-gh-release@v2
        with:
          files: |
            dist/*
            mymcp-*-offline-bundle.tar.gz
          generate_release_notes: true
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci: add release workflow with PyPI OIDC publish + offline bundle"
```

---

### Task 2: scripts/fetch-ripgrep.sh

**Files:** `scripts/fetch-ripgrep.sh`

- [ ] **Step 1: Write the script**

```bash
#!/usr/bin/env bash
# Usage: fetch-ripgrep.sh BUNDLE_DIR
# Downloads ripgrep static linux binaries for x86_64 and aarch64
# into BUNDLE_DIR/ripgrep-{x86_64,aarch64}.
set -euo pipefail

DEST="${1:?missing BUNDLE_DIR}"
mkdir -p "$DEST"

# Resolve the latest ripgrep release tag
TAG=$(curl -sI https://github.com/BurntSushi/ripgrep/releases/latest \
    | grep -i ^location: | sed 's|.*/||' | tr -d '\r\n')
echo "ripgrep tag: $TAG"

fetch() {
    local arch="$1" tarsuffix="$2"
    local out="$DEST/ripgrep-${arch}"
    local tarball="ripgrep-${TAG}-${tarsuffix}.tar.gz"
    local url="https://github.com/BurntSushi/ripgrep/releases/download/${TAG}/${tarball}"
    echo "fetching $url"
    local tmp; tmp=$(mktemp -d)
    curl -sL "$url" | tar xz -C "$tmp" --strip-components=1
    mv "$tmp/rg" "$out"
    chmod +x "$out"
    rm -rf "$tmp"
}

fetch x86_64  x86_64-unknown-linux-musl
fetch aarch64 aarch64-unknown-linux-gnu

ls -la "$DEST"/ripgrep-*
```

- [ ] **Step 2: Make executable + commit**

```bash
chmod +x scripts/fetch-ripgrep.sh
git add scripts/fetch-ripgrep.sh
git commit -m "build: add fetch-ripgrep.sh for offline bundle CI"
```

---

### Task 3: scripts/install-offline.sh

**Files:** `scripts/install-offline.sh`

- [ ] **Step 1: Write the script**

```bash
#!/usr/bin/env bash
# Run from inside an unpacked mymcp-<ver>-offline-bundle/ directory.
# Installs mymcp + deps from the bundled wheels and places the matching
# ripgrep binary into /usr/local/bin.
set -euo pipefail

BUNDLE_DIR="$(cd "$(dirname "$0")" && pwd)"
WHEELS="$BUNDLE_DIR/wheels"

if [ ! -d "$WHEELS" ]; then
    echo "error: $WHEELS not found. Are you running from inside the unpacked bundle?" >&2
    exit 1
fi

PIP=${PIP:-pip}
if ! command -v "$PIP" >/dev/null 2>&1; then
    echo "error: pip not found on PATH. Install python3 + pip first." >&2
    exit 1
fi

echo "installing mymcp from local wheels..."
"$PIP" install --no-index --find-links "$WHEELS" mymcp

ARCH=$(uname -m)
case "$ARCH" in
    x86_64)  RG="$BUNDLE_DIR/ripgrep-x86_64"  ;;
    aarch64) RG="$BUNDLE_DIR/ripgrep-aarch64" ;;
    *)       echo "warning: no bundled ripgrep for arch=$ARCH" >&2 ; RG="" ;;
esac

if [ -n "$RG" ] && [ -x "$RG" ]; then
    if [ "$(id -u)" -eq 0 ]; then
        cp "$RG" /usr/local/bin/rg
        chmod +x /usr/local/bin/rg
        echo "installed ripgrep -> /usr/local/bin/rg"
    else
        echo "ripgrep binary at $RG (re-run as root to install to /usr/local/bin)"
    fi
fi

echo
echo "Done. Next steps:"
echo "  sudo mymcp install-service --yes        # production"
echo "  mymcp serve                             # dev / quick try"
```

- [ ] **Step 2: Commit**

```bash
chmod +x scripts/install-offline.sh
git add scripts/install-offline.sh
git commit -m "build: add install-offline.sh for air-gapped installs"
```

---

### Task 4: README rewrite

**Files:** `README.md`

- [ ] **Step 1: Read current README**

```bash
head -40 README.md
```

- [ ] **Step 2: Rewrite the install/quickstart sections**

Replace the existing "Install" / "Quickstart" / "Upgrade" parts with:

```markdown
## Install

Requires Python 3.11+ on Linux.

```bash
pipx install mymcp
```

If `pipx` is not available, plain `pip` works too (a venv is recommended):

```bash
python3 -m venv ~/.local/share/mymcp-env
~/.local/share/mymcp-env/bin/pip install mymcp
ln -s ~/.local/share/mymcp-env/bin/mymcp ~/.local/bin/mymcp
```

### Quick try (foreground, no install of system service)

```bash
mymcp serve
```

mymcp prints a temporary admin and rw token to stderr, listens on
`127.0.0.1:8765`, and discards both tokens on exit.

### Production install (systemd)

```bash
sudo mymcp install-service --yes
sudo systemctl start mymcp
```

This writes `/etc/mymcp/.env`, generates an admin token (printed once),
optionally generates a metrics token, installs `/etc/systemd/system/mymcp.service`,
sets up logrotate for `/var/log/mymcp/audit.log`, and (by default) installs
`ripgrep` for fast file search.

### Upgrade

```bash
pipx upgrade mymcp
sudo systemctl restart mymcp
```

### Air-gapped install

Each GitHub Release ships a `mymcp-X.Y.Z-offline-bundle.tar.gz` containing
all wheels and ripgrep binaries:

```bash
tar xzf mymcp-2.0.0-offline-bundle.tar.gz
cd mymcp-2.0.0-offline-bundle
sudo ./install-offline.sh
sudo mymcp install-service --yes
```

## Upgrading from 1.x to 2.0

Breaking changes:
- Environment variable prefix renamed: `MCP_*` → `MYMCP_*` (no compat shim).
- Install layout: `/opt/mymcp/` (1.x) → `/etc/mymcp/` (2.0). Code is now
  managed by `pipx`, not unpacked into `/opt/mymcp/`.
- Install method: `git clone + deploy/install.sh` → `pipx install mymcp`.

One-line migration:

```bash
pipx install mymcp
sudo mymcp migrate-from-legacy
sudo rm -rf /opt/mymcp     # after verifying the new service is healthy
```

`mymcp migrate-from-legacy` reads `/opt/mymcp/.env`, rewrites `MCP_*` keys to
`MYMCP_*`, copies `tokens.json`, installs the new systemd unit, and restarts
the service. Pass `--dry-run` to see what it would do without making changes.

The legacy `deploy/install.sh` and `deploy/upgrade.sh` scripts remain in the
repository through the 2.0.x lifecycle for users who can't migrate yet.

## Configuration

All settings come from `MYMCP_*` environment variables (see
[`src/mymcp/config.py`](src/mymcp/config.py) for the full list and defaults).
The CLI loads `.env` from, in order: `--env-file PATH`, `MYMCP_ENV_FILE`,
`/etc/mymcp/.env`, `./.env`.

## CLI

```
mymcp serve              # run server (foreground)
mymcp install-service    # install systemd service (sudo)
mymcp uninstall-service  # remove systemd service (sudo)
mymcp token list         # list tokens (admin/metrics state + ro/rw list)
mymcp token add --name X --role ro|rw
mymcp token revoke <token>
mymcp token rotate-admin
mymcp token rotate-metrics
mymcp token disable-metrics
mymcp migrate-from-legacy [--dry-run]
mymcp doctor             # diagnostics
mymcp version
```
```

(Leave the rest of the README as-is — feature list, contributing, license.)

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: rewrite README install/quickstart/upgrade sections for 2.0"
```

---

### Task 5: CHANGELOG 2.0.0 entry

**Files:** `CHANGELOG.md`

- [ ] **Step 1: Prepend to CHANGELOG.md** (above existing 1.x entries):

```markdown
## 2.0.0 (unreleased)

Breaking changes:
- Environment variables: `MCP_*` → `MYMCP_*` (no compat). Migrate with `mymcp migrate-from-legacy`.
- Install layout: code via `pipx`; config moved from `/opt/mymcp/` to `/etc/mymcp/`.
- Install method: `pipx install mymcp` replaces `git clone + deploy/install.sh`.
- `MCP_APP_DIR` is removed. Protected paths now derive from the audit log dir + `MYMCP_PROTECTED_PATHS` only.

Added:
- `mymcp` CLI with subcommands: `serve`, `install-service`, `uninstall-service`,
  `token list/add/revoke/rotate-admin/rotate-metrics/disable-metrics`,
  `migrate-from-legacy`, `doctor`, `version`.
- `pipx install mymcp` workflow with `setuptools-scm`-derived versions.
- pydantic-settings-based config with typed defaults.
- Bash subprocess SIGTERM cleanup: in-flight bash children get TERM/KILL with
  configurable grace via `MYMCP_SHUTDOWN_GRACE_SEC`.
- Offline bundle (`mymcp-X.Y.Z-offline-bundle.tar.gz`) attached to GitHub Releases
  for air-gapped installs.
- ruff + mypy + pre-commit configuration; CI matrix on Python 3.11/3.12/3.13.

Changed:
- `main.py` split into `src/mymcp/server.py` (FastAPI factory, no import
  side-effects) and `src/mymcp/cli.py` (argparse + logging + signal handlers).
- Logging is configured at CLI entry, not module import.

Removed:
- `VERSION`, `requirements.txt`, `requirements-dev.txt` (replaced by `pyproject.toml`).
- The flat-layout source files at the repo root.

Deprecated:
- `deploy/install.sh` and `deploy/upgrade.sh` remain in-repo through the 2.0.x
  series for 1.x users; new installs should use the `mymcp` CLI.
```

- [ ] **Step 2: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: add 2.0.0 CHANGELOG entry"
```

---

### Task 6: Final validation + push

- [ ] **Step 1: Run lint + tests one more time**

```bash
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy src/mymcp
.venv/bin/pytest tests/ -q --no-header --benchmark-disable --tb=no
```

- [ ] **Step 2: Build wheel locally and inspect**

```bash
rm -rf dist/ && .venv/bin/python -m build
unzip -l dist/mymcp-*.whl | head -25
```

- [ ] **Step 3: Smoke `mymcp` shell command**

```bash
.venv/bin/mymcp --help
.venv/bin/mymcp doctor
```

- [ ] **Step 4: Push the branch**

```bash
git push
```

- [ ] **Step 5: Open the consolidated PR**

```bash
gh pr create --title "mymcp 2.0: pip-installable distribution + production install + release pipeline" \
  --body "[summarize spec + Plans 1-3 with test plan checkboxes]"
```

---

## Self-review

| Spec section | Task |
|---|---|
| §6 release.yml | Task 1 |
| §6 PyPI Trusted Publisher OIDC | Task 1 (`permissions: id-token: write`) |
| §6 offline bundle | Tasks 1-3 |
| §5 README upgrade section | Task 4 |
| §5 version 2.0.0 (CHANGELOG) | Task 5 |
| First PyPI publish — manual | Documented in Task 1 (Trusted Publisher requires one-time web-UI setup) |

The actual `git tag v2.0.0 && git push --tags` is left to the user — pushing a tag triggers the release workflow, which can't be run usefully until the PyPI Trusted Publisher is configured against the `mymcp` project name.
