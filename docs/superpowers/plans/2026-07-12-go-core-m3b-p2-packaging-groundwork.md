# Go Core M3b — Phase 2: Packaging Groundwork

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lay the safe, additive groundwork for v3 binary-wheel packaging — populate the `[recorder]` extra with the sidecar's real dependencies (so a future zero-base-deps flip has somewhere to put them) and add a release workflow that cross-compiles the static Go binary for `linux/amd64` + `linux/arm64` — without touching the still-live Python core, its dependencies, or the `mymcp` entry point.

**Architecture:** Two independent, additive changes. (1) `pyproject.toml` gains a real `[recorder]` extra listing exactly what the `mymcp-recorder` sidecar imports (`httpx`, `anyio`, `pydantic-settings`, `python-json-logger`, `opentelemetry-api/sdk/exporter-prometheus`); the base `dependencies` stay unchanged. (2) `.github/workflows/release.yml` builds `CGO_ENABLED=0` static binaries for both target arches and uploads them as artifacts, on a `v*` tag or manual dispatch.

**Tech Stack:** Python packaging (setuptools + extras), GitHub Actions (`actions/setup-go`), Go cross-compilation (`GOOS`/`GOARCH`, `CGO_ENABLED=0`). Tests: pytest (pyproject introspection via `tomllib`).

**Spec:** `docs/superpowers/specs/2026-07-04-go-core-rewrite-design.md` — "Packaging and the Recorder Split".
**Predecessor:** M3b phase 1 merged as PR #72 (`mymcp-recorder` sidecar entry exists).
**Branch:** `feat/go-core-m3b-packaging` off master (create it).

**Why this is only "groundwork" (scope boundary):** Dropping the base `dependencies` to zero and making `mymcp` resolve to the bundled Go binary (removing the Python `mymcp = mymcp.cli:main` entry) **cannot** coexist with the still-present Python core — the test suite and the `compat-python` CI job import and boot the Python core, which needs `fastapi`/`mcp`/`uvicorn`/`opentelemetry`. Those flips, the wheel-assembly that bundles the binary as `mymcp`, and removing the Python core are one **atomic v3.0.0 breaking change = Phase 3**. This phase only adds things that are inert until then: an extra (base install unaffected) and a workflow that produces binary artifacts.

---

## File Map

```
pyproject.toml                     # MODIFY: populate the [recorder] extra (base deps untouched)
.github/workflows/release.yml      # CREATE: cross-compile static amd64+arm64 binaries → artifacts
tests/test_packaging_recorder_extra.py  # CREATE: assert the [recorder] extra lists the sidecar deps
CHANGELOG.md                       # MODIFY: Unreleased entry
```

---

## Task 0: Branch

- [ ] **Step 1: Create the branch off master**

```bash
cd /home/zhu/repos/mymcp
git checkout master && git pull
git checkout -b feat/go-core-m3b-packaging
```

---

## Task 1: Populate the `[recorder]` extra

**Files:** Modify `pyproject.toml`; create `tests/test_packaging_recorder_extra.py`

The sidecar's import graph (verified via `python -X importtime -c "import mymcp.recorder.__main__"`) pulls in `anyio`, `httpx`, `opentelemetry`, `pydantic`, `pydantic-settings` — and `python-json-logger` at runtime for structured logs. It does **not** import `fastapi`/`mcp`/`uvicorn` (those are the Python core / the dropped `/admin/overview` router). Populate the currently-empty `recorder` extra with these, reusing the base version floors. This is inert today (base still lists them) but is where they live once Phase 3 zeroes the base deps.

- [ ] **Step 1: Write the failing test** — create `tests/test_packaging_recorder_extra.py`:

```python
import tomllib


def _pyproject() -> dict:
    with open("pyproject.toml", "rb") as f:
        return tomllib.load(f)


def test_recorder_extra_lists_sidecar_dependencies():
    extras = _pyproject()["project"]["optional-dependencies"]
    recorder = extras["recorder"]
    names = {req.split(">")[0].split("=")[0].split("[")[0].strip().lower() for req in recorder}
    # Exactly what `import mymcp.recorder.__main__` needs to run standalone.
    required = {
        "httpx",
        "anyio",
        "pydantic-settings",
        "python-json-logger",
        "opentelemetry-api",
        "opentelemetry-sdk",
        "opentelemetry-exporter-prometheus",
    }
    missing = required - names
    assert not missing, f"[recorder] extra missing: {missing}"
    # The sidecar must NOT drag in the Python-core web stack.
    assert "fastapi" not in names and "uvicorn" not in names and "mcp" not in names


def test_recorder_alias_extras_still_present():
    # Kept (possibly empty) so old install commands don't break.
    extras = _pyproject()["project"]["optional-dependencies"]
    assert "recorder-anthropic" in extras
    assert "recorder-openai" in extras
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_packaging_recorder_extra.py -q`
Expected: FAIL (`recorder` extra is empty; `required - names` is non-empty).

- [ ] **Step 3: Populate the extra** in `pyproject.toml`. Replace the empty `recorder = []` line with:

```toml
recorder = [
    "httpx>=0.27.0",
    "anyio>=4.0.0",
    "pydantic-settings>=2.0",
    "python-json-logger>=2.0",
    "opentelemetry-api>=1.41.0",
    "opentelemetry-sdk>=1.41.0",
    "opentelemetry-exporter-prometheus>=0.62b0",
]
```

