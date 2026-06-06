# Version Info Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the deployed application version via `GET /version` and `GET /health`, with `upgrade.sh` writing the authoritative version at deploy time.

**Architecture:** A git-tracked `VERSION` file serves as the dev fallback. `upgrade.sh` writes `APP_DIR/VERSION` on successful deploy. `config.py` reads from `APP_DIR/VERSION` first, then falls back to the repo `VERSION`, then `"unknown"`. Two HTTP endpoints (`/version`, updated `/health`) expose the value with no authentication.

**Tech Stack:** Python, FastAPI, bash (upgrade.sh)

---

### Task 1: Add git-tracked VERSION file and config.py `_read_version()`

**Files:**
- Create: `VERSION`
- Modify: `config.py`
- Test: `tests/test_version.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_version.py`:

```python
import os
import pytest
from unittest.mock import patch


def test_read_version_app_dir_takes_priority(tmp_path):
    app_version_file = tmp_path / "VERSION"
    app_version_file.write_text("2.0.0\n")
    repo_version_file = tmp_path / "repo_VERSION"
    repo_version_file.write_text("1.0.0\n")
    with patch("config.APP_DIR", str(tmp_path)), \
         patch("config._VERSION_FILE", str(repo_version_file)):
        import importlib
        import config
        importlib.reload(config)
        assert config.APP_VERSION == "2.0.0"


def test_read_version_falls_back_to_repo(tmp_path):
    repo_version_file = tmp_path / "VERSION"
    repo_version_file.write_text("1.1.0\n")
    missing_app_dir = str(tmp_path / "nonexistent")
    with patch("config.APP_DIR", missing_app_dir), \
         patch("config._VERSION_FILE", str(repo_version_file)):
        import importlib
        import config
        importlib.reload(config)
        assert config.APP_VERSION == "1.1.0"


def test_read_version_falls_back_to_unknown(tmp_path):
    missing_app_dir = str(tmp_path / "nonexistent")
    missing_repo = str(tmp_path / "noVERSION")
    with patch("config.APP_DIR", missing_app_dir), \
         patch("config._VERSION_FILE", missing_repo):
        import importlib
        import config
        importlib.reload(config)
        assert config.APP_VERSION == "unknown"


def test_read_version_strips_whitespace(tmp_path):
    app_version_file = tmp_path / "VERSION"
    app_version_file.write_text("  1.2.3  \n")
    with patch("config.APP_DIR", str(tmp_path)), \
         patch("config._VERSION_FILE", str(tmp_path / "nofile")):
        import importlib
        import config
        importlib.reload(config)
        assert config.APP_VERSION == "1.2.3"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_version.py -v --benchmark-disable
```

Expected: `ImportError` or `AttributeError` — `_VERSION_FILE` and `APP_VERSION` not yet defined in `config.py`.

- [ ] **Step 3: Create `VERSION` file (repo root)**

```
1.1.0
```

- [ ] **Step 4: Add `_read_version()` and `APP_VERSION` to `config.py`**

Add after the existing `APP_DIR` line (line 37 in the current file, after `APP_DIR = os.getenv("MCP_APP_DIR", "/opt/mymcp")`):

```python
_VERSION_FILE = os.path.join(os.path.dirname(__file__), "VERSION")


def _read_version() -> str:
    for path in [os.path.join(APP_DIR, "VERSION"), _VERSION_FILE]:
        try:
            with open(path) as f:
                return f.read().strip()
        except OSError:
            pass
    return "unknown"


APP_VERSION: str = _read_version()
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_version.py -v --benchmark-disable
```

Expected: all 4 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add VERSION config.py tests/test_version.py
git commit -m "feat(version): add VERSION file and config._read_version()"
```

---

### Task 2: Add `/version` endpoint and update `/health`

**Files:**
- Modify: `main.py`
- Test: `tests/test_version.py` (add cases)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_version.py`:

```python
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch
from auth import TokenStore


@pytest.fixture
def store(tmp_path):
    return TokenStore(str(tmp_path / "tokens.json"), "adm_testadmin")


@pytest.fixture
def versioned_app(store):
    import auth
    original = auth._store
    auth._store = store
    try:
        from main import app
        yield app
    finally:
        auth._store = original


@pytest.mark.anyio
async def test_get_version_returns_200(versioned_app):
    transport = ASGITransport(app=versioned_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("config.APP_VERSION", "1.2.3"):
            resp = await client.get("/version")
    assert resp.status_code == 200
    assert resp.json() == {"version": "1.2.3"}


@pytest.mark.anyio
async def test_get_version_no_auth_required(versioned_app):
    transport = ASGITransport(app=versioned_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/version")
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_health_includes_version(versioned_app):
    transport = ASGITransport(app=versioned_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("config.APP_VERSION", "2.0.0"):
            resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"] == "2.0.0"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_version.py::test_get_version_returns_200 tests/test_version.py::test_health_includes_version -v --benchmark-disable
```

Expected: `AssertionError` — `/version` returns 404, `/health` has no `version` field.

