# Audit Logging & Permission Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add audit logging, per-token ro/rw permissions, protected path enforcement, and remove unused file transfer endpoints.

**Architecture:** Token model gains a `role` field. `mcp_server.py` filters tools by role in `list_tools()`/`call_tool()`. New `audit.py` module writes JSON Lines via `RotatingFileHandler`. `tools/files.py` gains `check_protected_path()` to block access to MCP's own files. Deploy script and README updated.

**Tech Stack:** Python 3.11+, FastAPI, MCP SDK, Python `logging` module

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `config.py` | Modify | Add audit + protected path config entries |
| `audit.py` | Create | Audit logger: setup, `log_tool_call()`, JSON Lines format |
| `auth.py` | Modify | Token role field, `create_token(name, role)`, backward compat on load |
| `mcp_server.py` | Modify | Permission filtering in list_tools/call_tool, audit integration |
| `tools/files.py` | Modify | `check_protected_path()`, integrate into all file tools |
| `main.py` | Modify | Remove transfer_router, pass token info into MCP context |
| `tools/transfer.py` | Delete | Remove file transfer endpoints |
| `tests/test_transfer.py` | Delete | Remove transfer tests |
| `tests/test_audit.py` | Create | Audit logger tests |
| `tests/test_permissions.py` | Create | Permission filtering tests |
| `tests/test_protected_paths.py` | Create | Protected path tests |
| `tests/test_auth.py` | Modify | Add role field tests |
| `deploy/install.sh` | Modify | Audit logging prompts |
| `README.md` | Modify | Document new features |

---

### Task 1: Config — Add Audit & Protected Path Settings

**Files:**
- Modify: `config.py`

- [ ] **Step 1: Add new config entries to config.py**

Add to the end of `config.py`:

```python
# Audit logging
AUDIT_ENABLED = os.getenv("MCP_AUDIT_ENABLED", "false").lower() in ("true", "1", "yes")
AUDIT_LOG_DIR = os.getenv("MCP_AUDIT_LOG_DIR", "/var/log/mymcp")
AUDIT_MAX_BYTES = int(os.getenv("MCP_AUDIT_MAX_BYTES", str(10 * 1024 * 1024)))  # 10MB
AUDIT_BACKUP_COUNT = int(os.getenv("MCP_AUDIT_BACKUP_COUNT", "5"))

# Protected paths (APP_DIR and AUDIT_LOG_DIR are always protected)
APP_DIR = os.getenv("MCP_APP_DIR", "/opt/mymcp")
_extra = os.getenv("MCP_PROTECTED_PATHS", "")
PROTECTED_PATHS: list[str] = [APP_DIR, AUDIT_LOG_DIR]
if _extra.strip():
    PROTECTED_PATHS.extend(p.strip() for p in _extra.split(",") if p.strip())
```

- [ ] **Step 2: Verify syntax**

Run: `python3 -c "import config; print(config.AUDIT_ENABLED, config.PROTECTED_PATHS)"`
Expected: `False ['/opt/mymcp', '/var/log/mymcp']`

- [ ] **Step 3: Commit**

```bash
git add config.py
git commit -m "feat: add audit and protected path config entries"
```

---

### Task 2: Audit Module

**Files:**
- Create: `audit.py`
- Create: `tests/test_audit.py`

- [ ] **Step 1: Write the tests**

Create `tests/test_audit.py`:

