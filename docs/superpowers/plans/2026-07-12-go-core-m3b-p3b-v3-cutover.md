# Go Core M3b Phase 3b — v3.0.0 Cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship v3.0.0 — the Go binary becomes the one and only `mymcp` server; the Python package is demoted to a recorder-only sidecar with zero base dependencies, published as platform-tagged wheels that bundle the Go binary.

**Architecture:** Two PRs. **Part A (3b-prep, non-breaking)** closes the M3a write-protection gap in the Go core and severs the recorder's last two couplings to the doomed Python core — the core, tests, and both compat suites stay green. **Part B (3b-final, atomic breaking change)** flips `pyproject.toml` to zero base deps, deletes the Python core + its tests, rewires CI/release around platform wheels, and bumps to 3.0.0. Part B is atomic by nature: the pyproject flip and the core deletion cannot be half-landed without breaking the test suite and `compat-python`.

**Tech Stack:** Go 1.25 (core), Python 3.11+ (recorder sidecar only), setuptools + setuptools-scm, `wheel` (platform-wheel assembly), GitHub Actions, pytest, mutmut.

---

## File Structure (what changes and why)

**Part A — modify (no deletions of shipping code):**
- `go/internal/tools/readfile.go` — `ProtectedFromConfig` gains a write-only entry for `<RecorderDataDir>/overview` (parity with the Python recorder's `register_protected_path(..., modes={"write"})`, which is about to be deleted).
- `go/internal/tools/readfile_test.go` (or a new `protected_test.go`) — asserts overview dir is write-protected but readable.
- `src/mymcp/recorder/wiring.py` — drop `from mymcp.tools.files import register_protected_path` and the call. Overview protection now lives in the Go core.
- `src/mymcp/recorder/admin.py` — **delete** (FastAPI `/admin/overview` router; only mounted by the deleted `server.py`; sole recorder importer of `mymcp.auth` + `fastapi`).
- `tests/recorder/test_admin.py` — **delete** (tests the deleted router).
- `tests/recorder/test_wiring.py` — remove `test_build_supervisor_protects_overview_dir` (asserts Python-side protection that moved to Go).
- `tests/recorder/test_recorder_import_purity.py` — **new** guard test: importing `mymcp.recorder.*` must not import any deleted-core module.

**Part B — pyproject / packaging:**
- `pyproject.toml` — `dependencies = []`; drop `mymcp = "mymcp.cli:main"` from `[project.scripts]`; move old core deps into an inert `legacy`/removed state; keep recorder deps in `[recorder]`; repoint `[tool.mutmut].paths_to_mutate`; drop `Framework :: FastAPI` classifier.

**Part B — delete (the Python core):**
- `src/mymcp/server.py`, `mcp_server.py`, `cli.py`, `__main__.py`, `tool_definitions.py`, `audit.py`, `auth.py`
- `src/mymcp/tools/` (whole dir: `bash.py`, `files.py`, `transfer.py`, `__init__.py`)
- `src/mymcp/transfer/` (whole dir: `endpoints.py`, `tickets.py`, `__init__.py`)
- ~40 `tests/*.py` + a few `tests/recorder/*.py` that import the deleted core (exact list in Task B2).

**Part B — keep (the recorder sidecar package):**
- `src/mymcp/__init__.py`, `_version.py`, `config.py`, `audit_output.py`
- `src/mymcp/observability/` (all)
- `src/mymcp/deploy/` (all — stdlib-only, core-decoupled, ships both systemd unit templates; now library-only since the CLI that invoked it is gone)
- `src/mymcp/recorder/` (all except the deleted `admin.py`)

**Part B — CI / release:**
- `.github/workflows/ci.yml` — delete the `compat-python` job (no Python server exists in v3); keep `compat-go` (the Go server *is* the server); repoint the mutation jobs at surviving modules.
- `.github/workflows/release.yml` — publish **only** the platform wheels; the pure wheel and sdist become assembler inputs / are not published.
- `CHANGELOG.md`, `CLAUDE.md`, `README.md` — v3.0.0 notes.

---

# PART A — Phase 3b-prep (non-breaking, own PR)

Branch: `feat/go-core-m3b-p3b-prep`. Everything here keeps the Python core, all tests, and both compat suites green. This de-risks Part B by shrinking the atomic PR and pre-landing the Go write-protection.

### Task A1: Go core — write-protect the recorder overview dir

**Files:**
- Modify: `go/internal/tools/readfile.go:35-40` (`ProtectedFromConfig`)
- Test: `go/internal/tools/readfile_test.go` (add a test; if the file doesn't exist, create `go/internal/tools/protected_test.go` in `package tools`)

- [ ] **Step 1: Write the failing Go test**

```go
func TestProtectedFromConfigWriteProtectsOverviewDir(t *testing.T) {
	cfg := &config.Config{
		AuditLogDir:     "/tmp/does-not-matter-audit",
		RecorderDataDir: "/var/lib/mymcp/recorder",
	}
	prot := ProtectedFromConfig(cfg)
	overview := "/var/lib/mymcp/recorder/overview/overview.md"

	// Writes to the overview tree are denied...
	if msg := fsutil.CheckProtectedPath(overview, fsutil.ModeWrite, prot); msg == "" {
		t.Fatalf("expected overview dir to be write-protected, got allow")
	}
	// ...but reads are allowed (external LLMs fetch changelog.md via read_file).
	if msg := fsutil.CheckProtectedPath(overview, fsutil.ModeRead, prot); msg != "" {
		t.Fatalf("expected overview dir to be readable, got deny: %s", msg)
	}
}
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `cd /home/zhu/repos/mymcp/go && go test ./internal/tools/ -run TestProtectedFromConfigWriteProtectsOverviewDir -v`
Expected: FAIL (overview currently not in the protected set → write allowed).

- [ ] **Step 3: Implement — append the write-only overview entry**

In `go/internal/tools/readfile.go`, change `ProtectedFromConfig` to:

```go
func ProtectedFromConfig(cfg *config.Config) []fsutil.ProtectedEntry {
	var out []fsutil.ProtectedEntry
	for _, p := range cfg.ProtectedPaths() {
		out = append(out, fsutil.ProtectedEntry{Pattern: p, Modes: fsutil.ModeRead | fsutil.ModeWrite})
	}
	// The recorder overview dir is mymcp-owned: external file tools may READ it
	// (so external LLMs can fetch changelog.md) but never WRITE to it. Mirrors
	// the recorder's former register_protected_path(overview, modes={"write"}).
	if cfg.RecorderDataDir != "" {
		out = append(out, fsutil.ProtectedEntry{
			Pattern: filepath.Join(cfg.RecorderDataDir, "overview"),
			Modes:   fsutil.ModeWrite,
		})
	}
	return out
}
```

Ensure `path/filepath` is imported in `readfile.go`.

- [ ] **Step 4: Run the test — expect PASS**

Run: `cd /home/zhu/repos/mymcp/go && go test ./internal/tools/ -run TestProtectedFromConfigWriteProtectsOverviewDir -v`
Expected: PASS

- [ ] **Step 5: Full Go suite + vet**

Run: `cd /home/zhu/repos/mymcp/go && go vet ./... && go test ./...`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add go/internal/tools/readfile.go go/internal/tools/*_test.go
git commit -m "feat(go): write-protect recorder overview dir in ProtectedFromConfig"
```

### Task A2: Recorder — sever the Python-core couplings

**Files:**
- Modify: `src/mymcp/recorder/wiring.py:16,37`
- Delete: `src/mymcp/recorder/admin.py`, `tests/recorder/test_admin.py`
- Modify: `tests/recorder/test_wiring.py` (remove the overview-protection test)
- Create: `tests/recorder/test_recorder_import_purity.py`

- [ ] **Step 1: Write the failing import-purity guard**

`tests/recorder/test_recorder_import_purity.py`:

```python
"""Guard: the recorder sidecar must not import the (soon-deleted) Python core.

After v3 the Python package is recorder-only; the Go binary is the server. This
test fails loudly if anyone re-introduces a dependency on server/mcp_server/
tools/transfer/auth/cli, which would break the standalone `mymcp-recorder`.
"""

import importlib
import pkgutil

import mymcp.recorder

FORBIDDEN = (
    "mymcp.server",
    "mymcp.mcp_server",
    "mymcp.cli",
    "mymcp.tool_definitions",
    "mymcp.auth",
    "mymcp.audit",  # the writer; recorder reads audit.log via its own tailer
    "mymcp.tools",
    "mymcp.transfer",
)


def _all_recorder_modules():
    mods = ["mymcp.recorder"]
    for m in pkgutil.walk_packages(mymcp.recorder.__path__, "mymcp.recorder."):
        mods.append(m.name)
    return mods


def test_recorder_modules_do_not_import_core():
    import sys

    offenders = {}
    for name in _all_recorder_modules():
        before = set(sys.modules)
        importlib.import_module(name)
        after = set(sys.modules)
        pulled = [
            f for f in FORBIDDEN
            if any(mod == f or mod.startswith(f + ".") for mod in (after - before))
        ]
        # Also catch modules already imported by an earlier iteration.
        pulled += [f for f in FORBIDDEN if f in sys.modules]
        if pulled:
            offenders[name] = sorted(set(pulled))
    assert not offenders, f"recorder modules importing core: {offenders}"
```

- [ ] **Step 2: Run it — expect FAIL**

Run: `.venv/bin/pytest tests/recorder/test_recorder_import_purity.py -v`
Expected: FAIL — `wiring` pulls `mymcp.tools` (via `register_protected_path`) and `admin` pulls `mymcp.auth`.

- [ ] **Step 3: Sever wiring.py**

In `src/mymcp/recorder/wiring.py`, delete the import line:

```python
from mymcp.tools.files import register_protected_path
```

and delete the call + its comment in `build_supervisor`:

```python
    # The overview directory is mymcp-owned; external file tools may READ it
    # (so external LLMs can fetch changelog.md) but not WRITE to it.
    register_protected_path(str(overview_dir), modes={"write"})
```

(`overview_dir` is still used below for `OverviewStore`, so leave `overview_dir = data_dir / "overview"`.)

- [ ] **Step 4: Delete the admin router + its test**

```bash
git rm src/mymcp/recorder/admin.py tests/recorder/test_admin.py
```

- [ ] **Step 5: Remove the obsolete overview-protection test**

In `tests/recorder/test_wiring.py`, delete the whole `test_build_supervisor_protects_overview_dir` function (lines ~20-36, the one importing `from mymcp.tools.files import _runtime_protected, check_protected_path`). Overview protection is now the Go core's job (Task A1).

- [ ] **Step 6: Run the guard + recorder suite — expect PASS**

Run: `.venv/bin/pytest tests/recorder/ -v --benchmark-disable`
Expected: all PASS (import-purity green; no test references the removed admin router).

- [ ] **Step 7: Full Python suite (core still present) — expect PASS**

Run: `.venv/bin/pytest tests/ -q --benchmark-disable -p no:randomly`
Expected: PASS. (The core still exists; only the recorder decoupled. `test_server_factory` mounting the recorder admin router: verify `server.py` still imports `mymcp.recorder.admin` — if it does, this suite will error. See Step 8.)

- [ ] **Step 8: Fix the core's now-dangling admin import (if present)**

`server.py:134-138,175-177` import `from mymcp.recorder import admin as recorder_admin` and mount its router. Since `admin.py` is deleted, guard those blocks so the *still-shipping v2 core* keeps working in Part A. Wrap both in a tolerant import:

```python
    try:
        from mymcp.recorder import admin as recorder_admin
    except ImportError:
        recorder_admin = None
    ...
    if recorder_admin is not None:
        app.include_router(recorder_admin.router)
        recorder_admin.set_supervisor(supervisor)
```

Re-run Step 7 until green. (Part B deletes `server.py` entirely, so this guard is transient — but Part A must not break the core.)

- [ ] **Step 9: Lint**

Run: `.venv/bin/ruff check src/mymcp tests && .venv/bin/ruff format --check src/mymcp tests`
Expected: clean (fix imports if `ruff --fix` flags I001).

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "refactor(recorder): sever core couplings (drop register_protected_path + admin router)"
```

### Task A3: CHANGELOG + push + PR

- [ ] **Step 1:** Add an `Unreleased` bullet to `CHANGELOG.md`:
  `- Go core: recorder overview dir is now write-protected by the Go server. Recorder sidecar no longer imports the Python core (dropped the FastAPI admin router + register_protected_path).`
- [ ] **Step 2:** `git commit -am "docs(changelog): 3b-prep"` then push branch and open PR.
- [ ] **Step 3:** Wait for CI green (lint, go, test 3.11/3.12/3.13, compat-python, compat-go, mutation-smoke). Merge (squash) once green.

---

# PART B — Phase 3b-final (atomic v3.0.0 breaking change, own PR)

Branch: `feat/go-core-v3-cutover`. **Do not start until Part A is merged.** This deletes the Python core and cannot be partially landed. Deletion work is verified by "the remaining suite + compat-go + a real assembled wheel are green," not by unit TDD.

### Task B1: Flip pyproject.toml to a recorder-only, zero-base-deps package

**Files:** Modify `pyproject.toml`

- [ ] **Step 1: Zero the base dependencies**

Replace the `dependencies = [...]` list with:

```toml
# v3: the server is the bundled Go binary (installed as `mymcp` from the
# platform wheel). The Python package is a recorder-only sidecar; base install
# pulls nothing. Recorder features require the [recorder] extra.
dependencies = []
```

- [ ] **Step 2: Drop the Python `mymcp` console entry**

In `[project.scripts]`, remove `mymcp = "mymcp.cli:main"`, keeping only:

```toml
[project.scripts]
mymcp-recorder = "mymcp.recorder.__main__:main"
```

(The `mymcp` command is provided by the Go binary, injected into `<name>-<ver>.data/scripts/mymcp` by `scripts/assemble_wheel.py`, which also strips this very entry.)

- [ ] **Step 3: Drop the FastAPI classifier**

Remove `"Framework :: FastAPI",` from `classifiers` (no FastAPI in v3).

- [ ] **Step 4: Repoint mutmut at surviving modules**

Replace `[tool.mutmut].paths_to_mutate` (which lists deleted core files) with recorder/config modules that have strong tests:

```toml
[tool.mutmut]
paths_to_mutate = [
    "src/mymcp/config.py",
    "src/mymcp/recorder/cursor.py",
    "src/mymcp/recorder/events.py",
    "src/mymcp/recorder/merge_cycle.py",
]
also_copy = ["src/", "scripts/"]
tests_dir = ["tests/"]
pytest_add_cli_args = ["--benchmark-disable"]
```

- [ ] **Step 5: Verify pyproject parses + the sidecar entry still resolves**

Run: `.venv/bin/python -c "import tomllib,pathlib; tomllib.loads(pathlib.Path('pyproject.toml').read_text()); print('ok')"`
Expected: `ok`

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml
git commit -m "build!: v3 pyproject — zero base deps, drop python mymcp entry, repoint mutmut"
```

### Task B2: Delete the Python core + its tests

**Files:** delete (see lists below)

- [ ] **Step 1: Delete core source modules**

```bash
git rm src/mymcp/server.py src/mymcp/mcp_server.py src/mymcp/cli.py \
       src/mymcp/__main__.py src/mymcp/tool_definitions.py \
       src/mymcp/audit.py src/mymcp/auth.py
git rm -r src/mymcp/tools src/mymcp/transfer
```

- [ ] **Step 2: Delete core tests**

```bash
git rm tests/test_admin.py tests/test_audit.py tests/test_audit_otel.py \
  tests/test_audit_properties.py tests/test_auth.py tests/test_bash.py \
  tests/test_bash_signal_cleanup.py tests/test_benchmark.py tests/test_boundary.py \
  tests/test_clamping.py tests/test_cli.py tests/test_doctor.py tests/test_e2e_server.py \
  tests/test_files.py tests/test_install_service_cli.py tests/test_integration.py \
  tests/test_main.py tests/test_mcp.py tests/test_metrics.py tests/test_metrics_saturation.py \
  tests/test_permissions.py tests/test_protected_path_properties.py \
  tests/test_protected_paths.py tests/test_protected_paths_mode.py tests/test_request_id.py \
  tests/test_security.py tests/test_server_factory.py tests/test_ticket_properties.py \
  tests/test_token_cli.py tests/test_traces.py tests/test_transfer_dispatch.py \
  tests/test_transfer_endpoints.py tests/test_transfer_integration.py \
  tests/test_transfer_tickets.py tests/test_transfer_tools.py tests/test_version.py
```

- [ ] **Step 2b: Verify no *kept* test imports a deleted module**

Run:
```bash
grep -rlE "mymcp\.(server|mcp_server|tools|auth|transfer|cli|tool_definitions)\b|from mymcp import audit\b|import mymcp\.audit\b" \
  tests/ --include='*.py' | grep -v __pycache__
```
Expected: **empty**. Any file listed either (a) was missed above — delete it, or (b) is a kept recorder test that still references the core — fix it. Known survivors to check: `tests/recorder/test_last_success_gauge.py`, `tests/recorder/test_wiring_gauges.py`, `tests/recorder/test_wiring.py` — Part A migrated these to `wiring.set_active_supervisor`; if any still import `mymcp.tools`/`mcp_server`, patch or delete the offending assertion.

- [ ] **Step 3: Confirm the package still imports with only [recorder] deps**

Run:
```bash
.venv/bin/python -c "import mymcp, mymcp.config, mymcp.audit_output, mymcp.observability, mymcp.recorder, mymcp.recorder.__main__, mymcp.recorder.wiring; print('recorder-only package OK')"
```
Expected: `recorder-only package OK` (no ImportError for the deleted core).

- [ ] **Step 4: Run the surviving pytest suite**

Run: `.venv/bin/pytest tests/ -q --benchmark-disable -p no:randomly`
Expected: PASS. Surviving suites: `tests/recorder/**`, `tests/recorder/llm/**`, `tests/test_config_settings.py`, `tests/test_audit_output.py`, `tests/test_observability_*`, `tests/test_deploy_*`, `tests/test_packaging_recorder_extra.py`, `tests/test_assemble_wheel.py`. Fix collateral (e.g. a deploy test asserting the `mymcp` console entry) as flagged.

- [ ] **Step 5: mypy on the shrunken package**

Run: `.venv/bin/mypy src/mymcp`
Expected: clean (fewer files; fix any now-unused-ignore warnings).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat!: delete Python core (server/mcp_server/tools/transfer/auth/cli) — Go binary is the server"
```

### Task B3: Rewire CI (ci.yml)

**Files:** Modify `.github/workflows/ci.yml`

- [ ] **Step 1: Delete the `compat-python` job**

Remove the entire `compat-python:` job (lines ~92-118). There is no Python server to launch in v3. `compat-go` remains the compatibility gate (rename it to `compat` for clarity, updating any `needs:` references).

- [ ] **Step 2: Confirm `compat-go` builds & runs the Go server standalone**

`compat-go` already builds `go/cmd/mymcp` and runs `mymcp serve`. Verify it does not depend on the Python `mymcp` (it must `go build`/`go run` the binary, not `pip install`).

- [ ] **Step 3: Mutation jobs**

The `mutation-smoke` / `mutation-full` jobs run `mutmut` against `paths_to_mutate` (repointed in Task B1 to config/recorder). Confirm the job installs the `[dev,recorder]` extras so the recorder modules import. Update any `pip install -e ".[dev]"` to `pip install -e ".[dev,recorder]"` in the mutation + test jobs (base deps are now empty; recorder tests need the extra).

- [ ] **Step 4: `test` job extras**

Every job that runs pytest must install `[dev,recorder]` (base install now pulls nothing). Grep the workflow for `pip install -e` and append `,recorder` where a job runs recorder tests.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci!: drop compat-python (no Python server in v3); install [recorder] extra for tests"
```

### Task B4: Rewire release (release.yml) to publish platform wheels only

**Files:** Modify `.github/workflows/release.yml`

- [ ] **Step 1: Keep `build` (pure wheel is the assembler input), `build-binaries`, `assemble-wheels`.**

- [ ] **Step 2: Repoint `publish-pypi`** to depend on `assemble-wheels` and publish the platform wheels:

```yaml
  publish-pypi:
    runs-on: ubuntu-latest
    needs: assemble-wheels
    permissions:
      id-token: write
    environment: pypi
    steps:
      - uses: actions/download-artifact@v8
        with:
          pattern: platform-wheel-*
          path: platform-dist
          merge-multiple: true
      - uses: pypa/gh-action-pypi-publish@release/v1
        with:
          packages-dir: platform-dist
          skip-existing: true
```

(The pure wheel + sdist from `build` are **not** published — a pure wheel has no `mymcp` binary and would be a broken fallback. Only the two manylinux platform wheels go to PyPI.)

- [ ] **Step 3: Update `offline-bundle`** to bundle the amd64 **platform** wheel instead of the pure wheel:

```yaml
      - uses: actions/download-artifact@v8
        with:
          name: platform-wheel-amd64
          path: platform-dist
      ...
          pip download platform-dist/*.whl -d $BUNDLE/wheels \
              --python-version 3.11 --platform manylinux2014_x86_64 --only-binary=:all:
```

Adjust `needs:` to include `assemble-wheels`.

- [ ] **Step 4: Update `github-release`** `files:` to attach the platform wheels + offline bundle (download `platform-wheel-*` with `merge-multiple: true` instead of `dist`).

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "release!: publish platform wheels (Go binary as mymcp); stop publishing the pure wheel"
```

### Task B5: v3.0.0 docs

**Files:** `CHANGELOG.md`, `CLAUDE.md`, `README.md`

- [ ] **Step 1:** Add a `## 3.0.0` section to `CHANGELOG.md`: Go core is now the server; Python package is a recorder-only sidecar (`mymcp-recorder`); base install has zero deps; linux amd64/arm64 platform wheels; breaking: no Python server, `mymcp serve` is the Go binary; recorder requires `pip install "algony-mymcp[recorder]"`.
- [ ] **Step 2:** Update `CLAUDE.md`: the "Architecture" section now describes the Go server; the recorder section notes it is a standalone sidecar. Update the dev commands (Go build/test; `mymcp serve` is the Go binary).
- [ ] **Step 3:** Update `README.md` install/run instructions for v3.
- [ ] **Step 4:** `git commit -am "docs: v3.0.0"`.

### Task B6: Local end-to-end wheel validation (before any tag)

- [ ] **Step 1: Build the pure wheel + a real Go binary**

```bash
cd /home/zhu/repos/mymcp/go && CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
  go build -trimpath -ldflags "-s -w" -o /tmp/mymcp-linux-amd64 ./cmd/mymcp
cd /home/zhu/repos/mymcp && .venv/bin/python -m build --wheel -n
```

- [ ] **Step 2: Assemble + inspect the platform wheel**

```bash
.venv/bin/python scripts/assemble_wheel.py dist/*.whl /tmp/mymcp-linux-amd64 manylinux2014_x86_64 platform-dist/
python -m zipfile -l platform-dist/*.whl | grep -E "scripts/mymcp|entry_points|WHEEL"
```
Expected: `.data/scripts/mymcp` present; `entry_points.txt` has `mymcp-recorder` but **not** `mymcp = mymcp.cli:main`.

- [ ] **Step 3: Install into a throwaway venv and prove `mymcp` is the Go binary**

```bash
python -m venv /tmp/v3check && /tmp/v3check/bin/pip install platform-dist/*.whl
/tmp/v3check/bin/mymcp version          # runs the Go binary
/tmp/v3check/bin/pip show algony-mymcp | grep -i requires   # base deps: none
```
Expected: Go version string; `Requires:` empty. Then `/tmp/v3check/bin/pip install "algony-mymcp[recorder] @ platform-dist/*.whl"` pulls the recorder deps and exposes `mymcp-recorder`.

- [ ] **Step 4: Push branch, open PR, wait for CI green (lint, go, test matrix, compat-go, mutation-smoke, build/build-binaries/assemble-wheels). Squash-merge when green.**

---

# OPS RUNBOOK — user-driven cutover (after Part B merges + a `v3.0.0` tag)

These are the **user's** steps (interactive auth / production). Run them yourself via `! <cmd>` or on the ucloud box.

1. **TestPyPI dry run** — tag a pre-release (`v3.0.0rc1`) or run the release workflow against TestPyPI; `pip install -i https://test.pypi.org/... algony-mymcp` in a clean linux venv; confirm `mymcp version` runs and RSS is sane.
2. **Tag + release** — `git tag v3.0.0 && git push origin v3.0.0` triggers `release.yml` → platform wheels to PyPI + GitHub release with offline bundle.
3. **ucloud cutover** (reuses the existing `mymcp.service` unit — `ExecStart=... mymcp serve` still valid):
   ```
   pipx upgrade algony-mymcp        # or: pipx install --force algony-mymcp==3.0.0
   sudo systemctl restart mymcp
   systemctl status mymcp
   ```
4. **Verify RSS ≤ 20 MB**: `systemctl show mymcp -p MemoryCurrent` or `ps -o rss= -C mymcp`.
5. **Optional recorder sidecar**: `pipx inject algony-mymcp "algony-mymcp[recorder]"` (or a dedicated venv), install `mymcp-recorder.service` (template in `src/mymcp/recorder/templates/`), `systemctl enable --now mymcp-recorder`.
6. **Rollback**: `pipx install --force algony-mymcp==2.5.0 && sudo systemctl restart mymcp`.

---

## Self-Review

**Spec coverage:** ✅ zero base deps (B1) · drop Python `mymcp` entry (B1) · delete Python core (B2) · overview write-protection into Go (A1) · sever recorder couplings — `register_protected_path` (A2) + admin/auth router (A2) · adapt compat CI (B3) · bump 3.0.0 (B5) · wire assemble-wheels→publish (B4) · ops cutover + rollback (runbook). Deploy/: kept (stdlib-only, ships unit templates) — flagged as library-only, decision documented.

**Placeholder scan:** Go edit shows full function; pyproject/release edits show exact TOML/YAML; deletions are explicit `git rm` lists; every verification step has a concrete command + expected output. Task B2 Step 2b is a grep-verify with a defined "empty" expectation, not a TODO.

**Type consistency:** `ProtectedFromConfig` / `fsutil.ProtectedEntry{Pattern,Modes}` / `fsutil.ModeWrite` match the code read from `readfile.go`/`fsutil.go`. `wiring.set_active_supervisor` (Part A dependency) matches the merged M3b-p1 name. `scripts/assemble_wheel.py` CLI signature (`pure_wheel go_binary platform_tag out_dir`) matches the merged script.

**Open decision (flagged, not blocking):** mutmut repoint (B1 Step 4) assumes recorder tests kill mutants in config/cursor/events/merge_cycle. If `mutation-smoke` regresses, trim `paths_to_mutate` to the passing subset — verified live in B6/CI, not asserted blind.
