# Linux MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python MCP server that exposes full Linux system control (bash, file ops, search) to AI clients over HTTP/SSE, secured with multi-user Bearer token authentication.

**Architecture:** FastAPI hosts the SSE transport (`/sse`, `/messages`) and companion HTTP routes (file transfer at `/files/*`, admin at `/admin/*`). Auth middleware via FastAPI `Depends` gates every route. Six MCP tools (bash_execute, read_file, write_file, edit_file, glob, grep) are implemented as standalone async functions and wired into the MCP `Server` instance in `mcp_server.py`.

**Tech Stack:** Python 3.11+, `fastapi`, `uvicorn`, `mcp` (official MCP Python SDK), `python-multipart`, `pytest`, `pytest-asyncio`, `httpx`

---

## File Map

| File | Responsibility |
|------|---------------|
| `config.py` | All constants and env var reads — one import, no side effects |
| `auth.py` | `TokenStore` class + `require_auth`/`require_admin` FastAPI deps + `admin_router` |
| `tools/bash.py` | `run_bash_execute()` async function |
| `tools/files.py` | `read_file()`, `write_file()`, `edit_file()`, `glob_files()`, `grep_files()` |
| `tools/transfer.py` | FastAPI router for `/files/upload` and `/files/download` |
| `mcp_server.py` | MCP `Server` instance, tool JSON schemas, `call_tool` dispatcher |
| `main.py` | FastAPI app wiring: SSE routes, routers, startup validation |
| `tests/conftest.py` | Shared pytest fixtures |
| `tests/test_auth.py` | `TokenStore` unit tests |
| `tests/test_admin.py` | Admin API integration tests |
| `tests/test_bash.py` | `bash_execute` tool tests |
| `tests/test_files.py` | File tool tests |
| `tests/test_transfer.py` | File transfer endpoint tests |
| `tests/test_mcp.py` | MCP tool dispatch tests |

---

## Task 1: Scaffold — requirements, config, gitignore, env example

**Files:**
- Create: `requirements.txt`
- Create: `config.py`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `tools/__init__.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: Create `requirements.txt`**

```
mcp>=1.0.0
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
python-multipart>=0.0.9
httpx>=0.27.0
pytest>=8.0.0
pytest-asyncio>=0.23.0
anyio>=4.0.0
```

- [ ] **Step 2: Create `config.py`**

```python
import os

HOST = os.getenv("MCP_HOST", "0.0.0.0")
PORT = int(os.getenv("MCP_PORT", "8765"))
TOKEN_FILE = os.getenv("MCP_TOKEN_FILE", "./tokens.json")
ADMIN_TOKEN = os.getenv("MCP_ADMIN_TOKEN", "")

# bash_execute output limits
BASH_MAX_OUTPUT_BYTES = 102400        # 100 KB default
BASH_MAX_OUTPUT_BYTES_HARD = 1048576  # 1 MB hard cap

# read_file limits
READ_FILE_DEFAULT_LIMIT = 2000        # lines
READ_FILE_MAX_LIMIT = 10000           # lines
READ_FILE_MAX_LINE_BYTES = 4096       # bytes per line

# write_file limit
WRITE_FILE_MAX_BYTES = 10 * 1024 * 1024  # 10 MB

# edit_file limit
EDIT_STRING_MAX_BYTES = 1024 * 1024      # 1 MB per old/new string

# glob limit
GLOB_MAX_RESULTS = 1000

# grep limits
GREP_DEFAULT_MAX_RESULTS = 250
GREP_MAX_RESULTS = 5000
```

- [ ] **Step 3: Create `.gitignore`**

```
tokens.json
.env
__pycache__/
*.pyc
*.pyo
.pytest_cache/
.venv/
dist/
*.egg-info/
```

- [ ] **Step 4: Create `.env.example`**

```bash
MCP_HOST=0.0.0.0
MCP_PORT=8765
MCP_TOKEN_FILE=./tokens.json
MCP_ADMIN_TOKEN=adm_change_me
```

- [ ] **Step 5: Create empty `tools/__init__.py` and `tests/__init__.py`**

Both files are empty.

- [ ] **Step 6: Install dependencies**

```bash
pip install -r requirements.txt
```

Expected: All packages install without errors.

- [ ] **Step 7: Verify config imports cleanly**

```bash
python -c "import config; print(config.PORT)"
```

Expected: `8765`

- [ ] **Step 8: Commit**

```bash
git add requirements.txt config.py .gitignore .env.example tools/__init__.py tests/__init__.py
git commit -m "feat: scaffold config, requirements, gitignore"
```

---

## Task 2: TokenStore — core auth logic

**Files:**
- Create: `auth.py`
- Create: `tests/test_auth.py`

- [ ] **Step 1: Write failing tests in `tests/test_auth.py`**

```python
import pytest
from pathlib import Path
from auth import TokenStore


def make_store(tmp_path: Path) -> TokenStore:
    return TokenStore(str(tmp_path / "tokens.json"), "adm_testadmin")


def test_create_token_has_tok_prefix(tmp_path):
    store = make_store(tmp_path)
    token = store.create_token("client-a")
    assert token.startswith("tok_")


def test_validate_returns_info_for_valid_token(tmp_path):
    store = make_store(tmp_path)
    token = store.create_token("client-a")
    info = store.validate(token)
    assert info is not None
    assert info["name"] == "client-a"
    assert info["enabled"] is True


def test_validate_returns_none_for_unknown_token(tmp_path):
    store = make_store(tmp_path)
    assert store.validate("tok_doesnotexist") is None


def test_revoke_removes_token(tmp_path):
    store = make_store(tmp_path)
    token = store.create_token("client-b")
    assert store.revoke_token(token) is True
    assert store.validate(token) is None