```python
import json
import os
import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def audit_config(tmp_path):
    with patch.multiple(
        "config",
        AUDIT_ENABLED=True,
        AUDIT_LOG_DIR=str(tmp_path),
        AUDIT_MAX_BYTES=1024 * 1024,
        AUDIT_BACKUP_COUNT=2,
    ):
        import audit
        audit._logger = None
        audit._setup_done = False
        yield tmp_path


def test_log_tool_call_writes_json_line(audit_config):
    import audit
    audit.log_tool_call(
        token_name="test-client",
        role="rw",
        ip="127.0.0.1",
        tool="bash_execute",
        params={"command": "ls"},
        result="success",
        duration_ms=42,
    )

    log_file = audit_config / "audit.log"
    assert log_file.exists()
    line = log_file.read_text().strip()
    record = json.loads(line)
    assert record["token_name"] == "test-client"
    assert record["role"] == "rw"
    assert record["ip"] == "127.0.0.1"
    assert record["tool"] == "bash_execute"
    assert record["params"] == {"command": "ls"}
    assert record["result"] == "success"
    assert record["duration_ms"] == 42
    assert "ts" in record
    assert "reason" not in record


def test_log_denied_includes_reason(audit_config):
    import audit
    audit.log_tool_call(
        token_name="readonly-bot",
        role="ro",
        ip="10.0.0.1",
        tool="write_file",
        params={"file_path": "/tmp/x"},
        result="denied",
        reason="ro_role",
    )

    log_file = audit_config / "audit.log"
    record = json.loads(log_file.read_text().strip())
    assert record["result"] == "denied"
    assert record["reason"] == "ro_role"
    assert "duration_ms" not in record


def test_log_error_includes_reason(audit_config):
    import audit
    audit.log_tool_call(
        token_name="client",
        role="rw",
        ip="10.0.0.1",
        tool="bash_execute",
        params={"command": "bad"},
        result="error",
        reason="TimeoutError",
    )

    log_file = audit_config / "audit.log"
    record = json.loads(log_file.read_text().strip())
    assert record["result"] == "error"
    assert record["reason"] == "TimeoutError"


def test_audit_disabled_writes_nothing(tmp_path):
    with patch.multiple(
        "config",
        AUDIT_ENABLED=False,
        AUDIT_LOG_DIR=str(tmp_path),
        AUDIT_MAX_BYTES=1024 * 1024,
        AUDIT_BACKUP_COUNT=2,
    ):
        import audit
        audit._logger = None
        audit._setup_done = False
        audit.log_tool_call(
            token_name="x",
            role="rw",
            ip="1.2.3.4",
            tool="glob",
            params={"pattern": "*"},
            result="success",
        )
        log_file = tmp_path / "audit.log"
        assert not log_file.exists()


def test_multiple_entries_are_separate_lines(audit_config):
    import audit
    for i in range(3):
        audit.log_tool_call(
            token_name=f"client-{i}",
            role="rw",
            ip="127.0.0.1",
            tool="glob",
            params={"pattern": "*"},
            result="success",
            duration_ms=i,
        )

    log_file = audit_config / "audit.log"
    lines = [l for l in log_file.read_text().strip().split("\n") if l]
    assert len(lines) == 3
    for line in lines:
        json.loads(line)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/zhu/repos/mymcp && python3 -m pytest tests/test_audit.py -v`
Expected: FAIL (no module `audit`)

- [ ] **Step 3: Implement audit.py**

Create `audit.py`:

```python
import json
import logging
import logging.handlers
import os
from datetime import datetime, timezone

import config

_logger: logging.Logger | None = None
_setup_done = False


def _setup() -> logging.Logger | None:
    global _setup_done
    _setup_done = True

    if not config.AUDIT_ENABLED:
        return None

    os.makedirs(config.AUDIT_LOG_DIR, exist_ok=True)
    log_path = os.path.join(config.AUDIT_LOG_DIR, "audit.log")

    logger = logging.getLogger("mymcp.audit")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    # Avoid duplicate handlers on re-init (tests)
    logger.handlers.clear()

    handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=config.AUDIT_MAX_BYTES,
        backupCount=config.AUDIT_BACKUP_COUNT,
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    return logger


def log_tool_call(
    *,
    token_name: str,
    role: str,
    ip: str,
    tool: str,
    params: dict,
    result: str,
    reason: str | None = None,
    duration_ms: int | None = None,
) -> None:
    global _logger
    if not _setup_done:
        _logger = _setup()
    if _logger is None:
        return

    entry: dict = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "token_name": token_name,
        "role": role,
        "ip": ip,
        "tool": tool,
        "params": params,
        "result": result,
    }
    if reason is not None:
        entry["reason"] = reason
    if duration_ms is not None:
        entry["duration_ms"] = duration_ms

    _logger.info(json.dumps(entry, ensure_ascii=False))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/zhu/repos/mymcp && python3 -m pytest tests/test_audit.py -v`
Expected: all 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add audit.py tests/test_audit.py
git commit -m "feat: add audit logging module with rotating JSON Lines"
```

---

### Task 3: Token Role Field

**Files:**
- Modify: `auth.py`
- Modify: `tests/test_auth.py`

- [ ] **Step 1: Write the tests**

Add to the end of `tests/test_auth.py`:

```python
def test_create_token_default_role_is_ro(tmp_path):
    store = make_store(tmp_path)
    token = store.create_token("client-ro")
    info = store.validate(token)
    assert info["role"] == "ro"


def test_create_token_with_rw_role(tmp_path):
    store = make_store(tmp_path)
    token = store.create_token("client-rw", role="rw")
    info = store.validate(token)
    assert info["role"] == "rw"


def test_backward_compat_missing_role_defaults_rw(tmp_path):
    """Tokens without a role field (from older versions) default to rw."""
    import json
    path = tmp_path / "tokens.json"
    old_data = {
        "tokens": {
            "tok_legacy": {
                "name": "old-client",
                "created_at": "2026-01-01T00:00:00+00:00",
                "last_used": None,
                "enabled": True,
            }
        },
        "admin_token": "adm_testadmin",
    }
    path.write_text(json.dumps(old_data))
    store = TokenStore(str(path), "adm_testadmin")
    info = store.validate("tok_legacy")
    assert info is not None
    assert info["role"] == "rw"