Leave `recorder-anthropic = []` and `recorder-openai = []` unchanged (compat aliases). Leave the base `dependencies` unchanged.

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/test_packaging_recorder_extra.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml tests/test_packaging_recorder_extra.py
git commit -m "feat(packaging): populate the [recorder] extra with the sidecar's deps"
```

---

## Task 2: Cross-compile release workflow

**Files:** Create `.github/workflows/release.yml`

Cross-compile static (`CGO_ENABLED=0`) binaries for both target arches and upload them. Triggered by a `v*` tag or manual dispatch. This does not publish anything — it produces the binaries the Phase 3 wheel-assembly will consume, and proves the build matrix works. (Locally verified: both arches build static ELF binaries and the amd64 one runs `mymcp version`.)

- [ ] **Step 1: Create `.github/workflows/release.yml`**

```yaml
name: Release binaries

on:
  push:
    tags: ["v*"]
  workflow_dispatch:

permissions:
  contents: read

jobs:
  build-binaries:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        goarch: [amd64, arm64]
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-go@v6
        with:
          go-version: "1.25"
          cache-dependency-path: go/go.sum
      - name: cross-compile static binary
        working-directory: go
        env:
          CGO_ENABLED: "0"
          GOOS: linux
          GOARCH: ${{ matrix.goarch }}
        run: |
          go build -trimpath -ldflags "-s -w" -o "mymcp-linux-${{ matrix.goarch }}" ./cmd/mymcp
          file "mymcp-linux-${{ matrix.goarch }}"
          test "$(go env GOOS)" = "linux"
      - name: verify amd64 binary runs
        if: matrix.goarch == 'amd64'
        run: ./go/mymcp-linux-amd64 version
      - uses: actions/upload-artifact@v4
        with:
          name: mymcp-linux-${{ matrix.goarch }}
          path: go/mymcp-linux-${{ matrix.goarch }}
          if-no-files-found: error
```

- [ ] **Step 2: Validate the workflow YAML**

Run: `.venv/bin/python -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml')); print('release.yml OK')"`
Expected: `release.yml OK`.

- [ ] **Step 3: Validate the build commands locally** (the exact commands the job runs)

Run:
```bash
cd go && for a in amd64 arm64; do CGO_ENABLED=0 GOOS=linux GOARCH=$a go build -trimpath -ldflags "-s -w" -o /tmp/mymcp-linux-$a ./cmd/mymcp && file /tmp/mymcp-linux-$a | grep -q "statically linked" && echo "$a OK"; done && cd ..
```
Expected: `amd64 OK` and `arm64 OK`.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci: release workflow cross-compiles static linux amd64+arm64 binaries"
```

---

## Task 3: Verify no regressions + CHANGELOG + PR

- [ ] **Step 1: Full Python suite + lint** (the extra + workflow must not disturb anything)

Run:
```bash
.venv/bin/pytest tests/ -q --benchmark-disable
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy src/mymcp
```
Expected: all green.

- [ ] **Step 2: Confirm base install is unchanged.** The base `dependencies` array in `pyproject.toml` is byte-identical to master; only the `recorder` extra changed.

Run: `git diff master -- pyproject.toml`
Expected: the diff touches only the `recorder = [...]` extra (no change to `dependencies = [...]`).

- [ ] **Step 3: CHANGELOG entry** — under `## [Unreleased]` → `### Added`:

```markdown
- Go core M3b (phase 2): the `[recorder]` extra now lists the sidecar's real
  dependencies (httpx, anyio, pydantic-settings, python-json-logger,
  opentelemetry), and a `release.yml` workflow cross-compiles static
  linux/amd64 + linux/arm64 binaries — groundwork for the v3 binary wheels.
```

- [ ] **Step 4: Push + PR**

```bash
git add CHANGELOG.md
git commit -m "docs: changelog for M3b phase 2 (packaging groundwork)"
git push -u origin feat/go-core-m3b-packaging
gh pr create --title "Go core M3b (phase 2) — packaging groundwork ([recorder] extra + release binaries)" \
  --body "$(cat <<'EOF'
Second phase of **M3b** — additive packaging groundwork, no breaking change.

- `[recorder]` extra populated with exactly what `import mymcp.recorder.__main__` needs (httpx, anyio, pydantic-settings, python-json-logger, opentelemetry-api/sdk/exporter-prometheus). Base `dependencies` untouched, so this is inert until Phase 3 zeroes them.
- `release.yml`: cross-compiles `CGO_ENABLED=0` static binaries for linux/amd64 + linux/arm64 on a `v*` tag or manual dispatch, uploaded as artifacts. Verified locally (both arches build static ELF; amd64 runs `mymcp version`).

Deferred to **Phase 3 (the atomic v3.0.0)**: zeroing the base deps, the wheel-assembly that bundles the binary as `mymcp`, removing the Python core, and publishing to PyPI + the ucloud cutover — all of which are entangled and breaking.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 5: Confirm CI green** — all existing jobs (`test`, `lint`, `compat-python`, `compat-go`, `go`, `security-audit`, `build`, `mutation-smoke`) stay green. `release.yml` only runs on tags/dispatch, so it won't run on this PR — that's expected.

---

## Self-Review

**1. Spec coverage (this phase):** `[recorder]` extra with real deps — Task 1. Cross-compile pipeline for both arches — Task 2. Deferred + labeled: zero base deps, wheel assembly (binary-as-`mymcp`), no-sdist, PyPI publish, core removal, cutover — Phase 3 / ops.

**2. Placeholder scan:** every step is concrete — the extra block, the full workflow YAML, and exact verification commands are all shown.

**3. Type consistency:** the `[recorder]` package names in Task 1's edit match the `required` set asserted in Task 1's test; the workflow artifact names (`mymcp-linux-${goarch}`) and build output paths are consistent within Task 2.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-12-go-core-m3b-p2-packaging-groundwork.md`. Two execution options:

**1. Subagent-Driven (recommended).**
**2. Inline Execution** (how M2/M3a/M3b-p1 shipped).

Which approach?