def test_revoke_returns_false_for_unknown_token(tmp_path):
    store = make_store(tmp_path)
    assert store.revoke_token("tok_unknown") is False


def test_admin_token_not_valid_as_user_token(tmp_path):
    store = make_store(tmp_path)
    assert store.validate("adm_testadmin") is None


def test_tokens_persist_across_instances(tmp_path):
    path = str(tmp_path / "tokens.json")
    store1 = TokenStore(path, "adm_testadmin")
    token = store1.create_token("client-c")

    store2 = TokenStore(path, "adm_testadmin")
    assert store2.validate(token) is not None


def test_list_tokens_returns_all(tmp_path):
    store = make_store(tmp_path)
    t1 = store.create_token("client-1")
    t2 = store.create_token("client-2")
    all_tokens = store.list_tokens()
    assert t1 in all_tokens
    assert t2 in all_tokens


def test_validate_updates_last_used(tmp_path):
    store = make_store(tmp_path)
    token = store.create_token("client-d")
    assert store.validate(token)["last_used"] is not None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_auth.py -v
```

Expected: `ImportError` or `ModuleNotFoundError` (auth.py doesn't exist yet).

- [ ] **Step 3: Implement `auth.py` — TokenStore class only**

```python
import json
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path


class TokenStore:
    def __init__(self, path: str, admin_token: str):
        self.path = Path(path)
        self.admin_token = admin_token
        self._lock = threading.Lock()
        self._data: dict = {"tokens": {}, "admin_token": admin_token}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            with open(self.path) as f:
                self._data = json.load(f)
            self._data["admin_token"] = self.admin_token
        else:
            self._data = {"tokens": {}, "admin_token": self.admin_token}
            self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(self._data, f, indent=2)

    def validate(self, token: str) -> dict | None:
        """Returns token info dict if valid and enabled, else None."""
        with self._lock:
            info = self._data["tokens"].get(token)
            if info is None or not info.get("enabled", False):
                return None
            info["last_used"] = datetime.now(timezone.utc).isoformat()
            self._save()
            return dict(info)

    def create_token(self, name: str) -> str:
        token = "tok_" + secrets.token_hex(16)
        with self._lock:
            self._data["tokens"][token] = {
                "name": name,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "last_used": None,
                "enabled": True,
            }
            self._save()
        return token

    def revoke_token(self, token: str) -> bool:
        """Returns True if token existed and was removed, False otherwise."""
        with self._lock:
            if token not in self._data["tokens"]:
                return False
            del self._data["tokens"][token]
            self._save()
            return True

    def list_tokens(self) -> dict:
        with self._lock:
            return dict(self._data["tokens"])
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_auth.py -v
```

Expected: All 10 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add auth.py tests/test_auth.py
git commit -m "feat: implement TokenStore with persist, validate, create, revoke"
```

---

## Task 3: Auth middleware + Admin API

**Files:**
- Modify: `auth.py` — add FastAPI deps, admin router
- Create: `tests/conftest.py`
- Create: `tests/test_admin.py`

- [ ] **Step 1: Write failing tests in `tests/test_admin.py`**

```python
import pytest
from httpx import AsyncClient, ASGITransport
from fastapi import FastAPI
from auth import TokenStore, require_auth, require_admin, admin_router, get_store


def make_app(store: TokenStore) -> FastAPI:
    app = FastAPI()
    app.include_router(admin_router)
    app.dependency_overrides[get_store] = lambda: store

    @app.get("/protected")
    async def protected(info: dict = require_auth.__wrapped__ if hasattr(require_auth, '__wrapped__') else require_auth):
        return {"ok": True}

    return app


@pytest.fixture
def store(tmp_path):
    return TokenStore(str(tmp_path / "tokens.json"), "adm_testadmin")


@pytest.fixture
async def client(store):
    app = FastAPI()
    app.include_router(admin_router)
    app.dependency_overrides[get_store] = lambda: store
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.mark.anyio
async def test_create_token(client):
    resp = await client.post(
        "/admin/tokens",
        json={"name": "my-client"},
        headers={"Authorization": "Bearer adm_testadmin"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["token"].startswith("tok_")
    assert data["name"] == "my-client"


@pytest.mark.anyio
async def test_create_token_wrong_admin_token(client):
    resp = await client.post(
        "/admin/tokens",
        json={"name": "x"},
        headers={"Authorization": "Bearer wrong"},
    )
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_list_tokens(client, store):
    t = store.create_token("existing")
    resp = await client.get(
        "/admin/tokens",
        headers={"Authorization": "Bearer adm_testadmin"},
    )
    assert resp.status_code == 200
    assert t in resp.json()


@pytest.mark.anyio
async def test_revoke_token(client, store):
    t = store.create_token("to-revoke")
    resp = await client.delete(
        f"/admin/tokens/{t}",
        headers={"Authorization": "Bearer adm_testadmin"},
    )
    assert resp.status_code == 200
    assert store.validate(t) is None


@pytest.mark.anyio
async def test_revoke_unknown_token(client):
    resp = await client.delete(
        "/admin/tokens/tok_unknown",
        headers={"Authorization": "Bearer adm_testadmin"},
    )
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_missing_auth_header_returns_401(client):
    resp = await client.get("/admin/tokens")
    assert resp.status_code == 401
```

- [ ] **Step 2: Create `tests/conftest.py`**

```python
import pytest

# Use asyncio backend for all async tests
pytest_plugins = ["anyio"]
```

Also add `pytest.ini` (or `pyproject.toml` section) so pytest-anyio works:

```ini
# pytest.ini
[pytest]
asyncio_mode = auto
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
pytest tests/test_admin.py -v
```

Expected: `ImportError` — `require_auth`, `admin_router`, `get_store` not defined yet.