def test_create_token_invalid_role_raises(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(ValueError):
        store.create_token("bad", role="admin")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/zhu/repos/mymcp && python3 -m pytest tests/test_auth.py -v`
Expected: 4 new tests FAIL

- [ ] **Step 3: Modify auth.py — TokenStore._load() backward compat**

Replace `_load` method (lines 19-26 of auth.py):

```python
    def _load(self) -> None:
        if self.path.exists():
            with open(self.path) as f:
                self._data = json.load(f)
            self._data["admin_token"] = self.admin_token
            # Backward compat: add default role to tokens missing it
            for info in self._data.get("tokens", {}).values():
                if "role" not in info:
                    info["role"] = "rw"
        else:
            self._data = {"tokens": {}, "admin_token": self.admin_token}
            self._save()
```

- [ ] **Step 4: Modify auth.py — TokenStore.create_token() with role**

Replace `create_token` method (lines 43-53 of auth.py):

```python
    def create_token(self, name: str, role: str = "ro") -> str:
        if role not in ("ro", "rw"):
            raise ValueError(f"Invalid role: {role!r}. Must be 'ro' or 'rw'.")
        token = "tok_" + secrets.token_hex(16)
        with self._lock:
            self._data["tokens"][token] = {
                "name": name,
                "role": role,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "last_used": None,
                "enabled": True,
            }
            self._save()
        return token
```

- [ ] **Step 5: Update admin API — _CreateTokenRequest and endpoint**

Replace `_CreateTokenRequest` class (line 120-121 of auth.py):

```python
class _CreateTokenRequest(BaseModel):
    name: str
    role: str = "ro"
```

Replace `create_token` endpoint (lines 130-136 of auth.py):

```python
@admin_router.post("/tokens")
async def create_token(
    body: _CreateTokenRequest,
    store: "TokenStore" = Depends(get_store),
):
    try:
        token = store.create_token(body.name, role=body.role)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"token": token, "name": body.name, "role": body.role}
```

- [ ] **Step 6: Run tests**

Run: `cd /home/zhu/repos/mymcp && python3 -m pytest tests/test_auth.py -v`
Expected: all tests PASS (existing + 4 new)

- [ ] **Step 7: Commit**

```bash
git add auth.py tests/test_auth.py
git commit -m "feat: add role field to tokens (ro/rw) with backward compat"
```

---

### Task 4: Protected Paths

**Files:**
- Modify: `tools/files.py`
- Create: `tests/test_protected_paths.py`

- [ ] **Step 1: Write the tests**

Create `tests/test_protected_paths.py`:

```python
import os
import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def mock_protected_paths(tmp_path):
    app_dir = str(tmp_path / "mymcp")
    audit_dir = str(tmp_path / "audit")
    os.makedirs(app_dir)
    os.makedirs(audit_dir)
    with patch("config.PROTECTED_PATHS", [app_dir, audit_dir]):
        yield app_dir, audit_dir


def test_check_protected_path_blocks_app_dir(mock_protected_paths):
    from tools.files import check_protected_path
    app_dir, _ = mock_protected_paths
    err = check_protected_path(f"{app_dir}/config.py")
    assert err is not None
    assert "protected" in err.lower()


def test_check_protected_path_blocks_audit_dir(mock_protected_paths):
    from tools.files import check_protected_path
    _, audit_dir = mock_protected_paths
    err = check_protected_path(f"{audit_dir}/audit.log")
    assert err is not None


def test_check_protected_path_allows_normal_path(mock_protected_paths):
    from tools.files import check_protected_path
    err = check_protected_path("/tmp/somefile.txt")
    assert err is None


def test_check_protected_path_blocks_symlink(mock_protected_paths, tmp_path):
    from tools.files import check_protected_path
    app_dir, _ = mock_protected_paths
    secret = os.path.join(app_dir, ".env")
    with open(secret, "w") as f:
        f.write("SECRET=x")
    link = str(tmp_path / "sneaky_link")
    os.symlink(secret, link)
    err = check_protected_path(link)
    assert err is not None


def test_check_protected_path_exact_dir(mock_protected_paths):
    from tools.files import check_protected_path
    app_dir, _ = mock_protected_paths
    err = check_protected_path(app_dir)
    assert err is not None


@pytest.mark.anyio
async def test_read_file_rejects_protected_path(mock_protected_paths):
    from tools.files import read_file
    app_dir, _ = mock_protected_paths
    secret = os.path.join(app_dir, ".env")
    with open(secret, "w") as f:
        f.write("TOKEN=secret")
    result = await read_file(secret)
    assert result["success"] is False
    assert "protected" in result["error"].lower() or "protected" in result["message"].lower()


@pytest.mark.anyio
async def test_write_file_rejects_protected_path(mock_protected_paths):
    from tools.files import write_file
    app_dir, _ = mock_protected_paths
    result = await write_file(os.path.join(app_dir, "hack.py"), "evil code")
    assert result["success"] is False


