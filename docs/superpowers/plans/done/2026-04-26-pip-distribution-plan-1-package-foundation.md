# mymcp 2.0 Plan 1: Package Foundation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the flat-layout repo into a pip-installable `mymcp` package with `mymcp serve` CLI, pydantic-settings config, MYMCP_-prefixed env vars, ruff/mypy linting, CI matrix on Python 3.11/3.12/3.13, and bash-tool SIGTERM cleanup.

**Architecture:** Move all source into `src/mymcp/` (src layout). Replace bare `import config` style with `from mymcp import config`. Split `main.py` into a side-effect-free `server.py` (FastAPI app factory) and a `cli.py` argparse entrypoint that owns logging configuration and signal handling. Rewrite `config.py` as a `pydantic_settings.BaseSettings` subclass with `env_prefix="MYMCP_"`. Bash tool tracks spawned children via a thread-safe weakref set and a top-level signal handler kills the process group on SIGTERM/SIGINT.

**Tech Stack:** Python 3.11+, setuptools + setuptools-scm, FastAPI, uvicorn, pydantic-settings, ruff, mypy, pre-commit, pytest, GitHub Actions.

**Source spec:** `docs/superpowers/specs/2026-04-26-pip-distribution-design.md` (§§1-3, §7, parts of §6).

**Out of scope (deferred to Plan 2/3):** `install-service`, `uninstall-service`, `token`, `migrate-from-legacy`, `doctor` subcommands; PyPI publish; offline bundle; release.yml.

---

## File Structure

**Create:**
- `src/mymcp/__init__.py` — exposes `__version__` (resolved from installed metadata or `_version.py`)
- `src/mymcp/__main__.py` — `python -m mymcp` → `mymcp.cli:main`
- `src/mymcp/cli.py` — argparse, logging config, signal handlers, `serve` and `version` subcommands
- `src/mymcp/server.py` — FastAPI app factory (`create_app()`), middleware, route mounting; no module-level side effects
- `src/mymcp/tools/__init__.py` — empty marker
- `.pre-commit-config.yaml`
- `.github/workflows/ci.yml`

**Move (with import rewrites):**
- `mcp_server.py` → `src/mymcp/mcp_server.py`
- `auth.py` → `src/mymcp/auth.py`
- `audit.py` → `src/mymcp/audit.py`
- `metrics.py` → `src/mymcp/metrics.py`
- `tools/bash.py` → `src/mymcp/tools/bash.py` (also: SIGTERM cleanup)
- `tools/files.py` → `src/mymcp/tools/files.py`

**Rewrite:**
- `pyproject.toml` — replace mutmut-only stub with full `[build-system]` + `[project]` + tool configs
- `config.py` → `src/mymcp/config.py` as pydantic-settings `Settings` class with `MYMCP_` prefix
- `main.py` → split: app factory becomes `src/mymcp/server.py`, CLI lives in `src/mymcp/cli.py`. Original `main.py` deleted.

**Delete:**
- `main.py`, `mcp_server.py`, `auth.py`, `audit.py`, `metrics.py`, `config.py` (old top-level copies after move)
- `tools/bash.py`, `tools/files.py`, `tools/__init__.py` (old top-level copies)
- `VERSION` (replaced by setuptools-scm)
- `requirements.txt`, `requirements-dev.txt` (replaced by `[project] dependencies` and `[project.optional-dependencies] dev`)
- `tokens.json` at repo root (test artifact)

**Modify (test imports + env-var names):**
- All `tests/test_*.py` — bare imports → `from mymcp.<module> import …`; `MCP_*` env names → `MYMCP_*`
- `CLAUDE.md` — update Commands section

---

## Branch and prerequisites

This plan continues on branch `spec/pip-distribution-2.0` (already pushed with the spec commit) and adds implementation commits on top. **Do not** rebase onto master mid-plan. Open a PR at the end of the plan.

Before starting, run from the repo root:

```bash
git status                                         # expect clean
git rev-parse --abbrev-ref HEAD                    # expect: spec/pip-distribution-2.0
python3 -m pytest tests/ -v --benchmark-disable    # baseline: existing tests pass
```

If the baseline test run fails on master, **stop and report** — do not attempt the plan against a broken baseline.

---

### Task 1: Stage repo for restructure

**Files:**
- Create: `src/`, `src/mymcp/`, `src/mymcp/tools/`

- [ ] **Step 1: Create the src layout directories**

```bash
mkdir -p src/mymcp/tools src/mymcp/deploy/templates
```

- [ ] **Step 2: Verify directory tree**

```bash
ls -la src/mymcp src/mymcp/tools
```
Expected: empty directories exist.

- [ ] **Step 3: Commit the empty layout**

```bash
git add src/
git commit -m "chore: scaffold src/mymcp/ package layout"
```