- [ ] **Step 4: Add FastAPI deps and admin router to `auth.py`**

Append to `auth.py` (after the `TokenStore` class):

```python
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

_store: TokenStore | None = None


def get_store() -> TokenStore:
    """FastAPI dependency — returns the singleton TokenStore.
    Override in tests via app.dependency_overrides[get_store]."""
    global _store
    if _store is None:
        import config
        if not config.ADMIN_TOKEN:
            raise RuntimeError("MCP_ADMIN_TOKEN environment variable is required")
        _store = TokenStore(config.TOKEN_FILE, config.ADMIN_TOKEN)
    return _store


async def require_auth(
    request: Request,
    store: TokenStore = Depends(get_store),
) -> dict:
    """FastAPI dependency — validates user Bearer token. Returns token info."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    token = auth[7:]
    info = store.validate(token)
    if info is None:
        raise HTTPException(status_code=401, detail="Invalid or disabled token")
    return info


async def require_admin(
    request: Request,
    store: TokenStore = Depends(get_store),
) -> None:
    """FastAPI dependency — validates admin Bearer token."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    token = auth[7:]
    if token != store.admin_token:
        raise HTTPException(status_code=403, detail="Admin token required")


class _CreateTokenRequest(BaseModel):
    name: str


admin_router = APIRouter(
    prefix="/admin",
    dependencies=[Depends(require_admin)],
)


@admin_router.post("/tokens")
async def create_token(
    body: _CreateTokenRequest,
    store: TokenStore = Depends(get_store),
):
    token = store.create_token(body.name)
    return {"token": token, "name": body.name}


@admin_router.delete("/tokens/{token}")
async def revoke_token(token: str, store: TokenStore = Depends(get_store)):
    found = store.revoke_token(token)
    if not found:
        raise HTTPException(status_code=404, detail="Token not found")
    return {"revoked": token}


@admin_router.get("/tokens")
async def list_tokens(store: TokenStore = Depends(get_store)):
    return store.list_tokens()
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/test_admin.py -v
```

Expected: All 6 tests PASS.

- [ ] **Step 6: Verify existing TokenStore tests still pass**

```bash
pytest tests/test_auth.py -v
```

Expected: All 10 tests PASS.

- [ ] **Step 7: Commit**

```bash
git add auth.py tests/conftest.py tests/test_admin.py pytest.ini
git commit -m "feat: add auth middleware, admin API routes, tests"
```

---

## Task 4: bash_execute tool

**Files:**
- Create: `tools/bash.py`
- Create: `tests/test_bash.py`

- [ ] **Step 1: Write failing tests in `tests/test_bash.py`**

```python
import pytest
from tools.bash import run_bash_execute


@pytest.mark.anyio
async def test_simple_command_succeeds():
    result = await run_bash_execute("echo hello")
    assert result["stdout"].strip() == "hello"
    assert result["stderr"] == ""
    assert result["exit_code"] == 0
    assert result["timed_out"] is False


@pytest.mark.anyio
async def test_nonzero_exit_code():
    result = await run_bash_execute("exit 42", working_dir="/tmp")
    assert result["exit_code"] == 42


@pytest.mark.anyio
async def test_stderr_captured():
    result = await run_bash_execute("ls /path_that_does_not_exist_xyz")
    assert result["exit_code"] != 0
    assert len(result["stderr"]) > 0


@pytest.mark.anyio
async def test_working_dir_is_respected(tmp_path):
    result = await run_bash_execute("pwd", working_dir=str(tmp_path))
    assert result["stdout"].strip() == str(tmp_path)


@pytest.mark.anyio
async def test_timeout_kills_process():
    result = await run_bash_execute("sleep 10", timeout=1)
    assert result["timed_out"] is True
    assert result["exit_code"] == -1


@pytest.mark.anyio
async def test_output_truncated_when_over_limit():
    # Generate 200KB output, limit to 1000 bytes
    result = await run_bash_execute(
        'python3 -c "print(\'x\' * 200000)"',
        timeout=10,
        max_output_bytes=1000,
    )
    assert "[TRUNCATED" in result["stdout"]


@pytest.mark.anyio
async def test_bad_working_dir_returns_error():
    result = await run_bash_execute("ls", working_dir="/nonexistent_dir_xyz_abc")
    assert result.get("success") is False
    assert "error" in result
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_bash.py -v
```

Expected: `ImportError` — `tools.bash` doesn't exist.

- [ ] **Step 3: Implement `tools/bash.py`**

```python
import asyncio
import config


async def run_bash_execute(
    command: str,
    timeout: int = 30,
    working_dir: str = "/",
    max_output_bytes: int = config.BASH_MAX_OUTPUT_BYTES,
) -> dict:
    timeout = min(max(1, timeout), 600)
    max_output_bytes = min(max(1, max_output_bytes), config.BASH_MAX_OUTPUT_BYTES_HARD)

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=working_dir,
        )
    except FileNotFoundError:
        return {
            "success": False,
            "error": "FileNotFoundError",
            "message": f"Working directory not found: {working_dir}",
            "suggestion": "Check that the working_dir path exists",
        }
    except PermissionError as e:
        return {
            "success": False,
            "error": "PermissionError",
            "message": str(e),
            "suggestion": "Check directory permissions",
        }

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=float(timeout)
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return {
            "stdout": "",
            "stderr": f"Command timed out after {timeout}s",
            "exit_code": -1,
            "timed_out": True,
        }

    def _truncate(data: bytes, limit: int) -> str:
        if len(data) <= limit:
            return data.decode("utf-8", errors="replace")
        shown = data[:limit].decode("utf-8", errors="replace")
        return f"{shown}\n[TRUNCATED: total {len(data)} bytes, showing first {limit} bytes]"

    return {
        "stdout": _truncate(stdout_bytes, max_output_bytes),
        "stderr": _truncate(stderr_bytes, max_output_bytes),
        "exit_code": proc.returncode,
        "timed_out": False,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_bash.py -v
```