@pytest.mark.anyio
async def test_edit_file_rejects_protected_path(mock_protected_paths):
    from tools.files import edit_file
    app_dir, _ = mock_protected_paths
    target = os.path.join(app_dir, "config.py")
    with open(target, "w") as f:
        f.write("original")
    result = await edit_file(target, "original", "hacked")
    assert result["success"] is False


@pytest.mark.anyio
async def test_glob_filters_protected_paths(mock_protected_paths, tmp_path):
    from tools.files import glob_files
    app_dir, _ = mock_protected_paths
    with open(os.path.join(app_dir, "secret.py"), "w") as f:
        f.write("")
    normal_dir = str(tmp_path / "normal")
    os.makedirs(normal_dir)
    with open(os.path.join(normal_dir, "ok.py"), "w") as f:
        f.write("")

    result = await glob_files("**/*.py", str(tmp_path))
    file_list = result["files"]
    assert any("ok.py" in f for f in file_list)
    assert not any("secret.py" in f for f in file_list)


@pytest.mark.anyio
async def test_grep_filters_protected_paths(mock_protected_paths, tmp_path):
    from tools.files import grep_files
    app_dir, _ = mock_protected_paths
    with open(os.path.join(app_dir, "secret.py"), "w") as f:
        f.write("FINDME_SECRET")
    normal_dir = str(tmp_path / "normal")
    os.makedirs(normal_dir)
    with open(os.path.join(normal_dir, "ok.py"), "w") as f:
        f.write("FINDME_NORMAL")

    result = await grep_files("FINDME", str(tmp_path))
    assert "FINDME_NORMAL" in result["results"]
    assert "FINDME_SECRET" not in result["results"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/zhu/repos/mymcp && python3 -m pytest tests/test_protected_paths.py -v`
Expected: FAIL (no `check_protected_path` function)

- [ ] **Step 3: Add check_protected_path to tools/files.py**

Add at the top of `tools/files.py`, after the existing imports (after `import config`):

```python
def check_protected_path(file_path: str) -> str | None:
    """Returns error message if path is protected, None if allowed."""
    real = os.path.realpath(file_path)
    for protected in config.PROTECTED_PATHS:
        protected_real = os.path.realpath(protected)
        if real == protected_real or real.startswith(protected_real + os.sep):
            return f"Access denied: path is within protected directory {protected}"
    return None


def _filter_protected(paths: list[str]) -> list[str]:
    """Filter out paths that fall within protected directories."""
    return [p for p in paths if check_protected_path(p) is None]
```

- [ ] **Step 4: Integrate into read_file**

In `read_file()`, add after the `offset = max(1, offset)` line:

```python
    err = check_protected_path(file_path)
    if err:
        return {"success": False, "error": "ProtectedPath", "message": err}
```

- [ ] **Step 5: Integrate into write_file**

In `write_file()`, add at the very beginning (before the `content_bytes = ...` line):

```python
    err = check_protected_path(file_path)
    if err:
        return {"success": False, "error": "ProtectedPath", "message": err}
```

- [ ] **Step 6: Integrate into edit_file**

In `edit_file()`, add at the very beginning (before the old_string size check):

```python
    err = check_protected_path(file_path)
    if err:
        return {"success": False, "error": "ProtectedPath", "message": err}
```

- [ ] **Step 7: Integrate into glob_files**

In `glob_files()`, after the `matches.sort(...)` call but before the `truncated = ...` line, add:

```python
        matches = _filter_protected(matches)
```

- [ ] **Step 8: Integrate into grep_files — ripgrep path**

In `_grep_rg()`, after the `lines = stdout.decode(...)` line but before `total = len(lines)`, add:

```python
    # Filter protected paths from results
    filtered = []
    for line in lines:
        parts = line.split(":", 1)
        if parts and check_protected_path(parts[0]) is None:
            filtered.append(line)
    lines = filtered
```

- [ ] **Step 9: Integrate into grep_files — Python fallback path**

In `_grep_python()`, inside the `for fpath in files_to_search:` loop, add at the top of the loop body:

```python
        if check_protected_path(fpath) is not None:
            continue
```

- [ ] **Step 10: Run tests**

Run: `cd /home/zhu/repos/mymcp && python3 -m pytest tests/test_protected_paths.py -v`
Expected: all 12 tests PASS

- [ ] **Step 11: Run all existing tests to check for regressions**

Run: `cd /home/zhu/repos/mymcp && python3 -m pytest tests/ -v --ignore=tests/test_transfer.py`
Expected: all PASS

- [ ] **Step 12: Commit**

```bash
git add tools/files.py tests/test_protected_paths.py
git commit -m "feat: add protected path enforcement for file tools"
```

---

### Task 5: Permission Filtering in MCP Server

**Files:**
- Modify: `mcp_server.py`
- Modify: `main.py`
- Create: `tests/test_permissions.py`

- [ ] **Step 1: Write the tests**

Create `tests/test_permissions.py`:

```python
import pytest
from mcp_server import (
    ALL_TOOLS,
    READ_TOOLS,
    WRITE_TOOLS,
    filter_tools_by_role,
    check_tool_permission,
)


def test_read_tools_and_write_tools_cover_all():
    assert READ_TOOLS | WRITE_TOOLS == ALL_TOOLS


def test_filter_tools_ro_only_returns_read_tools():
    tools = filter_tools_by_role("ro")
    tool_names = {t.name for t in tools}
    assert tool_names == READ_TOOLS


def test_filter_tools_rw_returns_all_tools():
    tools = filter_tools_by_role("rw")
    tool_names = {t.name for t in tools}
    assert tool_names == ALL_TOOLS


def test_check_permission_ro_read_tool_allowed():
    err = check_tool_permission("read_file", "ro")
    assert err is None


def test_check_permission_ro_write_tool_denied():
    err = check_tool_permission("bash_execute", "ro")
    assert err is not None
    assert "rw" in err


def test_check_permission_rw_write_tool_allowed():
    err = check_tool_permission("bash_execute", "rw")
    assert err is None


def test_check_permission_unknown_tool():
    err = check_tool_permission("nonexistent", "rw")
    assert err is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/zhu/repos/mymcp && python3 -m pytest tests/test_permissions.py -v`
Expected: FAIL (no such names exported)

- [ ] **Step 3: Rewrite mcp_server.py**

Replace entire `mcp_server.py` with:

```python
import json
import time

from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp import types

import config
from audit import log_tool_call
from tools.bash import run_bash_execute
from tools.files import read_file, write_file, edit_file, glob_files, grep_files

server = Server("linux-server")
session_manager = StreamableHTTPSessionManager(server)

READ_TOOLS = {"read_file", "glob", "grep"}
WRITE_TOOLS = {"bash_execute", "write_file", "edit_file"}
ALL_TOOLS = READ_TOOLS | WRITE_TOOLS


def _build_tool_definitions() -> dict[str, types.Tool]:
    """Build all tool definitions once, keyed by name."""
    tools = {}
    tools["bash_execute"] = types.Tool(
        name="bash_execute",
        description=(
            "Execute any shell command on the Linux server. "
            "Stateless: each call is a fresh subprocess, no persistent shell state."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to run"},
                "timeout": {"type": "integer", "description": "Timeout seconds (default 30, max 600)"},
                "working_dir": {"type": "string", "description": "Working directory (default /)"},
                "max_output_bytes": {"type": "integer", "description": "Max stdout/stderr bytes each (default 102400)"},
            },
            "required": ["command"],
        },
    )
    tools["read_file"] = types.Tool(
        name="read_file",
        description="Read a file with line numbers. Supports pagination via offset/limit.",
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute path to file"},
                "offset": {"type": "integer", "description": "Start line 1-based (default 1)"},
                "limit": {"type": "integer", "description": "Lines to read (default 2000, max 10000)"},
            },
            "required": ["file_path"],
        },
    )
    tools["write_file"] = types.Tool(
        name="write_file",
        description="Create or overwrite a file. Max 10MB.",
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute path"},
                "content": {"type": "string", "description": "File content (max 10MB)"},
            },
            "required": ["file_path", "content"],
        },
    )
    tools["edit_file"] = types.Tool(
        name="edit_file",
        description="Replace a string in a file. old_string must be unique unless replace_all=true.",
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "old_string": {"type": "string", "description": "String to find (max 1MB)"},
                "new_string": {"type": "string", "description": "Replacement string (max 1MB)"},
                "replace_all": {"type": "boolean", "description": "Replace every occurrence (default false)"},
            },
            "required": ["file_path", "old_string", "new_string"],
        },
    )
    tools["glob"] = types.Tool(
        name="glob",
        description="Find files by glob pattern, e.g. '**/*.py'. Results sorted by mtime desc.",
        inputSchema={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Glob pattern, e.g. '**/*.log'"},
                "path": {"type": "string", "description": "Root directory (default /)"},
            },
            "required": ["pattern"],
        },
    )
    tools["grep"] = types.Tool(
        name="grep",
        description="Search file contents with regex. Uses ripgrep if installed, else Python fallback.",
        inputSchema={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern"},
                "path": {"type": "string", "description": "File or directory to search (default /)"},
                "glob": {"type": "string", "description": "File filter e.g. '*.log'"},
                "output_mode": {
                    "type": "string",
                    "enum": ["content", "files", "count"],
                    "description": "Output mode (default content)",
                },
                "context_lines": {"type": "integer", "description": "Lines of context (default 0)"},
                "max_results": {"type": "integer", "description": "Max matches (default 250, max 5000)"},
                "case_insensitive": {"type": "boolean", "description": "Case-insensitive (default false)"},
            },
            "required": ["pattern"],
        },
    )
    return tools