- [ ] **Step 3: Update `main.py`**

Add `Response` to the existing starlette imports line:
```python
from starlette.responses import JSONResponse, Response
```

Replace the existing `/health` endpoint:
```python
@app.get("/health")
async def health():
    return {"status": "ok", "version": config.APP_VERSION}
```

Add `/version` endpoint after `/health`:
```python
@app.get("/version")
async def version():
    return {"version": config.APP_VERSION}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_version.py -v --benchmark-disable
```

Expected: all 7 tests PASS. Also run the existing health test:

```bash
python3 -m pytest tests/test_main.py -v --benchmark-disable
```

The existing `test_health_endpoint` will fail because it checks `{"status": "ok"}` exactly — update that assertion:

In `tests/test_main.py`, find `test_health_endpoint` and update the assertion:
```python
async def test_health_endpoint(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "version" in body
```

- [ ] **Step 5: Run full test suite**

```bash
python3 -m pytest tests/ -v --benchmark-disable
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add main.py tests/test_version.py tests/test_main.py
git commit -m "feat(version): add /version endpoint and version field to /health"
```

---

### Task 3: Update `upgrade.sh` to write `APP_DIR/VERSION`

**Files:**
- Modify: `deploy/upgrade.sh`

The spec requires:
- On success with a specific tag (e.g. `v1.1.0`): write `1.1.0` (strip leading `v`) to `$APP_DIR/VERSION`
- On success with HEAD/latest: write `git describe --tags --always` output to `$APP_DIR/VERSION`
- On rollback tier2 (rsync restore): the VERSION file is automatically restored from the backup — no extra logic needed

- [ ] **Step 1: Find the success block in upgrade.sh**

The success block is around line 486-495 (after all `step_*` functions succeed). It currently writes `.install-info` at line 487-490. Add VERSION writing right after `.install-info`:

```bash
    # Write deployed version for /version endpoint
    if [[ "$TARGET_VERSION" =~ ^v?[0-9]+\.[0-9]+\.[0-9] ]]; then
        # Strip leading 'v' from tagged versions
        printf '%s\n' "${TARGET_VERSION#v}" > "$APP_DIR/VERSION"
    else
        # HEAD/branch/commit: use git describe for a human-readable string
        git -C "$APP_DIR" describe --tags --always > "$APP_DIR/VERSION"
    fi
```

In `upgrade.sh`, find this block (around line 486-495):

```bash
    write_state "$APP_DIR" "done" "$CURRENT_VERSION" "$TARGET_VERSION"
    # Write .install-info for audit / fallback version detection
    printf '{"version":"%s","installed_at":"%s","upgraded_from":"%s"}\n' \
        "$TARGET_VERSION" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$CURRENT_VERSION" \
        > "$APP_DIR/.install-info"
    prune_backups "$APP_DIR" "$KEEP_BACKUPS"
```

Add the VERSION writing block between `.install-info` write and `prune_backups`:

```bash
    write_state "$APP_DIR" "done" "$CURRENT_VERSION" "$TARGET_VERSION"
    # Write .install-info for audit / fallback version detection
    printf '{"version":"%s","installed_at":"%s","upgraded_from":"%s"}\n' \
        "$TARGET_VERSION" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$CURRENT_VERSION" \
        > "$APP_DIR/.install-info"
    # Write deployed version for /version endpoint
    if [[ "$TARGET_VERSION" =~ ^v?[0-9]+\.[0-9]+\.[0-9] ]]; then
        printf '%s\n' "${TARGET_VERSION#v}" > "$APP_DIR/VERSION"
    else
        git -C "$APP_DIR" describe --tags --always > "$APP_DIR/VERSION"
    fi
    prune_backups "$APP_DIR" "$KEEP_BACKUPS"
```

- [ ] **Step 2: Verify the bats tests still pass**

```bash
bats tests/test_upgrade.bats tests/test_install.bats
```

Expected: all existing bats tests PASS (no bats test covers VERSION writing, so no test changes needed here).

- [ ] **Step 3: Commit**

```bash
git add deploy/upgrade.sh
git commit -m "feat(version): write APP_DIR/VERSION on successful upgrade"
```

---

### Task 4: Final verification

- [ ] **Step 1: Run all Python tests**

```bash
python3 -m pytest tests/ -v --benchmark-disable
```

Expected: all tests PASS.

- [ ] **Step 2: Manual smoke test**

```bash
python3 main.py &
sleep 1
curl http://localhost:8765/version
curl http://localhost:8765/health
kill %1
```

Expected output:
```json
{"version": "1.1.0"}
{"status": "ok", "version": "1.1.0"}
```

- [ ] **Step 3: Push branch and open PR**

```bash
git push origin version-and-metrics
gh pr create --title "feat: version info and Prometheus monitoring" --body "Implements GET /version, GET /health version field, upgrade.sh VERSION writing, and Prometheus /metrics endpoint."
```

(Or wait until Prometheus task is also done before creating PR.)