Expected: All 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/bash.py tests/test_bash.py
git commit -m "feat: implement bash_execute tool with timeout and output truncation"
```

---

## Task 5: read_file tool

**Files:**
- Create: `tools/files.py` (read_file only for now)
- Create: `tests/test_files.py`

- [ ] **Step 1: Write failing tests for read_file in `tests/test_files.py`**

```python
import pytest
from tools.files import read_file


@pytest.mark.anyio
async def test_read_file_basic(tmp_path):
    f = tmp_path / "test.txt"
    f.write_text("line one\nline two\nline three\n")
    result = await read_file(str(f))
    assert "   1\tline one" in result["content"]
    assert "   3\tline three" in result["content"]
    assert result["total_lines"] == 3
    assert result["truncated"] is False


@pytest.mark.anyio
async def test_read_file_offset_and_limit(tmp_path):
    f = tmp_path / "big.txt"
    lines = [f"line {i}" for i in range(1, 201)]
    f.write_text("\n".join(lines))
    result = await read_file(str(f), offset=5, limit=3)
    assert "   5\tline 5" in result["content"]
    assert "   6\tline 6" in result["content"]
    assert "   7\tline 7" in result["content"]
    assert "   8\tline 8" not in result["content"]


@pytest.mark.anyio
async def test_read_file_truncated_flag(tmp_path):
    f = tmp_path / "big.txt"
    f.write_text("\n".join(f"line {i}" for i in range(1, 3001)))
    result = await read_file(str(f), limit=2000)
    assert result["total_lines"] == 3000
    assert result["truncated"] is True


@pytest.mark.anyio
async def test_read_file_not_found():
    result = await read_file("/nonexistent_xyz/file.txt")
    assert result["success"] is False
    assert result["error"] == "FileNotFoundError"


@pytest.mark.anyio
async def test_read_file_is_directory(tmp_path):
    result = await read_file(str(tmp_path))
    assert result["success"] is False
    assert result["error"] == "IsADirectoryError"