_TOOL_DEFS = _build_tool_definitions()


def filter_tools_by_role(role: str) -> list[types.Tool]:
    """Return tool definitions visible to the given role."""
    if role == "rw":
        return list(_TOOL_DEFS.values())
    return [t for name, t in _TOOL_DEFS.items() if name in READ_TOOLS]


def check_tool_permission(tool_name: str, role: str) -> str | None:
    """Returns error message if tool is not allowed for role, None if OK."""
    if tool_name not in ALL_TOOLS:
        return f"No tool named '{tool_name}'"
    if role != "rw" and tool_name in WRITE_TOOLS:
        return f"Tool '{tool_name}' requires 'rw' role"
    return None


def _extract_params(name: str, args: dict) -> dict:
    """Extract key parameters for audit logging — avoid logging large content."""
    if name == "bash_execute":
        return {"command": args.get("command", "")}
    if name in ("read_file", "write_file", "edit_file"):
        return {"file_path": args.get("file_path", "")}
    if name == "glob":
        return {"pattern": args.get("pattern", ""), "path": args.get("path", "/")}
    if name == "grep":
        return {"pattern": args.get("pattern", ""), "path": args.get("path", "/")}
    return {}


def _get_audit_info() -> dict:
    """Extract audit info stashed by the auth middleware in scope state."""
    try:
        ctx = server.request_context
        if ctx and ctx.session and hasattr(ctx.session, '_audit_info'):
            return ctx.session._audit_info
    except Exception:
        pass
    return {"token_name": "unknown", "role": "rw", "ip": "unknown"}


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    info = _get_audit_info()
    return filter_tools_by_role(info.get("role", "rw"))