(Empty directories don't commit by themselves — this commit will be a no-op until Task 2 adds files. Skip the commit and combine with Task 2.)

---

### Task 2: Move modules into the package (mechanical, no logic changes)

**Files:**
- Move: `mcp_server.py` → `src/mymcp/mcp_server.py`
- Move: `auth.py` → `src/mymcp/auth.py`
- Move: `audit.py` → `src/mymcp/audit.py`
- Move: `metrics.py` → `src/mymcp/metrics.py`
- Move: `tools/bash.py` → `src/mymcp/tools/bash.py`
- Move: `tools/files.py` → `src/mymcp/tools/files.py`
- Move: `tools/__init__.py` → `src/mymcp/tools/__init__.py`

- [ ] **Step 1: Move with git so history is preserved**

```bash
git mv mcp_server.py src/mymcp/mcp_server.py
git mv auth.py src/mymcp/auth.py
git mv audit.py src/mymcp/audit.py
git mv metrics.py src/mymcp/metrics.py
git mv tools/bash.py src/mymcp/tools/bash.py
git mv tools/files.py src/mymcp/tools/files.py
git mv tools/__init__.py src/mymcp/tools/__init__.py
rmdir tools
```

- [ ] **Step 2: Verify tree**

```bash
find src/mymcp -type f -name '*.py' | sort
```
Expected output:
```
src/mymcp/audit.py
src/mymcp/auth.py
src/mymcp/mcp_server.py
src/mymcp/metrics.py
src/mymcp/tools/__init__.py
src/mymcp/tools/bash.py
src/mymcp/tools/files.py
```

- [ ] **Step 3: Commit (do not yet rewrite imports — next task)**

```bash
git commit -m "chore: move source modules under src/mymcp/"
```

---

### Task 3: Create package `__init__.py` and `__main__.py`

**Files:**
- Create: `src/mymcp/__init__.py`
- Create: `src/mymcp/__main__.py`

- [ ] **Step 1: Write `src/mymcp/__init__.py`**

```python
"""mymcp — MCP server for Linux system control."""
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("mymcp")
except PackageNotFoundError:
    try:
        from mymcp._version import __version__  # generated by setuptools-scm
    except ImportError:
        __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
```

- [ ] **Step 2: Write `src/mymcp/__main__.py`**

```python
"""Allow `python -m mymcp` to invoke the CLI."""
import sys

from mymcp.cli import main

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: Commit**

```bash
git add src/mymcp/__init__.py src/mymcp/__main__.py
git commit -m "feat: add mymcp package entry points"
```

---

### Task 4: Rewrite intra-package imports

**Files:**
- Modify: `src/mymcp/mcp_server.py:11-15`
- Modify: `src/mymcp/audit.py:7`
- Modify: `src/mymcp/auth.py:88` (the lazy `import config` inside `get_store`)
- Modify: `src/mymcp/tools/bash.py:2`
- Modify: `src/mymcp/tools/files.py:6`

- [ ] **Step 1: Edit `src/mymcp/mcp_server.py` lines 11-15**

Replace:
```python
import config
import metrics
from audit import log_tool_call
from tools.bash import run_bash_execute
from tools.files import read_file, write_file, edit_file, glob_files, grep_files
```
With:
```python
from mymcp import config
from mymcp import metrics
from mymcp.audit import log_tool_call
from mymcp.tools.bash import run_bash_execute
from mymcp.tools.files import read_file, write_file, edit_file, glob_files, grep_files
```

- [ ] **Step 2: Edit `src/mymcp/audit.py` line 7**

Replace `import config` with `from mymcp import config`.

- [ ] **Step 3: Edit `src/mymcp/auth.py` line 88**

Replace the lazy `import config` inside `get_store()` with `from mymcp import config`.

- [ ] **Step 4: Edit `src/mymcp/tools/bash.py` line 2**

Replace `import config` with `from mymcp import config`.

- [ ] **Step 5: Edit `src/mymcp/tools/files.py` line 6**

Replace `import config` with `from mymcp import config`.

- [ ] **Step 6: Move `config.py` into the package as a placeholder**

```bash
git mv config.py src/mymcp/config.py
```

(Will be rewritten in Task 7. The move now keeps imports valid.)

- [ ] **Step 7: Verify imports parse**

```bash
python3 -c "import sys; sys.path.insert(0, 'src'); from mymcp import mcp_server, auth, audit, metrics, config; from mymcp.tools import bash, files; print('OK')"
```
Expected: `OK`

- [ ] **Step 8: Commit**

```bash
git add -u
git commit -m "refactor: rewrite intra-package imports to mymcp.* form"
```

---

### Task 5: Write `pyproject.toml` (full metadata)

**Files:**
- Modify: `pyproject.toml` (replace contents wholesale)

- [ ] **Step 1: Replace `pyproject.toml` with the full configuration**

Write exactly:

```toml
[build-system]
requires = ["setuptools>=64", "setuptools-scm>=8"]
build-backend = "setuptools.build_meta"

[project]
name = "mymcp"
dynamic = ["version"]
description = "Linux system control MCP server over Streamable HTTP with Bearer token auth"
readme = "README.md"
requires-python = ">=3.11"
license = "Apache-2.0"
authors = [{name = "algony-tony", email = "txzhu1010@gmail.com"}]
keywords = ["mcp", "model-context-protocol", "linux", "remote-control", "fastapi"]
classifiers = [
    "Development Status :: 4 - Beta",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Operating System :: POSIX :: Linux",
    "License :: OSI Approved :: Apache Software License",
    "Topic :: System :: Systems Administration",
    "Framework :: FastAPI",
]
dependencies = [
    "mcp>=1.0.0",
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "python-multipart>=0.0.9",
    "httpx>=0.27.0",
    "anyio>=4.0.0",
    "prometheus-client>=0.20.0",
    "pydantic-settings>=2.0",
    "python-json-logger>=2.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-anyio",
    "pytest-benchmark",
    "ruff>=0.6",
    "mypy>=1.11",
    "pre-commit>=3.7",
    "mutmut",
    "build",
]

[project.urls]
Homepage = "https://github.com/algony-tony/mymcp"
Repository = "https://github.com/algony-tony/mymcp"
Issues = "https://github.com/algony-tony/mymcp/issues"
Changelog = "https://github.com/algony-tony/mymcp/blob/master/CHANGELOG.md"

[project.scripts]
mymcp = "mymcp.cli:main"

[tool.setuptools]
package-dir = {"" = "src"}

[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools.package-data]
mymcp = ["deploy/templates/*.in"]

[tool.setuptools_scm]
write_to = "src/mymcp/_version.py"
fallback_version = "0.0.0+unknown"

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP", "SIM"]
ignore = ["B008"]   # FastAPI Depends() in defaults is idiomatic

[tool.mypy]
python_version = "3.11"
files = ["src/mymcp"]
strict = false
warn_unused_ignores = true
warn_return_any = true
no_implicit_optional = true

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "--benchmark-disable"

[tool.mutmut]
paths_to_mutate = "src/mymcp/auth.py,src/mymcp/audit.py,src/mymcp/config.py,src/mymcp/tools/bash.py,src/mymcp/tools/files.py,src/mymcp/mcp_server.py"
tests_dir = "tests/"
runner = "python3 -m pytest tests/ -x -q --tb=no --no-header --benchmark-disable"
```

Note: `mypy strict = false` initially — we will tighten later. `strict` mode against the existing codebase would flag dozens of issues unrelated to this plan.

- [ ] **Step 2: Verify pyproject parses and an editable install works**

```bash
python3 -m pip install --upgrade pip build
python3 -m pip install -e ".[dev]"
```
Expected: install succeeds; `mymcp` command resolves but errors because `cli.py` doesn't exist yet (next tasks).

```bash
which mymcp && mymcp --version 2>&1 | head -1
```
Expected: path printed, then a Python ImportError mentioning `mymcp.cli`. That's fine — proves the entry point is wired.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "build: full pyproject.toml with project metadata and tool config"
```

---

### Task 6: Test that the new config schema loads MYMCP_ env vars

**Files:**
- Create: `tests/test_config_settings.py`

This is the failing test for Task 7's pydantic-settings rewrite.

- [ ] **Step 1: Write `tests/test_config_settings.py`**

```python
import importlib

import pytest


def _reload_config(monkeypatch, env: dict[str, str]):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import mymcp.config
    importlib.reload(mymcp.config)
    return mymcp.config


def test_settings_reads_mymcp_prefixed_vars(monkeypatch, tmp_path):
    monkeypatch.delenv("MYMCP_ENV_FILE", raising=False)
    cfg = _reload_config(monkeypatch, {
        "MYMCP_HOST": "127.0.0.1",
        "MYMCP_PORT": "9000",
        "MYMCP_ADMIN_TOKEN": "tok_abc",
        "MYMCP_AUDIT_ENABLED": "true",
        "MYMCP_AUDIT_LOG_DIR": str(tmp_path),
    })
    s = cfg.get_settings()
    assert s.host == "127.0.0.1"
    assert s.port == 9000
    assert s.admin_token == "tok_abc"
    assert s.audit_enabled is True
    assert s.audit_log_dir == str(tmp_path)


def test_settings_ignores_unprefixed_mcp_vars(monkeypatch):
    """Hard rename: legacy MCP_* must NOT be honored."""
    monkeypatch.delenv("MYMCP_ENV_FILE", raising=False)
    monkeypatch.delenv("MYMCP_HOST", raising=False)
    monkeypatch.setenv("MCP_HOST", "10.0.0.1")
    cfg = _reload_config(monkeypatch, {})
    s = cfg.get_settings()
    assert s.host != "10.0.0.1"
    assert s.host == "0.0.0.0"


def test_settings_metrics_token_empty_means_disabled(monkeypatch):
    monkeypatch.delenv("MYMCP_ENV_FILE", raising=False)
    monkeypatch.delenv("MYMCP_METRICS_TOKEN", raising=False)
    cfg = _reload_config(monkeypatch, {})
    s = cfg.get_settings()
    assert s.metrics_token == ""


def test_settings_protected_paths_includes_log_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("MYMCP_ENV_FILE", raising=False)
    log_dir = tmp_path / "audit"
    log_dir.mkdir()
    cfg = _reload_config(monkeypatch, {
        "MYMCP_AUDIT_LOG_DIR": str(log_dir),
    })
    paths = cfg.get_protected_paths()
    assert str(log_dir) in paths


def test_settings_extra_protected_paths(monkeypatch):
    monkeypatch.delenv("MYMCP_ENV_FILE", raising=False)
    cfg = _reload_config(monkeypatch, {
        "MYMCP_PROTECTED_PATHS": "/extra/one,/extra/two",
    })
    paths = cfg.get_protected_paths()
    assert "/extra/one" in paths
    assert "/extra/two" in paths
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_config_settings.py -v
```
Expected: all five tests fail (current `config.py` uses `os.getenv` with `MCP_` prefix and has no `get_settings()` or `get_protected_paths()` functions).

- [ ] **Step 3: Commit the failing test**

```bash
git add tests/test_config_settings.py
git commit -m "test: add failing tests for MYMCP_-prefixed pydantic-settings config"
```

---

### Task 7: Rewrite `config.py` with pydantic-settings + MYMCP_ prefix

**Files:**
- Modify: `src/mymcp/config.py` (full rewrite)

- [ ] **Step 1: Replace `src/mymcp/config.py` contents**

```python
"""mymcp configuration via pydantic-settings.

All settings come from MYMCP_-prefixed environment variables. An optional
.env file can be loaded — discovery order: MYMCP_ENV_FILE env var,
/etc/mymcp/.env, ./.env. Use get_settings() to retrieve the cached singleton.
"""
import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _discover_env_file() -> str | None:
    explicit = os.environ.get("MYMCP_ENV_FILE")
    if explicit and Path(explicit).is_file():
        return explicit
    for candidate in ("/etc/mymcp/.env", ".env"):
        if Path(candidate).is_file():
            return candidate
    return None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MYMCP_",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Server
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8765)

    # Auth
    admin_token: str = Field(default="")
    metrics_token: str = Field(default="")
    token_file: str = Field(default="/etc/mymcp/tokens.json")

    # bash_execute output limits
    bash_max_output_bytes: int = Field(default=102400)
    bash_max_output_bytes_hard: int = Field(default=1048576)

    # read_file limits
    read_file_default_limit: int = Field(default=2000)
    read_file_max_limit: int = Field(default=50000)
    read_file_max_line_bytes: int = Field(default=32768)

    # write_file / edit limits
    write_file_max_bytes: int = Field(default=10 * 1024 * 1024)
    edit_string_max_bytes: int = Field(default=1024 * 1024)

    # glob / grep limits
    glob_max_results: int = Field(default=1000)
    grep_default_max_results: int = Field(default=500)
    grep_max_results: int = Field(default=5000)

    # Audit
    audit_enabled: bool = Field(default=False)
    audit_log_dir: str = Field(default="/var/log/mymcp")
    audit_max_bytes: int = Field(default=10 * 1024 * 1024)
    audit_backup_count: int = Field(default=5)

    # Shutdown
    shutdown_grace_sec: int = Field(default=5)

    # Extra protected paths (CSV)
    protected_paths: str = Field(default="")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    env_file = _discover_env_file()
    if env_file:
        return Settings(_env_file=env_file)  # type: ignore[call-arg]
    return Settings()


def reset_settings_cache() -> None:
    """Test-only helper to force re-read of env vars/files."""
    get_settings.cache_clear()


def get_protected_paths() -> list[str]:
    """Always-protected paths the file tools must refuse access to.

    Composed from the audit log dir and any extras from MYMCP_PROTECTED_PATHS.
    """
    s = get_settings()
    paths: list[str] = [s.audit_log_dir]
    if s.protected_paths.strip():
        paths.extend(p.strip() for p in s.protected_paths.split(",") if p.strip())
    return paths


# Module-level convenience attributes for back-compat with existing call sites.
# These resolve lazily via __getattr__.
_LEGACY_ATTRS = {
    "HOST": "host",
    "PORT": "port",
    "ADMIN_TOKEN": "admin_token",
    "METRICS_TOKEN": "metrics_token",
    "TOKEN_FILE": "token_file",
    "BASH_MAX_OUTPUT_BYTES": "bash_max_output_bytes",
    "BASH_MAX_OUTPUT_BYTES_HARD": "bash_max_output_bytes_hard",
    "READ_FILE_DEFAULT_LIMIT": "read_file_default_limit",
    "READ_FILE_MAX_LIMIT": "read_file_max_limit",
    "READ_FILE_MAX_LINE_BYTES": "read_file_max_line_bytes",
    "WRITE_FILE_MAX_BYTES": "write_file_max_bytes",
    "EDIT_STRING_MAX_BYTES": "edit_string_max_bytes",
    "GLOB_MAX_RESULTS": "glob_max_results",
    "GREP_DEFAULT_MAX_RESULTS": "grep_default_max_results",
    "GREP_MAX_RESULTS": "grep_max_results",
    "AUDIT_ENABLED": "audit_enabled",
    "AUDIT_LOG_DIR": "audit_log_dir",
    "AUDIT_MAX_BYTES": "audit_max_bytes",
    "AUDIT_BACKUP_COUNT": "audit_backup_count",
}


def __getattr__(name: str):
    if name == "PROTECTED_PATHS":
        return get_protected_paths()
    if name == "APP_VERSION":
        from mymcp import __version__
        return __version__
    if name in _LEGACY_ATTRS:
        return getattr(get_settings(), _LEGACY_ATTRS[name])
    raise AttributeError(f"module 'mymcp.config' has no attribute {name!r}")
```

- [ ] **Step 2: Run the new config tests**

```bash
python3 -m pytest tests/test_config_settings.py -v
```
Expected: all five tests pass.

- [ ] **Step 3: Run the full test suite to catch regressions**

```bash
python3 -m pytest tests/ -v --benchmark-disable
```
Expected: tests that read `MCP_*` env vars or rely on old `config.APP_DIR` will fail. **These are intentional failures fixed in Task 8.** Note the failure list and proceed.

- [ ] **Step 4: Commit**

```bash
git add src/mymcp/config.py
git commit -m "refactor: rewrite config as pydantic-settings with MYMCP_ env prefix"
```

---

### Task 8: Update tests for MYMCP_ env vars and removed APP_DIR

**Files:**
- Modify: `tests/test_protected_paths.py`
- Modify: `tests/test_auth.py:125` (the `RuntimeError` match string)
- Modify: any other test using `MCP_*` env names or `config.APP_DIR`
- Modify: `tests/conftest.py` (if any) or test fixtures using `patch.multiple("config", ...)`

- [ ] **Step 1: Search for all tests touching old config**

```bash
grep -rn "MCP_\|config\.APP_DIR\|patch.*\"config\"" tests/
```
Record the matching files.

- [ ] **Step 2: Edit `tests/test_protected_paths.py`**

Replace any occurrence of `MCP_PROTECTED_PATHS`, `MCP_APP_DIR`, `MCP_AUDIT_LOG_DIR` with `MYMCP_PROTECTED_PATHS`, etc. Remove any assertion that depends on `APP_DIR` being in protected paths (it no longer is). Replace with assertion about audit_log_dir.

Example replacement pattern in this file:
```python
# Old:
"MCP_PROTECTED_PATHS": "/extra/one, /extra/two",
"MCP_APP_DIR": "/opt/mymcp",
"MCP_AUDIT_LOG_DIR": "/var/log/mymcp",
# New:
"MYMCP_PROTECTED_PATHS": "/extra/one, /extra/two",
"MYMCP_AUDIT_LOG_DIR": "/var/log/mymcp",
```
(Drop the `APP_DIR` line entirely.)

Adjust the assertions: any test checking that `/opt/mymcp` is in `PROTECTED_PATHS` should be removed or rewritten to check the audit dir.

- [ ] **Step 3: Edit `tests/test_auth.py:125`**

```python
# Old:
with pytest.raises(RuntimeError, match="MCP_ADMIN_TOKEN"):
# New:
with pytest.raises(RuntimeError, match="MYMCP_ADMIN_TOKEN"):
```

Also update `auth.py` line 90's error message (Task 11 will revisit if needed):
```python
# In src/mymcp/auth.py get_store():
raise RuntimeError("MYMCP_ADMIN_TOKEN environment variable is required")
```

- [ ] **Step 4: Update remaining test imports**

For each test file in the search results, replace bare imports:
```python
# Old:
from auth import TokenStore
from mcp_server import call_tool, list_tools
from tools.bash import run_bash_execute
from tools.files import read_file, ...
import config
# New:
from mymcp.auth import TokenStore
from mymcp.mcp_server import call_tool, list_tools
from mymcp.tools.bash import run_bash_execute
from mymcp.tools.files import read_file, ...
from mymcp import config
```

For `unittest.mock.patch.multiple("config", ...)`, change to `patch.multiple("mymcp.config", ...)`. Where the patch is setting attributes that are now driven by `Settings`, switch to setting the env var via `monkeypatch` and calling `mymcp.config.reset_settings_cache()`.

- [ ] **Step 5: Run the test suite**

```bash
python3 -m pytest tests/ -v --benchmark-disable
```
Expected: all tests pass (or remaining failures are listed and fixed inline before next commit).

- [ ] **Step 6: Commit**

```bash
git add -u tests/ src/mymcp/auth.py
git commit -m "test: migrate tests to mymcp.* imports and MYMCP_ env vars"
```

---

### Task 9: Test for FastAPI app factory in `server.py`

**Files:**
- Create: `tests/test_server_factory.py`

- [ ] **Step 1: Write the failing test**

```python
"""Server module must expose a side-effect-free `create_app()` factory."""


def test_create_app_returns_fastapi_instance(monkeypatch, tmp_path):
    monkeypatch.setenv("MYMCP_ADMIN_TOKEN", "tok_test")
    monkeypatch.setenv("MYMCP_TOKEN_FILE", str(tmp_path / "tokens.json"))
    monkeypatch.setenv("MYMCP_AUDIT_LOG_DIR", str(tmp_path / "audit"))
    (tmp_path / "audit").mkdir()
    monkeypatch.delenv("MYMCP_ENV_FILE", raising=False)

    from mymcp import config
    config.reset_settings_cache()

    from mymcp.server import create_app
    app = create_app()

    from fastapi import FastAPI
    assert isinstance(app, FastAPI)


def test_importing_server_does_not_configure_logging():
    """Importing mymcp.server must not call logging.basicConfig()."""
    import logging
    root = logging.getLogger()
    pre_handlers = list(root.handlers)
    pre_level = root.level

    import importlib
    import mymcp.server
    importlib.reload(mymcp.server)

    assert list(root.handlers) == pre_handlers
    assert root.level == pre_level
```

- [ ] **Step 2: Run to verify it fails**

```bash
python3 -m pytest tests/test_server_factory.py -v
```
Expected: ImportError on `from mymcp.server import create_app` (file does not yet exist).

- [ ] **Step 3: Commit failing test**

```bash
git add tests/test_server_factory.py
git commit -m "test: add failing test for mymcp.server.create_app factory"
```

---

### Task 10: Split `main.py` into `server.py` + minimal `cli.py`

**Files:**
- Create: `src/mymcp/server.py`
- Delete: `main.py`

- [ ] **Step 1: Read current `main.py`**

```bash
cat main.py
```
Note: contents to migrate are the FastAPI app, the `McpAuthMiddleware`, the `MetricsMiddleware`, the `lifespan` context manager, the `/health`, `/version`, `/metrics`, `/mcp/...` route mounting, and the import-time `logging.basicConfig` call (which must be **removed** here and **moved** to `cli.py` in Task 12).

- [ ] **Step 2: Write `src/mymcp/server.py` as a factory**

Skeleton (port the actual logic from `main.py`):

```python
"""FastAPI app factory for mymcp. No module-level side effects."""
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, Response

from mymcp import config, metrics
from mymcp.auth import admin_router, get_store
from mymcp.mcp_server import server, session_manager, _current_audit_info


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        async with session_manager.run():
            yield

    app = FastAPI(lifespan=lifespan)

    # --- Middlewares (port verbatim from old main.py) ---
    # McpAuthMiddleware
    # MetricsMiddleware
    # ... copy class definitions and add_middleware calls here ...

    # --- Routes ---
    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/version")
    async def version_route() -> dict[str, str]:
        from mymcp import __version__
        return {"version": __version__}

    @app.get("/metrics")
    async def get_metrics(request: Request):
        if not metrics.ENABLED:
            return JSONResponse(
                {"detail": "Metrics disabled: prometheus_client not installed"},
                status_code=503,
            )
        s = config.get_settings()
        if not s.metrics_token:
            return JSONResponse(
                {"detail": "Metrics disabled: MYMCP_METRICS_TOKEN not configured"},
                status_code=503,
            )
        auth_header = request.headers.get("Authorization", "")
        if auth_header != f"Bearer {s.metrics_token}":
            return Response(status_code=401)
        return Response(content=metrics.generate_latest(), media_type=metrics.CONTENT_TYPE_LATEST)

    app.include_router(admin_router)
    # MCP streamable HTTP mount: copy from old main.py
    # app.mount("/mcp", session_manager.app)  # adapt to actual mount point

    return app
```

**Important porting notes:**
- Copy `McpAuthMiddleware` and `MetricsMiddleware` class definitions verbatim into `server.py`, but rewrite any reference to module-level `app` so they're added with `app.add_middleware(...)` inside `create_app()`.
- Replace `config.METRICS_TOKEN` reads with `config.get_settings().metrics_token`.
- Do **not** call `logging.basicConfig` anywhere in this file.
- Do **not** create a module-level `app = FastAPI()` — only `create_app()` returns one.
- Preserve the existing `/mcp` mount path and any uvicorn-specific configuration that lived in old `main.py`.

- [ ] **Step 3: Delete the old `main.py`**

```bash
git rm main.py
```

- [ ] **Step 4: Run the server-factory tests**

```bash
python3 -m pytest tests/test_server_factory.py -v
```
Expected: both tests pass.

- [ ] **Step 5: Run full suite**

```bash
python3 -m pytest tests/ -v --benchmark-disable
```
Expected: pass. If `tests/test_main.py` referenced `from main import app`, update it to:
```python
from mymcp.server import create_app
app = create_app()
```

- [ ] **Step 6: Commit**

```bash
git add src/mymcp/server.py
git add -u
git commit -m "refactor: split main.py into mymcp.server factory (no import side-effects)"
```

---

### Task 11: Test for `mymcp serve` CLI

**Files:**
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

```python
"""CLI argparse parsing and entry-point behavior."""
import subprocess
import sys


def test_mymcp_version_flag():
    result = subprocess.run(
        [sys.executable, "-m", "mymcp", "--version"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0
    assert "mymcp" in result.stdout.lower()


def test_mymcp_version_subcommand():
    result = subprocess.run(
        [sys.executable, "-m", "mymcp", "version"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0
    assert "mymcp" in result.stdout.lower()


def test_mymcp_serve_help():
    result = subprocess.run(
        [sys.executable, "-m", "mymcp", "serve", "--help"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0
    assert "--env-file" in result.stdout
    assert "--host" in result.stdout
    assert "--port" in result.stdout
    assert "--log-level" in result.stdout
    assert "--log-format" in result.stdout


def test_mymcp_no_subcommand_shows_help():
    result = subprocess.run(
        [sys.executable, "-m", "mymcp"],
        capture_output=True, text=True, timeout=10,
    )
    # argparse exits 2 when required subcommand missing
    assert result.returncode == 2
    assert "usage:" in (result.stderr + result.stdout).lower()
```

- [ ] **Step 2: Run to verify failure**

```bash
python3 -m pytest tests/test_cli.py -v
```
Expected: ImportError on `from mymcp.cli import main` (file doesn't exist yet).

- [ ] **Step 3: Commit failing test**

```bash
git add tests/test_cli.py
git commit -m "test: add failing tests for mymcp CLI entry point"
```

---

### Task 12: Implement `cli.py` with `serve` and `version` subcommands

**Files:**
- Create: `src/mymcp/cli.py`

- [ ] **Step 1: Write `src/mymcp/cli.py`**

```python
"""mymcp CLI entry point."""
from __future__ import annotations

import argparse
import logging
import os
import secrets
import signal
import sys
from typing import Any

from mymcp import __version__


def _configure_logging(level: str, fmt: str) -> None:
    log_level = getattr(logging, level.upper(), logging.INFO)
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stderr)
    if fmt == "json":
        from pythonjsonlogger import jsonlogger
        handler.setFormatter(
            jsonlogger.JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
    root.addHandler(handler)
    root.setLevel(log_level)


def _install_signal_handlers() -> None:
    from mymcp.tools.bash import shutdown_inflight_processes

    def _handler(signum: int, _frame: Any) -> None:
        shutdown_inflight_processes()
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)


def _maybe_set_temp_tokens(with_metrics: bool) -> None:
    """When no env file is present and no tokens are configured, generate
    in-memory tokens and inject them via env vars before settings load."""
    from mymcp.config import _discover_env_file

    if _discover_env_file():
        return
    if os.environ.get("MYMCP_ADMIN_TOKEN"):
        return

    admin = "tok_" + secrets.token_hex(16)
    rw = "tok_" + secrets.token_hex(16)
    os.environ["MYMCP_ADMIN_TOKEN"] = admin
    print(f"[mymcp] temp admin token: {admin}", file=sys.stderr)
    print(f"[mymcp] temp rw token:    {rw}", file=sys.stderr)
    print("[mymcp] tokens are in-memory; they vanish on exit.", file=sys.stderr)

    metrics_token = ""
    if with_metrics:
        metrics_token = "tok_" + secrets.token_hex(16)
        os.environ["MYMCP_METRICS_TOKEN"] = metrics_token
        print(f"[mymcp] temp metrics token: {metrics_token}", file=sys.stderr)

    # Stash the rw token; server.py will register it into the in-memory store
    os.environ["_MYMCP_TEMP_RW_TOKEN"] = rw


def cmd_serve(args: argparse.Namespace) -> int:
    if args.env_file:
        os.environ["MYMCP_ENV_FILE"] = args.env_file

    _configure_logging(args.log_level, args.log_format)
    _maybe_set_temp_tokens(args.with_metrics_token)

    from mymcp import config
    config.reset_settings_cache()
    s = config.get_settings()

    host = args.host if args.host is not None else s.host
    port = args.port if args.port is not None else s.port

    _install_signal_handlers()

    from mymcp.server import create_app
    app = create_app()

    # Register temp rw token if temp mode injected one
    rw = os.environ.pop("_MYMCP_TEMP_RW_TOKEN", "")
    if rw:
        from mymcp.auth import get_store
        store = get_store()
        with store._lock:  # noqa: SLF001 - local in-memory injection
            store._data["tokens"][rw] = {
                "name": "temp-rw",
                "created_at": "ephemeral",
                "last_used": None,
                "enabled": True,
                "role": "rw",
            }

    import uvicorn

    uvicorn.run(app, host=host, port=port, log_config=None)
    return 0


def cmd_version(_args: argparse.Namespace) -> int:
    print(f"mymcp {__version__}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mymcp",
        description="MCP server for Linux system control",
    )
    parser.add_argument("--version", action="version", version=f"mymcp {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_serve = sub.add_parser("serve", help="Run the MCP server (foreground)")
    p_serve.add_argument("--env-file", help="Path to .env file")
    p_serve.add_argument("--host", help="Override bind host")
    p_serve.add_argument("--port", type=int, help="Override bind port")
    p_serve.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
    )
    p_serve.add_argument("--log-format", default="text", choices=["text", "json"])
    p_serve.add_argument(
        "--with-metrics-token", action="store_true",
        help="In temp-token mode, also generate an ephemeral metrics token",
    )
    p_serve.set_defaults(func=cmd_serve)

    p_version = sub.add_parser("version", help="Print the installed version")
    p_version.set_defaults(func=cmd_version)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
```

- [ ] **Step 2: Run CLI tests**

```bash
python3 -m pytest tests/test_cli.py -v
```
Expected: all 4 tests pass.

- [ ] **Step 3: Smoke test `mymcp` shell command**

```bash
mymcp version
mymcp --version
mymcp serve --help | head -20
```
Expected: each prints the version or usage.

- [ ] **Step 4: Run full test suite**

```bash
python3 -m pytest tests/ -v --benchmark-disable
```
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add src/mymcp/cli.py
git commit -m "feat: add mymcp CLI with serve and version subcommands"
```

---

### Task 13: Test bash subprocess SIGTERM cleanup

**Files:**
- Create: `tests/test_bash_signal_cleanup.py`

- [ ] **Step 1: Write the failing test**

```python
"""SIGTERM to the parent must propagate to in-flight bash subprocesses."""
import os
import signal
import subprocess
import sys
import time

import pytest


pytestmark = pytest.mark.skipif(
    sys.platform != "linux", reason="signal/process group test is Linux-only",
)


def _spawn_serve(env_extra: dict[str, str], tmp_path) -> subprocess.Popen:
    env = os.environ.copy()
    env.update({
        "MYMCP_ADMIN_TOKEN": "tok_test_admin",
        "MYMCP_TOKEN_FILE": str(tmp_path / "tokens.json"),
        "MYMCP_AUDIT_LOG_DIR": str(tmp_path / "audit"),
        "MYMCP_HOST": "127.0.0.1",
        "MYMCP_PORT": "0",      # pick free port; not used here, we test cleanup unit
        "MYMCP_SHUTDOWN_GRACE_SEC": "2",
    })
    env.update(env_extra)
    (tmp_path / "audit").mkdir()
    return subprocess.Popen(
        [sys.executable, "-m", "mymcp", "serve", "--port", "0"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def test_shutdown_inflight_processes_kills_running_child():
    """Unit test: directly call the cleanup function with a known child."""
    from mymcp.tools.bash import _track_process, shutdown_inflight_processes

    p = subprocess.Popen(
        ["sleep", "30"],
        start_new_session=True,
    )
    try:
        _track_process(p)
        assert p.poll() is None  # still running

        shutdown_inflight_processes(grace_sec=2)

        # Within (grace + small slack), child should be gone
        for _ in range(30):
            if p.poll() is not None:
                break
            time.sleep(0.1)
        assert p.poll() is not None, "child still alive after shutdown_inflight_processes"
    finally:
        if p.poll() is None:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait(timeout=2)


def test_shutdown_inflight_processes_handles_already_exited():
    """Cleanup must not raise if the child has already exited."""
    from mymcp.tools.bash import _track_process, shutdown_inflight_processes

    p = subprocess.Popen(["true"], start_new_session=True)
    p.wait(timeout=5)
    _track_process(p)

    shutdown_inflight_processes(grace_sec=1)  # must not raise
```

- [ ] **Step 2: Run to verify failure**

```bash
python3 -m pytest tests/test_bash_signal_cleanup.py -v
```
Expected: ImportError on `_track_process` and `shutdown_inflight_processes` (don't exist yet).

- [ ] **Step 3: Commit failing test**

```bash
git add tests/test_bash_signal_cleanup.py
git commit -m "test: add failing tests for bash subprocess SIGTERM cleanup"
```

---

### Task 14: Implement bash SIGTERM cleanup

**Files:**
- Modify: `src/mymcp/tools/bash.py` (add tracking + cleanup; convert `subprocess.run` → `Popen` in new process group)

- [ ] **Step 1: Read current bash.py**

```bash
cat src/mymcp/tools/bash.py
```
Identify the `subprocess.run` call; that's the line to convert.

- [ ] **Step 2: Add tracking infrastructure to top of `bash.py`**

After the existing imports, add:

```python
import os
import signal
import threading
import time
from weakref import WeakSet

_inflight_lock = threading.Lock()
_inflight: WeakSet = WeakSet()


def _track_process(p) -> None:
    with _inflight_lock:
        _inflight.add(p)


def _untrack_process(p) -> None:
    with _inflight_lock:
        _inflight.discard(p)


def shutdown_inflight_processes(grace_sec: int | None = None) -> None:
    """Send SIGTERM to all tracked process groups, then SIGKILL after grace.

    Idempotent and safe to call from a signal handler.
    """
    if grace_sec is None:
        from mymcp import config
        grace_sec = config.get_settings().shutdown_grace_sec

    with _inflight_lock:
        snapshot = list(_inflight)

    for p in snapshot:
        if p.poll() is not None:
            continue
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass

    deadline = time.monotonic() + max(0, grace_sec)
    while time.monotonic() < deadline:
        if all(p.poll() is not None for p in snapshot):
            return
        time.sleep(0.05)

    for p in snapshot:
        if p.poll() is None:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
```

- [ ] **Step 3: Convert the `subprocess.run` call to `Popen` with tracking**

Locate the body of `run_bash_execute` (or whatever the executor function is). Replace the `subprocess.run(...)` call. Example transformation:

```python
# Old (in run_bash_execute):
result = subprocess.run(
    ["bash", "-c", command],
    capture_output=True, text=True, timeout=timeout,
)
# returncode = result.returncode
# stdout = result.stdout; stderr = result.stderr
```
becomes:

```python
import subprocess

p = subprocess.Popen(
    ["bash", "-c", command],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    start_new_session=True,
)
_track_process(p)
try:
    stdout, stderr = p.communicate(timeout=timeout)
    timed_out = False
except subprocess.TimeoutExpired:
    try:
        os.killpg(os.getpgid(p.pid), signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        stdout, stderr = p.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        stdout, stderr = p.communicate(timeout=2)
    timed_out = True
finally:
    _untrack_process(p)

returncode = p.returncode
```

Preserve the existing output truncation, audit metadata, and return-shape logic untouched.

- [ ] **Step 4: Run the cleanup tests**

```bash
python3 -m pytest tests/test_bash_signal_cleanup.py -v
```
Expected: both tests pass.

- [ ] **Step 5: Run the existing bash tests**

```bash
python3 -m pytest tests/test_bash.py tests/test_clamping.py tests/test_boundary.py -v
```
Expected: pass (the conversion preserves return shape).

- [ ] **Step 6: Run full suite**

```bash
python3 -m pytest tests/ -v --benchmark-disable
```
Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add src/mymcp/tools/bash.py
git commit -m "feat(bash): track in-flight subprocesses and clean up on SIGTERM"
```

---

### Task 15: Add `.pre-commit-config.yaml`

**Files:**
- Create: `.pre-commit-config.yaml`

- [ ] **Step 1: Write `.pre-commit-config.yaml`**

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.0
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.11.0
    hooks:
      - id: mypy
        files: ^src/mymcp/
        additional_dependencies:
          - pydantic>=2
          - pydantic-settings>=2
          - types-setuptools
```

- [ ] **Step 2: Install hooks locally and run once**

```bash
python3 -m pip install pre-commit
pre-commit install
pre-commit run --all-files
```
Expected: ruff/format may auto-fix some files; mypy may flag type issues. Allow ruff fixes; **fix any mypy errors that block** (in scope: `src/mymcp/**`). For unfixable issues unrelated to this plan, add narrow `# type: ignore[code]` with a comment explaining why.

- [ ] **Step 3: Run tests after lint fixes**

```bash
python3 -m pytest tests/ -v --benchmark-disable
```
Expected: pass.

- [ ] **Step 4: Commit lint config and any auto-fixes**

```bash
git add .pre-commit-config.yaml
git add -u
git commit -m "build: add ruff/mypy/pre-commit configuration"
```

---

### Task 16: Add CI workflow

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Verify `.github/workflows/` directory exists, else create**

```bash
mkdir -p .github/workflows
```

- [ ] **Step 2: Write `.github/workflows/ci.yml`**

```yaml
name: CI

on:
  pull_request:
  push:
    branches: [master]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install lint tooling
        run: |
          python -m pip install --upgrade pip
          pip install ruff mypy pydantic-settings types-setuptools
      - run: ruff check .
      - run: ruff format --check .
      - run: mypy src/mymcp

  test:
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        python-version: ["3.11", "3.12", "3.13"]
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - name: Install package
        run: |
          python -m pip install --upgrade pip
          pip install -e ".[dev]"
      - name: Run pytest
        run: pytest tests/ -v --benchmark-disable

  bats:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Install bats
        run: |
          sudo apt-get update -qq
          sudo apt-get install -y -qq bats
      - name: Run legacy install/upgrade bats tests
        run: |
          bats tests/test_install.bats tests/test_upgrade.bats tests/test_upgrade_integration.bats

  build:
    runs-on: ubuntu-latest
    needs: [lint, test]
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
      - uses: actions/upload-artifact@v4
        with:
          name: dist
          path: dist/*
          retention-days: 7
```

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: lint + test matrix + bats + build artifact"
```

---

### Task 17: Clean up legacy artifacts

**Files:**
- Delete: `VERSION`, `requirements.txt`, `requirements-dev.txt`, `tokens.json`

- [ ] **Step 1: Remove the files**

```bash
git rm VERSION requirements.txt requirements-dev.txt
[ -f tokens.json ] && git rm tokens.json || true
```

- [ ] **Step 2: Verify nothing references them**

```bash
grep -rn "VERSION\|requirements.txt\|requirements-dev.txt" \
    --include="*.py" --include="*.md" --include="*.sh" --include="*.yml" \
    | grep -v "^docs/superpowers/" || true
```
Expected: results may include `deploy/install.sh` and `deploy/upgrade.sh` (legacy bash, kept on purpose) and CHANGELOG references — those are fine. If anywhere live in `src/mymcp/` or CI references them, fix the reference.

- [ ] **Step 3: Run full test suite**

```bash
python3 -m pytest tests/ -v --benchmark-disable
```
Expected: pass.

- [ ] **Step 4: Commit**

```bash
git add -u
git commit -m "chore: remove VERSION, requirements*.txt — replaced by pyproject.toml"
```

---

### Task 18: Update `CLAUDE.md` commands section

**Files:**
- Modify: `CLAUDE.md` (Commands and Architecture sections)

- [ ] **Step 1: Open `CLAUDE.md` and replace the Commands section**

Replace existing content under `## Commands` with:

```markdown
## Commands

```bash
# Install in editable mode for development
pip install -e ".[dev]"

# Run all tests
pytest tests/ -v --benchmark-disable

# Run a single test
pytest tests/test_files.py::test_read_file_basic -v

# Run bats tests for legacy deploy helpers (kept through 2.0.x)
bats tests/test_install.bats tests/test_upgrade.bats tests/test_upgrade_integration.bats

# Start dev server (foreground, prints temp tokens to stderr)
mymcp serve

# Start dev server with custom .env
mymcp serve --env-file ./.env

# Lint and type-check
ruff check . && ruff format --check . && mypy src/mymcp
```
```

- [ ] **Step 2: Update the Architecture section's "Key files" subsection**

Replace existing file paths to reflect the new layout:

```markdown
### Key files

- `src/mymcp/cli.py` — argparse entry, logging configuration, signal handlers
- `src/mymcp/server.py` — FastAPI app factory (`create_app()`), middlewares, routes
- `src/mymcp/mcp_server.py` — MCP Server with tool definitions, permission enforcement, dispatch, audit logging
- `src/mymcp/config.py` — pydantic-settings `Settings`; reads `MYMCP_*` env vars + optional .env file
- `src/mymcp/audit.py` — Rotating file audit logger
- `src/mymcp/auth.py` — TokenStore (JSON file-backed), admin API router, FastAPI deps
- `src/mymcp/tools/files.py` — read/write/edit/glob/grep with `check_protected_path()`
- `src/mymcp/tools/bash.py` — `run_bash_execute` with timeout, output truncation, and SIGTERM-safe subprocess tracking
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md for src/mymcp layout and pip install"
```

---

### Task 19: End-to-end smoke test

**Files:**
- (No file changes; verification only.)

- [ ] **Step 1: Reinstall fresh in editable mode**

```bash
pip uninstall -y mymcp
pip install -e ".[dev]"
```

- [ ] **Step 2: Build a wheel and inspect contents**

```bash
python3 -m build
unzip -l dist/mymcp-*.whl | head -30
```
Expected: `mymcp/cli.py`, `mymcp/server.py`, `mymcp/_version.py`, `mymcp/tools/bash.py`, etc. all present.

- [ ] **Step 3: Smoke-run the server in temp-token mode**

In one terminal:
```bash
mymcp serve --port 28765 --log-level INFO
```
Expected: stderr prints temp admin and rw tokens, then uvicorn starts.

In another terminal:
```bash
curl -s http://127.0.0.1:28765/health
```
Expected: `{"status":"ok"}`.

```bash
RW=<paste rw token from stderr>
curl -s -H "Authorization: Bearer $RW" http://127.0.0.1:28765/version
```
Expected: `{"version":"…"}`.

- [ ] **Step 4: Send SIGTERM and verify clean exit**

In the serve terminal, press Ctrl+C. Expected: process exits within `MYMCP_SHUTDOWN_GRACE_SEC` (default 5s).

- [ ] **Step 5: Run full test suite one more time**

```bash
pytest tests/ -v --benchmark-disable
```
Expected: pass on Python 3.11+.

- [ ] **Step 6: Push branch**

```bash
git push
```

- [ ] **Step 7: Open the PR**

```bash
gh pr create --title "Plan 1: pip-installable mymcp foundation" --body "$(cat <<'EOF'
## Summary
- Restructure to src/mymcp/ package layout and add full pyproject.toml
- Rewrite config.py with pydantic-settings + MYMCP_ env prefix (hard rename, no MCP_ compat)
- Split main.py into mymcp.server (factory) + mymcp.cli (argparse + logging + signals)
- Add mymcp serve / mymcp version subcommands; temp-token mode prints admin+rw tokens to stderr
- Bash tool tracks subprocesses; SIGTERM/SIGINT cleans up the process group with grace + SIGKILL
- ruff + mypy + pre-commit config; CI matrix on Python 3.11/3.12/3.13 + bats + build job
- Remove VERSION, requirements.txt, requirements-dev.txt; setuptools-scm derives version from git tag

Spec: docs/superpowers/specs/2026-04-26-pip-distribution-design.md
Plan: docs/superpowers/plans/2026-04-26-pip-distribution-plan-1-package-foundation.md

## Test plan
- [ ] CI lint job passes
- [ ] CI test matrix passes on 3.11/3.12/3.13
- [ ] CI bats job passes (legacy install.sh still works)
- [ ] CI build job uploads a wheel artifact
- [ ] Local: `pip install -e ".[dev]"` then `mymcp serve --port 28765` accepts MCP traffic
- [ ] Local: SIGTERM during a long-running bash_execute kills the child within grace

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-review

**Spec coverage check (against `docs/superpowers/specs/2026-04-26-pip-distribution-design.md`):**

| Spec section | In this plan |
|---|---|
| §1 Package structure (src/mymcp/, file moves) | Tasks 1, 2, 3, 4, 10 |
| §2 CLI subcommands — `serve`, `version` | Tasks 11, 12 |
| §2 CLI — `install-service`, `uninstall-service`, `token`, `migrate-from-legacy`, `doctor` | **Plan 2** (not this plan) |
| §2 Logging in cli.py (no import side-effects) | Tasks 9, 10, 12 |
| §2 Temp-token mode (admin + rw, optional metrics) | Task 12 |
| §3 pyproject.toml metadata | Task 5 |
| §3 setuptools-scm version from git tag | Task 5 (`[tool.setuptools_scm]`) |
| §3 ruff + mypy config | Task 5 + Task 15 |
| §3 Drop requirements*.txt | Task 17 |
| §4 install-service flow | **Plan 2** |
| §5 migrate-from-legacy + version 2.0.0 tag | **Plan 2** + Plan 3 |
| §6 CI lint/test/bats/build | Task 16 |
| §6 release.yml + PyPI publish + offline bundle | **Plan 3** |
| §6 pre-commit config | Task 15 |
| §7 Bash SIGTERM cleanup | Tasks 13, 14 |
| Env rename `MCP_*` → `MYMCP_*` (hard) | Tasks 7, 8 |

All spec items covered are implemented; deferred items go to Plans 2/3 as designed.

**Type/name consistency:**
- `get_settings()` introduced in Task 7, called in Tasks 8, 9, 10, 12, 14 — consistent.
- `_track_process` / `_untrack_process` / `shutdown_inflight_processes` — defined Task 14, called Task 12, tested Task 13 — consistent.
- `create_app()` — defined Task 10, used in Task 9 test and Task 12 CLI — consistent.

**No-placeholder check:**
- Task 10 says "port the actual logic from `main.py`" — this is necessary because the existing middleware code is too long to inline, but the porting rules (no `logging.basicConfig`, no module-level `app`, all references to `config.X` switch to `config.get_settings().x`) are explicit. Acceptable.
- Task 14 likewise references "preserve the existing output truncation, audit metadata, and return-shape logic untouched" — this is concrete because the engineer can diff the file and see exactly what those are.
- All test code is fully written; all command lines are exact.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-26-pip-distribution-plan-1-package-foundation.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