@pytest.mark.anyio
async def test_read_file_long_line_truncated(tmp_path):
    f = tmp_path / "long.txt"
    f.write_bytes(b"x" * 10000 + b"\n")
    result = await read_file(str(f))
    assert "[LINE TRUNCATED]" in result["content"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_files.py::test_read_file_basic -v
```

Expected: `ImportError`.

- [ ] **Step 3: Implement `read_file` in `tools/files.py`**

```python
import config


async def read_file(
    file_path: str,
    offset: int = 1,
    limit: int = config.READ_FILE_DEFAULT_LIMIT,
) -> dict:
    limit = min(max(1, limit), config.READ_FILE_MAX_LIMIT)
    offset = max(1, offset)

    try:
        with open(file_path, "rb") as f:
            raw_lines = f.readlines()
    except FileNotFoundError:
        return {
            "success": False,
            "error": "FileNotFoundError",
            "message": f"File not found: {file_path}",
            "suggestion": "Check the file path",
        }
    except IsADirectoryError:
        return {
            "success": False,
            "error": "IsADirectoryError",
            "message": f"{file_path} is a directory",
            "suggestion": "Use glob to list directory contents",
        }
    except PermissionError as e:
        return {
            "success": False,
            "error": "PermissionError",
            "message": str(e),
            "suggestion": "Check file read permissions",
        }

    total_lines = len(raw_lines)
    selected = raw_lines[offset - 1 : offset - 1 + limit]
    output_lines = []

    for i, raw_line in enumerate(selected, start=offset):
        line = raw_line.rstrip(b"\n").rstrip(b"\r")
        if len(line) > config.READ_FILE_MAX_LINE_BYTES:
            line = line[: config.READ_FILE_MAX_LINE_BYTES]
            decoded = line.decode("utf-8", errors="replace") + " [LINE TRUNCATED]"
        else:
            decoded = line.decode("utf-8", errors="replace")
        output_lines.append(f"{i:4}\t{decoded}")

    return {
        "content": "\n".join(output_lines),
        "total_lines": total_lines,
        "truncated": (offset - 1 + limit) < total_lines,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_files.py -v
```

Expected: All 6 read_file tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/files.py tests/test_files.py
git commit -m "feat: implement read_file with pagination and line truncation"
```

---

## Task 6: write_file + edit_file tools

**Files:**
- Modify: `tools/files.py` — add write_file, edit_file
- Modify: `tests/test_files.py` — add write_file and edit_file tests

- [ ] **Step 1: Append failing tests to `tests/test_files.py`**

```python
from tools.files import write_file, edit_file


@pytest.mark.anyio
async def test_write_file_creates_file(tmp_path):
    path = str(tmp_path / "new.txt")
    result = await write_file(path, "hello world\n")
    assert result["success"] is True
    assert result["bytes_written"] == 12
    assert (tmp_path / "new.txt").read_text() == "hello world\n"


@pytest.mark.anyio
async def test_write_file_overwrites_existing(tmp_path):
    f = tmp_path / "existing.txt"
    f.write_text("old content")
    result = await write_file(str(f), "new content")
    assert result["success"] is True
    assert f.read_text() == "new content"


@pytest.mark.anyio
async def test_write_file_creates_parent_dirs(tmp_path):
    path = str(tmp_path / "deep" / "nested" / "file.txt")
    result = await write_file(path, "data")
    assert result["success"] is True


@pytest.mark.anyio
async def test_write_file_too_large():
    import config
    big = "x" * (config.WRITE_FILE_MAX_BYTES + 1)
    result = await write_file("/tmp/toobig.txt", big)
    assert result["success"] is False
    assert result["error"] == "FileTooLarge"


@pytest.mark.anyio
async def test_edit_file_replaces_string(tmp_path):
    f = tmp_path / "code.py"
    f.write_text("def old_name():\n    pass\n")
    result = await edit_file(str(f), "old_name", "new_name")
    assert result["success"] is True
    assert result["replacements"] == 1
    assert "new_name" in f.read_text()


@pytest.mark.anyio
async def test_edit_file_ambiguous_fails(tmp_path):
    f = tmp_path / "dup.txt"
    f.write_text("foo foo foo")
    result = await edit_file(str(f), "foo", "bar")
    assert result["success"] is False
    assert result["error"] == "AmbiguousMatch"


@pytest.mark.anyio
async def test_edit_file_replace_all(tmp_path):
    f = tmp_path / "dup.txt"
    f.write_text("foo foo foo")
    result = await edit_file(str(f), "foo", "bar", replace_all=True)
    assert result["success"] is True
    assert result["replacements"] == 3
    assert f.read_text() == "bar bar bar"


@pytest.mark.anyio
async def test_edit_file_string_not_found(tmp_path):
    f = tmp_path / "code.py"
    f.write_text("hello world")
    result = await edit_file(str(f), "nonexistent_string", "replacement")
    assert result["success"] is False
    assert result["error"] == "StringNotFound"


@pytest.mark.anyio
async def test_edit_file_not_found():
    result = await edit_file("/nonexistent_xyz/file.py", "old", "new")
    assert result["success"] is False
    assert result["error"] == "FileNotFoundError"
```

- [ ] **Step 2: Run new tests to verify they fail**

```bash
pytest tests/test_files.py -k "write_file or edit_file" -v
```

Expected: `ImportError` — `write_file` and `edit_file` not defined yet.

- [ ] **Step 3: Append `write_file` and `edit_file` to `tools/files.py`**

```python
import os


async def write_file(file_path: str, content: str) -> dict:
    content_bytes = content.encode("utf-8")
    if len(content_bytes) > config.WRITE_FILE_MAX_BYTES:
        return {
            "success": False,
            "error": "FileTooLarge",
            "message": (
                f"Content is {len(content_bytes)} bytes, "
                f"max is {config.WRITE_FILE_MAX_BYTES} (10MB)"
            ),
            "suggestion": "Use the /files/upload endpoint for large files",
        }
    try:
        parent = os.path.dirname(os.path.abspath(file_path))
        os.makedirs(parent, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        return {"success": True, "bytes_written": len(content_bytes)}
    except PermissionError as e:
        return {
            "success": False,
            "error": "PermissionError",
            "message": str(e),
            "suggestion": "Check write permissions",
        }


async def edit_file(
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> dict:
    if len(old_string.encode("utf-8")) > config.EDIT_STRING_MAX_BYTES:
        return {
            "success": False,
            "error": "FileTooLarge",
            "message": "old_string exceeds 1MB limit",
        }
    if len(new_string.encode("utf-8")) > config.EDIT_STRING_MAX_BYTES:
        return {
            "success": False,
            "error": "FileTooLarge",
            "message": "new_string exceeds 1MB limit",
        }

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except FileNotFoundError:
        return {
            "success": False,
            "error": "FileNotFoundError",
            "message": f"File not found: {file_path}",
        }
    except PermissionError as e:
        return {"success": False, "error": "PermissionError", "message": str(e)}

    count = content.count(old_string)
    if count == 0:
        return {
            "success": False,
            "error": "StringNotFound",
            "message": "old_string not found in file",
        }
    if count > 1 and not replace_all:
        return {
            "success": False,
            "error": "AmbiguousMatch",
            "message": (
                f"old_string appears {count} times. "
                "Set replace_all=true to replace all occurrences."
            ),
        }

    new_content = content.replace(old_string, new_string)
    replacements = count if replace_all else 1
    if not replace_all:
        new_content = content.replace(old_string, new_string, 1)

    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        return {"success": True, "replacements": replacements}
    except PermissionError as e:
        return {"success": False, "error": "PermissionError", "message": str(e)}
```

- [ ] **Step 4: Run all file tests**

```bash
pytest tests/test_files.py -v
```

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/files.py tests/test_files.py
git commit -m "feat: implement write_file and edit_file tools"
```

---

## Task 7: glob + grep tools

**Files:**
- Modify: `tools/files.py` — add glob_files, grep_files
- Modify: `tests/test_files.py` — add glob and grep tests

- [ ] **Step 1: Append failing tests to `tests/test_files.py`**

```python
from tools.files import glob_files, grep_files


@pytest.mark.anyio
async def test_glob_finds_files(tmp_path):
    (tmp_path / "a.py").write_text("a")
    (tmp_path / "b.py").write_text("b")
    (tmp_path / "c.txt").write_text("c")
    result = await glob_files("*.py", path=str(tmp_path))
    assert result["count"] >= 2
    assert any(p.endswith("a.py") for p in result["files"])
    assert any(p.endswith("b.py") for p in result["files"])
    assert not any(p.endswith("c.txt") for p in result["files"])


@pytest.mark.anyio
async def test_glob_recursive(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "deep.py").write_text("x")
    result = await glob_files("**/*.py", path=str(tmp_path))
    assert any("deep.py" in p for p in result["files"])


@pytest.mark.anyio
async def test_glob_empty_result(tmp_path):
    result = await glob_files("*.nonexistent", path=str(tmp_path))
    assert result["count"] == 0
    assert result["files"] == []


@pytest.mark.anyio
async def test_grep_content_mode(tmp_path):
    f = tmp_path / "log.txt"
    f.write_text("error: connection failed\ninfo: all good\nerror: timeout\n")
    result = await grep_files("error", path=str(tmp_path))
    assert result["match_count"] == 2
    assert "error: connection failed" in result["results"]
    assert "error: timeout" in result["results"]
    assert "all good" not in result["results"]


@pytest.mark.anyio
async def test_grep_files_mode(tmp_path):
    (tmp_path / "match.txt").write_text("contains error here")
    (tmp_path / "nomatch.txt").write_text("nothing relevant")
    result = await grep_files("error", path=str(tmp_path), output_mode="files")
    assert any("match.txt" in r for r in result["results"].splitlines())
    assert not any("nomatch.txt" in r for r in result["results"].splitlines())


@pytest.mark.anyio
async def test_grep_case_insensitive(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("ERROR found here\n")
    result = await grep_files("error", path=str(tmp_path), case_insensitive=True)
    assert result["match_count"] >= 1


@pytest.mark.anyio
async def test_grep_glob_filter(tmp_path):
    (tmp_path / "a.log").write_text("target line\n")
    (tmp_path / "b.txt").write_text("target line\n")
    result = await grep_files("target", path=str(tmp_path), glob="*.log")
    assert any("a.log" in line for line in result["results"].splitlines())
    assert not any("b.txt" in line for line in result["results"].splitlines())


@pytest.mark.anyio
async def test_grep_truncates_at_max_results(tmp_path):
    f = tmp_path / "big.log"
    f.write_text("\n".join(f"match line {i}" for i in range(300)))
    result = await grep_files("match", path=str(tmp_path), max_results=10)
    assert "[TRUNCATED" in result["results"]
    assert result["match_count"] == 300
```

- [ ] **Step 2: Run new tests to verify they fail**

```bash
pytest tests/test_files.py -k "glob or grep" -v
```

Expected: `ImportError`.

- [ ] **Step 3: Append `glob_files` and `grep_files` to `tools/files.py`**

```python
import asyncio
import glob as _glob_module
import shutil


async def glob_files(pattern: str, path: str = "/") -> dict:
    try:
        import os
        base = os.path.abspath(path)
        full_pattern = os.path.join(base, pattern)
        matches = _glob_module.glob(full_pattern, recursive=True)
        matches.sort(
            key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0,
            reverse=True,
        )
        truncated = len(matches) > config.GLOB_MAX_RESULTS
        return {
            "files": matches[: config.GLOB_MAX_RESULTS],
            "count": len(matches),
            "truncated": truncated,
        }
    except Exception as e:
        return {"success": False, "error": type(e).__name__, "message": str(e)}


async def grep_files(
    pattern: str,
    path: str = "/",
    glob: str | None = None,
    output_mode: str = "content",
    context_lines: int = 0,
    max_results: int = config.GREP_DEFAULT_MAX_RESULTS,
    case_insensitive: bool = False,
) -> dict:
    max_results = min(max(1, max_results), config.GREP_MAX_RESULTS)
    if shutil.which("rg"):
        return await _grep_rg(
            pattern, path, glob, output_mode, context_lines, max_results, case_insensitive
        )
    return await _grep_python(
        pattern, path, glob, output_mode, context_lines, max_results, case_insensitive
    )


async def _grep_rg(
    pattern, path, glob_pattern, output_mode, context_lines, max_results, case_insensitive
) -> dict:
    cmd = ["rg", "--no-heading", "-n"]
    if case_insensitive:
        cmd.append("-i")
    if context_lines:
        cmd.extend(["-C", str(context_lines)])
    if glob_pattern:
        cmd.extend(["--glob", glob_pattern])
    if output_mode == "files":
        cmd.append("-l")
    elif output_mode == "count":
        cmd.append("--count")
    cmd.extend([pattern, path])

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
        lines = stdout.decode("utf-8", errors="replace").splitlines()
    except asyncio.TimeoutError:
        return {
            "success": False,
            "error": "TimeoutError",
            "message": "grep timed out after 60s",
        }

    total = len(lines)
    truncated = total > max_results
    result_str = "\n".join(lines[:max_results])
    if truncated:
        result_str += f"\n[TRUNCATED: {total - max_results} more matches not shown]"
    return {"results": result_str, "match_count": total}


async def _grep_python(
    pattern, path, glob_pattern, output_mode, context_lines, max_results, case_insensitive
) -> dict:
    import re
    import os
    import fnmatch

    flags = re.IGNORECASE if case_insensitive else 0
    try:
        regex = re.compile(pattern, flags)
    except re.error as e:
        return {"success": False, "error": "InvalidRegex", "message": str(e)}

    matches = []
    if os.path.isfile(path):
        files_to_search = [path]
    else:
        files_to_search = []
        for root, _dirs, files in os.walk(path):
            for fname in files:
                if glob_pattern and not fnmatch.fnmatch(fname, glob_pattern):
                    continue
                files_to_search.append(os.path.join(root, fname))

    for fpath in files_to_search:
        try:
            with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except (PermissionError, IsADirectoryError, OSError):
            continue

        for lineno, line in enumerate(lines, 1):
            if regex.search(line):
                if output_mode == "files":
                    matches.append(fpath)
                    break
                elif output_mode == "count":
                    matches.append(f"{fpath}: {sum(1 for l in lines if regex.search(l))}")
                    break
                else:
                    matches.append(f"{fpath}:{lineno}:{line.rstrip()}")
                    if len(matches) >= max_results:
                        break
        if len(matches) >= max_results and output_mode == "content":
            break

    total = len(matches)
    truncated = total > max_results
    result_str = "\n".join(matches[:max_results])
    if truncated:
        result_str += f"\n[TRUNCATED: {total - max_results} more matches not shown]"
    return {"results": result_str, "match_count": total}
```

- [ ] **Step 4: Run all file tool tests**

```bash
pytest tests/test_files.py -v
```

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/files.py tests/test_files.py
git commit -m "feat: implement glob_files and grep_files tools"
```

---

## Task 8: File transfer HTTP endpoints

**Files:**
- Create: `tools/transfer.py`
- Create: `tests/test_transfer.py`

- [ ] **Step 1: Write failing tests in `tests/test_transfer.py`**

```python
import pytest
from httpx import AsyncClient, ASGITransport
from fastapi import FastAPI
from auth import TokenStore, get_store, require_auth
from tools.transfer import transfer_router


@pytest.fixture
def store(tmp_path):
    return TokenStore(str(tmp_path / "tokens.json"), "adm_testadmin")


@pytest.fixture
async def client(store, tmp_path):
    app = FastAPI()
    app.include_router(transfer_router)
    app.dependency_overrides[get_store] = lambda: store
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest.fixture
def auth_headers(store):
    token = store.create_token("test-client")
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.anyio
async def test_upload_file(client, auth_headers, tmp_path):
    dest = str(tmp_path / "uploaded.txt")
    resp = await client.post(
        "/files/upload",
        headers=auth_headers,
        files={"file": ("test.txt", b"hello upload", "text/plain")},
        data={"dest_path": dest},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["path"] == dest
    assert data["size"] == 12
    assert (tmp_path / "uploaded.txt").read_bytes() == b"hello upload"


@pytest.mark.anyio
async def test_upload_requires_dest_path(client, auth_headers):
    resp = await client.post(
        "/files/upload",
        headers=auth_headers,
        files={"file": ("test.txt", b"data", "text/plain")},
    )
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_upload_requires_auth(client, tmp_path):
    resp = await client.post(
        "/files/upload",
        files={"file": ("test.txt", b"data", "text/plain")},
        data={"dest_path": str(tmp_path / "x.txt")},
    )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_download_file(client, auth_headers, tmp_path):
    f = tmp_path / "download_me.txt"
    f.write_bytes(b"file content here")
    resp = await client.get(
        f"/files/download",
        headers=auth_headers,
        params={"path": str(f)},
    )
    assert resp.status_code == 200
    assert resp.content == b"file content here"
    assert "attachment" in resp.headers.get("content-disposition", "")


@pytest.mark.anyio
async def test_download_not_found(client, auth_headers):
    resp = await client.get(
        "/files/download",
        headers=auth_headers,
        params={"path": "/nonexistent_xyz/file.txt"},
    )
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_download_requires_auth(client, tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("data")
    resp = await client.get("/files/download", params={"path": str(f)})
    assert resp.status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_transfer.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Implement `tools/transfer.py`**

```python
import os
from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from auth import require_auth

transfer_router = APIRouter(prefix="/files")

CHUNK_SIZE = 1024 * 1024  # 1 MB streaming chunks


@transfer_router.post("/upload")
async def upload_file(
    file: UploadFile,
    dest_path: str = Form(...),
    _: dict = Depends(require_auth),
):
    parent = os.path.dirname(os.path.abspath(dest_path))
    os.makedirs(parent, exist_ok=True)

    total = 0
    try:
        with open(dest_path, "wb") as f:
            while chunk := await file.read(CHUNK_SIZE):
                f.write(chunk)
                total += len(chunk)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    return {"path": dest_path, "size": total}


@transfer_router.get("/download")
async def download_file(
    path: str,
    _: dict = Depends(require_auth),
):
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"File not found: {path}")

    filename = os.path.basename(path)

    def iterfile():
        with open(path, "rb") as f:
            while chunk := f.read(CHUNK_SIZE):
                yield chunk

    return StreamingResponse(
        iterfile(),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_transfer.py -v
```

Expected: All 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/transfer.py tests/test_transfer.py
git commit -m "feat: implement file upload/download endpoints with streaming"
```

---

## Task 9: MCP server — tool registration

**Files:**
- Create: `mcp_server.py`
- Create: `tests/test_mcp.py`

- [ ] **Step 1: Write failing tests in `tests/test_mcp.py`**

```python
import pytest
import json
from mcp_server import dispatch_tool


@pytest.mark.anyio
async def test_dispatch_bash_execute():
    result = await dispatch_tool("bash_execute", {"command": "echo mcp_test"})
    data = json.loads(result)
    assert "mcp_test" in data["stdout"]
    assert data["exit_code"] == 0


@pytest.mark.anyio
async def test_dispatch_read_file(tmp_path):
    f = tmp_path / "hello.txt"
    f.write_text("hello mcp\n")
    result = await dispatch_tool("read_file", {"file_path": str(f)})
    data = json.loads(result)
    assert "hello mcp" in data["content"]


@pytest.mark.anyio
async def test_dispatch_write_file(tmp_path):
    path = str(tmp_path / "out.txt")
    result = await dispatch_tool("write_file", {"file_path": path, "content": "written"})
    data = json.loads(result)
    assert data["success"] is True
    assert (tmp_path / "out.txt").read_text() == "written"


@pytest.mark.anyio
async def test_dispatch_edit_file(tmp_path):
    f = tmp_path / "edit.txt"
    f.write_text("replace_me")
    result = await dispatch_tool(
        "edit_file",
        {"file_path": str(f), "old_string": "replace_me", "new_string": "replaced"},
    )
    data = json.loads(result)
    assert data["success"] is True


@pytest.mark.anyio
async def test_dispatch_glob(tmp_path):
    (tmp_path / "a.py").write_text("")
    result = await dispatch_tool("glob", {"pattern": "*.py", "path": str(tmp_path)})
    data = json.loads(result)
    assert data["count"] >= 1


@pytest.mark.anyio
async def test_dispatch_grep(tmp_path):
    (tmp_path / "f.txt").write_text("needle in haystack\n")
    result = await dispatch_tool("grep", {"pattern": "needle", "path": str(tmp_path)})
    data = json.loads(result)
    assert data["match_count"] >= 1


@pytest.mark.anyio
async def test_dispatch_unknown_tool():
    result = await dispatch_tool("nonexistent_tool", {})
    data = json.loads(result)
    assert data["success"] is False
    assert data["error"] == "UnknownTool"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_mcp.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Implement `mcp_server.py`**

```python
import json
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp import types

import config
from tools.bash import run_bash_execute
from tools.files import read_file, write_file, edit_file, glob_files, grep_files

server = Server("linux-server")
sse_transport = SseServerTransport("/messages")


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
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
        ),
        types.Tool(
            name="read_file",
            description=(
                "Read a file with line numbers. Supports pagination via offset/limit."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Absolute path to file"},
                    "offset": {"type": "integer", "description": "Start line 1-based (default 1)"},
                    "limit": {"type": "integer", "description": "Lines to read (default 2000, max 10000)"},
                },
                "required": ["file_path"],
            },
        ),
        types.Tool(
            name="write_file",
            description=(
                "Create or overwrite a file. Max 10MB. "
                "For larger files use the /files/upload HTTP endpoint."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Absolute path"},
                    "content": {"type": "string", "description": "File content (max 10MB)"},
                },
                "required": ["file_path", "content"],
            },
        ),
        types.Tool(
            name="edit_file",
            description=(
                "Replace a string in a file. old_string must be unique unless replace_all=true."
            ),
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
        ),
        types.Tool(
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
        ),
        types.Tool(
            name="grep",
            description=(
                "Search file contents with regex. Uses ripgrep if installed, else Python fallback."
            ),
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
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict | None) -> list[types.TextContent]:
    result_json = await dispatch_tool(name, arguments or {})
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