@server.call_tool()
async def call_tool(name: str, arguments: dict | None) -> list[types.TextContent]:
    args = arguments or {}
    info = _get_audit_info()
    token_name = info.get("token_name", "unknown")
    role = info.get("role", "rw")
    ip = info.get("ip", "unknown")

    # Permission check
    perm_err = check_tool_permission(name, role)
    if perm_err:
        log_tool_call(
            token_name=token_name, role=role, ip=ip,
            tool=name, params=_extract_params(name, args),
            result="denied", reason="ro_role",
        )
        return [types.TextContent(type="text", text=json.dumps({
            "success": False, "error": "PermissionError", "message": perm_err,
        }))]

    # Execute
    start = time.monotonic()
    result_json = await dispatch_tool(name, args)
    elapsed_ms = int((time.monotonic() - start) * 1000)

    # Audit
    result_data = json.loads(result_json)
    audit_result = "success"
    audit_reason = None
    if isinstance(result_data, dict) and result_data.get("success") is False:
        if result_data.get("error") == "ProtectedPath":
            audit_result = "denied"
            audit_reason = "protected_path"
        else:
            audit_result = "error"
            audit_reason = result_data.get("error", "unknown")

    log_tool_call(
        token_name=token_name, role=role, ip=ip,
        tool=name, params=_extract_params(name, args),
        result=audit_result,
        reason=audit_reason,
        duration_ms=elapsed_ms if audit_result == "success" else None,
    )

    return [types.TextContent(type="text", text=result_json)]


async def dispatch_tool(name: str, args: dict) -> str:
    """Dispatch to the appropriate tool function and return JSON string."""
    if name == "bash_execute":
        result = await run_bash_execute(
            command=args["command"],
            timeout=min(args.get("timeout", 30), 600),
            working_dir=args.get("working_dir", "/"),
            max_output_bytes=min(
                args.get("max_output_bytes", config.BASH_MAX_OUTPUT_BYTES),
                config.BASH_MAX_OUTPUT_BYTES_HARD,
            ),
        )
    elif name == "read_file":
        result = await read_file(
            file_path=args["file_path"],
            offset=args.get("offset", 1),
            limit=min(args.get("limit", config.READ_FILE_DEFAULT_LIMIT), config.READ_FILE_MAX_LIMIT),
        )
    elif name == "write_file":
        result = await write_file(
            file_path=args["file_path"],
            content=args["content"],
        )
    elif name == "edit_file":
        result = await edit_file(
            file_path=args["file_path"],
            old_string=args["old_string"],
            new_string=args["new_string"],
            replace_all=args.get("replace_all", False),
        )
    elif name == "glob":
        result = await glob_files(
            pattern=args["pattern"],
            path=args.get("path", "/"),
        )
    elif name == "grep":
        result = await grep_files(
            pattern=args["pattern"],
            path=args.get("path", "/"),
            glob=args.get("glob"),
            output_mode=args.get("output_mode", "content"),
            context_lines=args.get("context_lines", 0),
            max_results=min(
                args.get("max_results", config.GREP_DEFAULT_MAX_RESULTS),
                config.GREP_MAX_RESULTS,
            ),
            case_insensitive=args.get("case_insensitive", False),
        )
    else:
        result = {
            "success": False,
            "error": "UnknownTool",
            "message": f"No tool named '{name}'",
        }

    return json.dumps(result)
```

- [ ] **Step 4: Rewrite main.py — remove transfer, pass token info**

Replace entire `main.py`:

```python
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

import config
from auth import admin_router, get_store
from mcp_server import server, session_manager


def _validate_token(request: Request) -> tuple[JSONResponse | None, dict | None]:
    """Validate bearer token. Returns (error_response, token_info)."""
    store = get_store()
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return JSONResponse({"detail": "Missing Bearer token"}, status_code=401), None
    token = auth[7:]
    info = store.validate(token)
    if info is None:
        return JSONResponse({"detail": "Invalid or disabled token"}, status_code=401), None
    return None, info


class McpAuthMiddleware:
    """Intercepts /mcp to validate Bearer token, then delegates
    to StreamableHTTPSessionManager as raw ASGI."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "http" and scope.get("path", "") == "/mcp":
            request = Request(scope, receive, send)
            error, token_info = _validate_token(request)
            if error:
                await error(scope, receive, send)
                return
            # Stash token info + IP for audit in MCP handlers
            client = scope.get("client")
            ip = client[0] if client else "unknown"
            scope["state"] = scope.get("state", {})
            scope["state"]["audit_info"] = {
                "token_name": token_info.get("name", "unknown"),
                "role": token_info.get("role", "rw"),
                "ip": ip,
            }
            await session_manager.handle_request(scope, receive, send)
            return
        await self.app(scope, receive, send)


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_store()
    async with session_manager.run():
        yield


app = FastAPI(title="Linux MCP Server", version="1.0.0", lifespan=lifespan)

app.add_middleware(McpAuthMiddleware)

app.include_router(admin_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run("main:app", host=config.HOST, port=config.PORT, reload=False)
```

- [ ] **Step 5: Run permission tests**

Run: `cd /home/zhu/repos/mymcp && python3 -m pytest tests/test_permissions.py -v`
Expected: all 7 tests PASS

- [ ] **Step 6: Run all tests (excluding transfer)**

Run: `cd /home/zhu/repos/mymcp && python3 -m pytest tests/ -v --ignore=tests/test_transfer.py`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add mcp_server.py main.py tests/test_permissions.py
git commit -m "feat: add permission filtering and audit integration in MCP server"
```

---

### Task 6: Delete File Transfer Endpoints

**Files:**
- Delete: `tools/transfer.py`
- Delete: `tests/test_transfer.py`

- [ ] **Step 1: Delete the files**

```bash
rm tools/transfer.py tests/test_transfer.py
```

- [ ] **Step 2: Run all tests**

Run: `cd /home/zhu/repos/mymcp && python3 -m pytest tests/ -v`
Expected: all PASS

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "chore: remove unused file transfer endpoints"
```

---

### Task 7: Deploy Script — Audit Prompts

**Files:**
- Modify: `deploy/install.sh`

- [ ] **Step 1: Add audit prompts to the .env creation block**

In `deploy/install.sh`, in the `else` branch of the `.env` section (after the `GENERATED_TOKEN=...` line), add before the `cat > "${APP_DIR}/.env"`:

```bash
    AUDIT_ENABLED="false"
    AUDIT_LOG_DIR="/var/log/mymcp"
    if confirm "Enable audit logging? (recommended)" Y; then
        AUDIT_ENABLED="true"
        AUDIT_LOG_DIR=$(prompt_value "Audit log directory" "/var/log/mymcp")
        mkdir -p "${AUDIT_LOG_DIR}"
        chmod 750 "${AUDIT_LOG_DIR}"
    fi
```

Then update the heredoc to:

```bash
    cat > "${APP_DIR}/.env" <<EOF
MCP_ADMIN_TOKEN=${GENERATED_TOKEN}
MCP_HOST=0.0.0.0
MCP_PORT=${CONFIGURED_PORT}
MCP_TOKEN_FILE=${APP_DIR}/tokens.json
MCP_APP_DIR=${APP_DIR}
MCP_AUDIT_ENABLED=${AUDIT_ENABLED}
MCP_AUDIT_LOG_DIR=${AUDIT_LOG_DIR}
EOF
```

- [ ] **Step 2: Add audit info to the summary output**

After the admin token block in the summary, add:

```bash
if [ "$AUDIT_ENABLED" = "true" ] 2>/dev/null; then
    echo "  Audit log:    ${AUDIT_LOG_DIR}/audit.log"
fi
```

- [ ] **Step 3: Verify syntax**

Run: `bash -n deploy/install.sh`
Expected: no output

- [ ] **Step 4: Commit**

```bash
git add deploy/install.sh
git commit -m "feat: add audit logging prompts to install script"
```

---

### Task 8: Update README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update Features section**

Replace the Features section with:

```markdown
## Features

- **6 MCP tools**: `bash_execute`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`
- **Per-token permissions**: read-only (`ro`) or read-write (`rw`) roles
- **Audit logging**: JSON Lines audit trail of all tool invocations
- **Protected paths**: MCP's own files are protected from tool access
- **Multi-user auth**: Bearer token authentication with per-token management
- **Admin API**: create/revoke tokens without restarting the server
- **Streamable HTTP transport**: modern MCP protocol with session management
```

- [ ] **Step 2: Remove File Transfer section**

Delete the entire "## File Transfer" section (the heading, Upload/Download subsections, and all curl examples).

- [ ] **Step 3: Update Configuration table**

Replace the config table with:

```markdown
| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_ADMIN_TOKEN` | *(required)* | Admin token for managing user tokens |
| `MCP_HOST` | `0.0.0.0` | Bind address |
| `MCP_PORT` | `8765` | Listen port |
| `MCP_TOKEN_FILE` | `/opt/mymcp/tokens.json` | Token store path |
| `MCP_APP_DIR` | `/opt/mymcp` | Application directory (auto-protected) |
| `MCP_AUDIT_ENABLED` | `false` | Enable audit logging |
| `MCP_AUDIT_LOG_DIR` | `/var/log/mymcp` | Audit log directory (auto-protected) |
| `MCP_PROTECTED_PATHS` | *(empty)* | Additional protected paths, comma-separated |
```

- [ ] **Step 4: Update Managing Tokens section**

Update the create token curl examples to include role:

```bash
# Create a read-only token (default)
curl -X POST http://localhost:8765/admin/tokens \
  -H "Authorization: Bearer <ADMIN_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"name": "my-claude-desktop"}'
# → {"token": "tok_abc123...", "name": "my-claude-desktop", "role": "ro"}

# Create a read-write token
curl -X POST http://localhost:8765/admin/tokens \
  -H "Authorization: Bearer <ADMIN_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"name": "my-admin-client", "role": "rw"}'
# → {"token": "tok_def456...", "name": "my-admin-client", "role": "rw"}
```

- [ ] **Step 5: Update MCP Tools table**

Replace with:

```markdown
| Tool | Permission | Description |
|------|-----------|-------------|
| `bash_execute` | rw | Run any shell command |
| `read_file` | ro | Read file with line numbers and pagination |
| `write_file` | rw | Create or overwrite a file (max 10MB) |
| `edit_file` | rw | Replace a string in a file |
| `glob` | ro | Find files by pattern |
| `grep` | ro | Search file contents with regex |
```

- [ ] **Step 6: Add Audit Logging and Protected Paths sections before Security Note**

```markdown
## Audit Logging

When enabled (`MCP_AUDIT_ENABLED=true`), all tool invocations are logged to `<MCP_AUDIT_LOG_DIR>/audit.log` in JSON Lines format:

\`\`\`json
{"ts":"2026-04-10T15:30:22Z","token_name":"my-client","role":"rw","ip":"203.0.113.5","tool":"bash_execute","params":{"command":"apt update"},"result":"success","duration_ms":1523}
\`\`\`

Logs rotate automatically at 10MB with 5 backups.

## Protected Paths

MCP automatically protects its own installation directory and audit log directory from access via file tools (`read_file`, `write_file`, `edit_file`, `glob`, `grep`). This prevents AI clients from reading tokens, modifying server code, or tampering with audit logs.

Add extra protected paths via `MCP_PROTECTED_PATHS=/path/one,/path/two`.

Note: `bash_execute` is not subject to path protection — use `ro` tokens for untrusted clients.
```

- [ ] **Step 7: Update Security Note**

Replace with:

```markdown
## Security Note

This server grants system access to AI clients. Security measures:

- **Permissions**: New tokens default to `ro` (read-only). Only grant `rw` to trusted clients.
- **Audit**: Enable audit logging to track all tool invocations.
- **Protected paths**: Server files are automatically protected from tool access.
- **Network**: Run behind a firewall and consider TLS (e.g. via nginx reverse proxy).
```

- [ ] **Step 8: Run all tests one final time**

Run: `cd /home/zhu/repos/mymcp && python3 -m pytest tests/ -v`
Expected: all PASS

- [ ] **Step 9: Commit**

```bash
git add README.md
git commit -m "docs: update README with permissions, audit logging, and protected paths"
```

---

## Verification Checklist

After all tasks complete:

- [ ] `python3 -m pytest tests/ -v` — all tests pass
- [ ] `bash -n deploy/install.sh` — syntax OK
- [ ] No imports of `transfer_router` or `tools.transfer` remain: `grep -r "transfer" *.py tools/*.py`
- [ ] `python3 -c "import config; print(config.PROTECTED_PATHS)"` — shows default paths
- [ ] README has no mention of `/files/upload` or `/files/download`
- [ ] `write_file` tool description no longer references `/files/upload` endpoint