- [ ] **Step 4: Run MCP dispatch tests**

```bash
pytest tests/test_mcp.py -v
```

Expected: All 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add mcp_server.py tests/test_mcp.py
git commit -m "feat: implement MCP server with tool schemas and dispatch"
```

---

## Task 10: main.py — wire everything together

**Files:**
- Create: `main.py`

- [ ] **Step 1: Implement `main.py`**

```python
import uvicorn
from fastapi import Depends, FastAPI, Request

import config
from auth import admin_router, get_store, require_auth
from mcp_server import server, sse_transport
from tools.transfer import transfer_router

app = FastAPI(title="Linux MCP Server", version="1.0.0")

app.include_router(admin_router)
app.include_router(transfer_router)


@app.get("/sse")
async def handle_sse(request: Request, _: dict = Depends(require_auth)):
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await server.run(
            streams[0],
            streams[1],
            server.create_initialization_options(),
        )


@app.post("/messages")
async def handle_messages(request: Request, _: dict = Depends(require_auth)):
    await sse_transport.handle_post_message(
        request.scope, request.receive, request._send
    )


@app.on_event("startup")
async def startup():
    # Fail fast if ADMIN_TOKEN not configured
    get_store()


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run("main:app", host=config.HOST, port=config.PORT, reload=False)
```

- [ ] **Step 2: Run the full test suite**

```bash
pytest tests/ -v
```

Expected: All tests PASS. If any fail, fix before proceeding.

- [ ] **Step 3: Smoke test the server**

```bash
export MCP_ADMIN_TOKEN=adm_smoketest
python main.py &
SERVER_PID=$!
sleep 2

# Health check
curl -s http://localhost:8765/health

# Create a token
curl -s -X POST http://localhost:8765/admin/tokens \
  -H "Authorization: Bearer adm_smoketest" \
  -H "Content-Type: application/json" \
  -d '{"name": "smoke-client"}'

# Attempt SSE without token — expect 401
curl -s -o /dev/null -w "%{http_code}" http://localhost:8765/sse

kill $SERVER_PID
```

Expected output:
```
{"status":"ok"}
{"token":"tok_...","name":"smoke-client"}
401
```

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat: wire FastAPI app with SSE routes, auth, file transfer, admin"
```

---

## Task 11: Final cleanup — .gitignore, verify README accuracy

**Files:**
- Verify: `.gitignore` covers `tokens.json` and `.env`
- Verify: `README.md` startup instructions match actual code

- [ ] **Step 1: Confirm tokens.json is gitignored**

```bash
touch tokens.json
git status
```

Expected: `tokens.json` does NOT appear in untracked files.

- [ ] **Step 2: Run full test suite one final time**

```bash
pytest tests/ -v --tb=short
```

Expected: All tests PASS, no warnings about deprecated APIs.

- [ ] **Step 3: Final commit**

```bash
git add -A
git status
# Confirm only expected files are staged (no tokens.json, no .env)
git commit -m "chore: final cleanup and verification"
```

---

## Self-Review Against Spec

| Spec Requirement | Covered In |
|-----------------|-----------|
| HTTP/SSE transport | Task 10 (`/sse`, `/messages`) |
| Multi-user Bearer token auth | Task 2 (TokenStore), Task 3 (middleware) |
| Token CRUD admin API | Task 3 |
| `bash_execute` with timeout, truncation | Task 4 |
| `read_file` with pagination, line truncation | Task 5 |
| `write_file` with 10MB limit | Task 6 |
| `edit_file` with ambiguity check | Task 6 |
| `glob` with 1000 file cap | Task 7 |
| `grep` with rg+fallback, modes, max_results | Task 7 |
| `/files/upload` streaming multipart | Task 8 |
| `/files/download` streaming | Task 8 |
| Structured error responses | All tool tasks |
| Configurable limits via `config.py` | Task 1 |
| `.gitignore`, `.env.example` | Task 1 |
| README with startup/connect instructions | Already committed (brainstorm phase) |
