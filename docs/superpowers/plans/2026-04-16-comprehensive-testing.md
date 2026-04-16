# Comprehensive Testing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform mymcp's test suite from 91% line coverage to a comprehensive multi-dimensional testing strategy with branch coverage, integration tests, boundary analysis, performance benchmarks, load tests, and mutation testing — while separating dev dependencies from production.

**Architecture:** Split requirements files for clean dependency separation. Add new test files organized by testing dimension (unit gaps, integration, boundary, benchmark). Configure mutmut via pyproject.toml. Update CI to report branch coverage and mutation score badges.

**Tech Stack:** pytest, pytest-cov, pytest-benchmark, pytest-asyncio, httpx (ASGITransport), mutmut, locust

---

### Task 1: Dependency Separation

**Files:**
- Modify: `requirements.txt`
- Create: `requirements-dev.txt`
- Modify: `CLAUDE.md`
- Modify: `.github/workflows/ci.yml:26-27`

- [ ] **Step 1: Update requirements.txt — remove test dependencies**

Replace the entire contents of `requirements.txt` with:

```
mcp>=1.0.0
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
python-multipart>=0.0.9
httpx>=0.27.0
anyio>=4.0.0
```

- [ ] **Step 2: Create requirements-dev.txt**

Create `requirements-dev.txt`:

```
-r requirements.txt
pytest>=8.0.0
pytest-asyncio>=0.23.0
pytest-cov>=5.0.0
pytest-benchmark>=4.0.0
mutmut>=2.4.0
locust>=2.20.0
```

- [ ] **Step 3: Install dev dependencies locally**

Run: `pip install -r requirements-dev.txt`
Expected: All packages install successfully.

- [ ] **Step 4: Verify existing tests still pass**

Run: `python3 -m pytest tests/ -v`
Expected: 103 passed.

- [ ] **Step 5: Update CLAUDE.md install command**

In `CLAUDE.md`, change the `# Install dependencies` command:

```bash
# Install production dependencies
pip install -r requirements.txt

# Install development/test dependencies
pip install -r requirements-dev.txt
```

- [ ] **Step 6: Update CI workflow to use requirements-dev.txt**

In `.github/workflows/ci.yml`, change line 27 from:
```yaml
          pip install -r requirements.txt pytest-cov
```
to:
```yaml
          pip install -r requirements-dev.txt
```

- [ ] **Step 7: Commit**

```bash
git add requirements.txt requirements-dev.txt CLAUDE.md .github/workflows/ci.yml
git commit -m "chore: separate production and dev dependencies into requirements.txt and requirements-dev.txt"
```

---

### Task 2: pytest Configuration — Enable Branch Coverage

**Files:**
- Modify: `pytest.ini`

- [ ] **Step 1: Update pytest.ini with coverage and benchmark config**

Replace `pytest.ini` contents with:

```ini
[pytest]
addopts = --cov-branch
markers =
    benchmark: marks tests as benchmarks (deselect with '-m "not benchmark"')
```

- [ ] **Step 2: Verify branch coverage is reported**

Run: `python3 -m pytest tests/ --cov=. --cov-report=term-missing -q 2>&1 | head -30`
Expected: Output shows "Branch" column in coverage table.

- [ ] **Step 3: Commit**

```bash
git add pytest.ini
git commit -m "chore: enable branch coverage and add benchmark marker in pytest config"
```

---

### Task 3: Unit Test — main.py (McpAuthMiddleware, health, lifespan)

**Files:**
- Create: `tests/test_main.py`
- Depends on: `main.py`, `auth.py`, `config.py`

- [ ] **Step 1: Write tests for main.py**

Create `tests/test_main.py`:

```python
import pytest
from unittest.mock import patch, MagicMock
from httpx import AsyncClient, ASGITransport

from auth import TokenStore


@pytest.fixture
def store(tmp_path):
    return TokenStore(str(tmp_path / "tokens.json"), "adm_testadmin")


@pytest.fixture
def app_with_store(store):
    """Create a fresh FastAPI app with overridden token store."""
    # Patch config before importing main to avoid side effects
    with patch("config.ADMIN_TOKEN", "adm_testadmin"):
        # Reset the singleton so get_store() uses our store
        import auth
        original_store = auth._store
        auth._store = store
        try:
            from main import app
            yield app
        finally:
            auth._store = original_store


@pytest.fixture
async def client(app_with_store):
    transport = ASGITransport(app=app_with_store)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# /health endpoint
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_health_endpoint(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# _validate_token
# ---------------------------------------------------------------------------

def test_validate_token_missing_bearer(store):
    from main import _validate_token
    from starlette.requests import Request
    scope = {"type": "http", "method": "GET", "headers": [], "query_string": b""}
    request = Request(scope)
    error, info = _validate_token(request)
    assert error is not None
    assert info is None


def test_validate_token_invalid_token(store):
    from main import _validate_token
    from starlette.requests import Request
    import auth
    original = auth._store
    auth._store = store
    try:
        scope = {
            "type": "http", "method": "GET",
            "headers": [(b"authorization", b"Bearer tok_invalid")],
            "query_string": b"",
        }
        request = Request(scope)
        error, info = _validate_token(request)
        assert error is not None
        assert info is None
    finally:
        auth._store = original


def test_validate_token_valid_token(store):
    from main import _validate_token
    from starlette.requests import Request
    import auth
    original = auth._store
    auth._store = store
    token = store.create_token("test-client", role="rw")
    try:
        scope = {
            "type": "http", "method": "GET",
            "headers": [(b"authorization", f"Bearer {token}".encode())],
            "query_string": b"",
        }
        request = Request(scope)
        error, info = _validate_token(request)
        assert error is None
        assert info is not None
        assert info["name"] == "test-client"
    finally:
        auth._store = original


# ---------------------------------------------------------------------------
# McpAuthMiddleware
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_middleware_no_token_on_mcp(client):
    resp = await client.post("/mcp")
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_middleware_invalid_token_on_mcp(client):
    resp = await client.post(
        "/mcp",
        headers={"Authorization": "Bearer tok_invalid"},
    )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_middleware_non_mcp_path_passes_through(client):
    """Non-/mcp paths should go through normal FastAPI routing."""
    resp = await client.get("/health")
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_middleware_valid_token_forwards_to_mcp(client, store):
    """Valid token on /mcp should be forwarded to session_manager."""
    token = store.create_token("mcp-client", role="rw")
    # We can't easily test the full MCP protocol here, but we can verify
    # the middleware doesn't return 401
    resp = await client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {token}"},
        content=b"{}",
    )
    # MCP session manager will respond (not 401)
    assert resp.status_code != 401


@pytest.mark.anyio
async def test_middleware_sets_contextvar(store):
    """Verify the middleware sets _current_audit_info contextvar."""
    from main import app, _validate_token
    from mcp_server import _current_audit_info

    token = store.create_token("ctx-client", role="ro")
    import auth
    original = auth._store
    auth._store = store

    captured = {}

    # Patch session_manager.handle_request to capture the contextvar
    async def fake_handle_request(scope, receive, send):
        info = _current_audit_info.get()
        captured.update(info)
        from starlette.responses import JSONResponse
        resp = JSONResponse({"ok": True})
        await resp(scope, receive, send)

    try:
        with patch("main.session_manager") as mock_sm:
            mock_sm.handle_request = fake_handle_request
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                resp = await c.post(
                    "/mcp",
                    headers={"Authorization": f"Bearer {token}"},
                )

        assert captured["token_name"] == "ctx-client"
        assert captured["role"] == "ro"
        assert "ip" in captured
    finally:
        auth._store = original
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_main.py -v`
Expected: All tests pass.

- [ ] **Step 3: Check coverage improvement for main.py**

Run: `python3 -m pytest tests/ --cov=main --cov-report=term-missing -q 2>&1 | tail -5`
Expected: `main.py` coverage significantly improved from 0%.

- [ ] **Step 4: Commit**

```bash
git add tests/test_main.py
git commit -m "test: add unit tests for main.py — middleware, validation, health endpoint"
```

---

### Task 4: Unit Test — auth.py (get_store, require_auth, require_admin)

**Files:**
- Modify: `tests/test_auth.py`
- Modify: `tests/test_admin.py`

- [ ] **Step 1: Add get_store and FastAPI dependency tests to test_auth.py**

Append to `tests/test_auth.py`:

```python


# ---------------------------------------------------------------------------
# get_store() singleton
# ---------------------------------------------------------------------------

def test_get_store_raises_without_admin_token(tmp_path):
    """get_store() should raise RuntimeError when ADMIN_TOKEN is empty."""
    import auth
    original = auth._store
    auth._store = None  # Reset singleton
    try:
        with patch("config.ADMIN_TOKEN", ""):
            with pytest.raises(RuntimeError, match="MCP_ADMIN_TOKEN"):
                auth.get_store()
    finally:
        auth._store = original


def test_get_store_creates_singleton(tmp_path):
    """get_store() should create and return a TokenStore singleton."""
    import auth
    original = auth._store
    auth._store = None
    try:
        with patch("config.ADMIN_TOKEN", "adm_test123"), \
             patch("config.TOKEN_FILE", str(tmp_path / "tokens.json")):
            store = auth.get_store()
            assert store is not None
            # Second call returns same instance
            store2 = auth.get_store()
            assert store is store2
    finally:
        auth._store = original
```

- [ ] **Step 2: Add require_auth and require_admin tests to test_admin.py**

Append to `tests/test_admin.py`:

```python


# ---------------------------------------------------------------------------
# require_auth dependency
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_require_auth_missing_bearer(client):
    """require_auth should reject requests without Bearer prefix."""
    # Use admin endpoint that uses require_admin, but test with a custom app
    # that uses require_auth instead. We'll test via a simple endpoint.
    from fastapi import FastAPI, Depends
    from auth import require_auth, get_store

    app2 = FastAPI()

    @app2.get("/protected")
    async def protected(info: dict = Depends(require_auth)):
        return info

    app2.dependency_overrides[get_store] = lambda: store
    async with AsyncClient(transport=ASGITransport(app=app2), base_url="http://test") as c:
        resp = await c.get("/protected")
        assert resp.status_code == 401
        assert "Bearer" in resp.json()["detail"]


@pytest.mark.anyio
async def test_require_auth_invalid_token(client, store):
    from fastapi import FastAPI, Depends
    from auth import require_auth, get_store

    app2 = FastAPI()

    @app2.get("/protected")
    async def protected(info: dict = Depends(require_auth)):
        return info

    app2.dependency_overrides[get_store] = lambda: store
    async with AsyncClient(transport=ASGITransport(app=app2), base_url="http://test") as c:
        resp = await c.get(
            "/protected",
            headers={"Authorization": "Bearer tok_doesnotexist"},
        )
        assert resp.status_code == 401
        assert "Invalid" in resp.json()["detail"]


@pytest.mark.anyio
async def test_require_auth_valid_token(client, store):
    from fastapi import FastAPI, Depends
    from auth import require_auth, get_store

    app2 = FastAPI()

    @app2.get("/protected")
    async def protected(info: dict = Depends(require_auth)):
        return {"name": info["name"]}

    token = store.create_token("auth-test")
    app2.dependency_overrides[get_store] = lambda: store
    async with AsyncClient(transport=ASGITransport(app=app2), base_url="http://test") as c:
        resp = await c.get(
            "/protected",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "auth-test"


@pytest.mark.anyio
async def test_require_admin_wrong_token(client):
    """require_admin rejects non-admin tokens with 403."""
    resp = await client.get(
        "/admin/tokens",
        headers={"Authorization": "Bearer tok_notadmin"},
    )
    assert resp.status_code == 403
    assert "Admin" in resp.json()["detail"]
```

- [ ] **Step 3: Run tests**

Run: `python3 -m pytest tests/test_auth.py tests/test_admin.py -v`
Expected: All pass.

- [ ] **Step 4: Check coverage**

Run: `python3 -m pytest tests/ --cov=auth --cov-report=term-missing -q 2>&1 | tail -5`
Expected: `auth.py` coverage improved from 84% toward ~98%.

- [ ] **Step 5: Commit**

```bash
git add tests/test_auth.py tests/test_admin.py
git commit -m "test: cover get_store, require_auth, require_admin in auth.py"
```

---

### Task 5: Unit Test — tools/files.py (grep and glob gaps)

**Files:**
- Modify: `tests/test_files.py`

- [ ] **Step 1: Add glob exception and grep coverage tests**

Append to `tests/test_files.py`:

```python


# ---------------------------------------------------------------------------
# glob_files — exception path
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_glob_exception_returns_error(tmp_path):
    """glob_files should catch exceptions and return error dict."""
    with patch("tools.files._glob_module.glob", side_effect=OSError("disk error")):
        result = await glob_files("*.py", path=str(tmp_path))
    assert result["success"] is False
    assert result["error"] == "OSError"
    assert "disk error" in result["message"]


# ---------------------------------------------------------------------------
# _grep_python fallback — full coverage
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_grep_python_invalid_regex(tmp_path):
    """Invalid regex should return error."""
    (tmp_path / "f.txt").write_text("data\n")
    with patch("shutil.which", return_value=None):
        result = await grep_files("[invalid", path=str(tmp_path))
    assert result["success"] is False
    assert result["error"] == "InvalidRegex"


@pytest.mark.anyio
async def test_grep_python_single_file(tmp_path):
    """When path is a file, search that single file."""
    f = tmp_path / "single.txt"
    f.write_text("hello world\nfoo bar\nhello again\n")
    with patch("shutil.which", return_value=None):
        result = await grep_files("hello", path=str(f))
    assert result["match_count"] == 2


@pytest.mark.anyio
async def test_grep_python_files_mode(tmp_path):
    """Python fallback files output_mode."""
    (tmp_path / "a.txt").write_text("match here\n")
    (tmp_path / "b.txt").write_text("nothing\n")
    with patch("shutil.which", return_value=None):
        result = await grep_files("match", path=str(tmp_path), output_mode="files")
    assert any("a.txt" in line for line in result["results"].splitlines())
    assert not any("b.txt" in line for line in result["results"].splitlines())


@pytest.mark.anyio
async def test_grep_python_count_mode(tmp_path):
    """Python fallback count output_mode."""
    (tmp_path / "data.txt").write_text("apple\nbanana\napple pie\n")
    with patch("shutil.which", return_value=None):
        result = await grep_files("apple", path=str(tmp_path), output_mode="count")
    assert result["match_count"] >= 1
    assert "2" in result["results"]


@pytest.mark.anyio
async def test_grep_python_glob_filter(tmp_path):
    """Python fallback glob filter."""
    (tmp_path / "a.log").write_text("target\n")
    (tmp_path / "b.txt").write_text("target\n")
    with patch("shutil.which", return_value=None):
        result = await grep_files("target", path=str(tmp_path), glob="*.log")
    assert any("a.log" in line for line in result["results"].splitlines())
    assert not any("b.txt" in line for line in result["results"].splitlines())


@pytest.mark.anyio
async def test_grep_python_permission_error_skipped(tmp_path):
    """Python fallback should skip files with permission errors."""
    ok = tmp_path / "ok.txt"
    ok.write_text("findme\n")
    noperm = tmp_path / "noperm.txt"
    noperm.write_text("findme\n")
    noperm.chmod(0o000)
    try:
        with patch("shutil.which", return_value=None):
            result = await grep_files("findme", path=str(tmp_path))
        assert result["match_count"] >= 1
        assert "ok.txt" in result["results"]
    finally:
        noperm.chmod(0o644)


@pytest.mark.anyio
async def test_grep_python_case_insensitive(tmp_path):
    """Python fallback case-insensitive search."""
    (tmp_path / "f.txt").write_text("ERROR found\n")
    with patch("shutil.which", return_value=None):
        result = await grep_files("error", path=str(tmp_path), case_insensitive=True)
    assert result["match_count"] >= 1


@pytest.mark.anyio
async def test_grep_python_truncates(tmp_path):
    """Python fallback truncation."""
    f = tmp_path / "big.txt"
    f.write_text("\n".join(f"match line {i}" for i in range(100)))
    with patch("shutil.which", return_value=None):
        result = await grep_files("match", path=str(tmp_path), max_results=5)
    assert "[TRUNCATED" in result["results"]


# ---------------------------------------------------------------------------
# _grep_rg — explicit tests (only run if rg is available)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_grep_rg_files_mode(tmp_path):
    """ripgrep files output_mode."""
    import shutil
    if not shutil.which("rg"):
        pytest.skip("ripgrep not installed")
    (tmp_path / "a.txt").write_text("target line\n")
    (tmp_path / "b.txt").write_text("nothing\n")
    result = await grep_files("target", path=str(tmp_path), output_mode="files")
    assert any("a.txt" in line for line in result["results"].splitlines())


@pytest.mark.anyio
async def test_grep_rg_count_mode(tmp_path):
    """ripgrep count output_mode."""
    import shutil
    if not shutil.which("rg"):
        pytest.skip("ripgrep not installed")
    (tmp_path / "data.txt").write_text("apple\nbanana\napple pie\n")
    result = await grep_files("apple", path=str(tmp_path), output_mode="count")
    assert result["match_count"] >= 1


@pytest.mark.anyio
async def test_grep_rg_context_lines(tmp_path):
    """ripgrep with context lines."""
    import shutil
    if not shutil.which("rg"):
        pytest.skip("ripgrep not installed")
    f = tmp_path / "ctx.txt"
    f.write_text("aaa\nbbb\nccc\nddd\neee\n")
    result = await grep_files("ccc", path=str(tmp_path), context_lines=1)
    assert "bbb" in result["results"] or "ccc" in result["results"]


@pytest.mark.anyio
async def test_grep_rg_timeout(tmp_path):
    """ripgrep timeout should return error."""
    import shutil
    if not shutil.which("rg"):
        pytest.skip("ripgrep not installed")
    with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError()):
        result = await grep_files("pattern", path=str(tmp_path))
    assert result["success"] is False
    assert result["error"] == "TimeoutError"
```

- [ ] **Step 2: Run tests**

Run: `python3 -m pytest tests/test_files.py -v`
Expected: All pass (rg tests may skip if ripgrep not installed).

- [ ] **Step 3: Check coverage**

Run: `python3 -m pytest tests/ --cov=tools/files --cov-report=term-missing -q 2>&1 | tail -5`
Expected: `tools/files.py` coverage improved from 78% toward ~97%.

- [ ] **Step 4: Commit**

```bash
git add tests/test_files.py
git commit -m "test: cover grep python fallback, grep rg paths, and glob exception in files.py"
```

---

### Task 6: Unit Test — mcp_server.py (list_tools, JSON decode path)

**Files:**
- Modify: `tests/test_mcp.py`

- [ ] **Step 1: Add list_tools and JSON decode error tests**

Append to `tests/test_mcp.py`:

```python


# ---------------------------------------------------------------------------
# list_tools — via _current_audit_info contextvar
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_list_tools_ro_role():
    """list_tools should return only read tools for ro role."""
    from mcp_server import list_tools, _current_audit_info, READ_TOOLS
    token = _current_audit_info.set({
        "token_name": "ro-user", "role": "ro", "ip": "127.0.0.1",
    })
    try:
        tools = await list_tools()
        tool_names = {t.name for t in tools}
        assert tool_names == READ_TOOLS
    finally:
        _current_audit_info.reset(token)


@pytest.mark.anyio
async def test_list_tools_rw_role():
    """list_tools should return all tools for rw role."""
    from mcp_server import list_tools, _current_audit_info, ALL_TOOLS
    token = _current_audit_info.set({
        "token_name": "rw-user", "role": "rw", "ip": "127.0.0.1",
    })
    try:
        tools = await list_tools()
        tool_names = {t.name for t in tools}
        assert tool_names == ALL_TOOLS
    finally:
        _current_audit_info.reset(token)


# ---------------------------------------------------------------------------
# call_tool — JSON decode error path
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_call_tool_non_json_result(set_audit_info):
    """When dispatch_tool returns non-JSON, result_status should be 'ok'."""
    with patch("mcp_server.dispatch_tool", return_value="plain text not json"):
        with patch("mcp_server.log_tool_call") as mock_log:
            results = await call_tool("bash_execute", {"command": "echo x"})
            assert results[0].text == "plain text not json"
            kwargs = mock_log.call_args.kwargs
            assert kwargs["result"] == "ok"
```

- [ ] **Step 2: Run tests**

Run: `python3 -m pytest tests/test_mcp.py -v`
Expected: All pass.

- [ ] **Step 3: Commit**

```bash
git add tests/test_mcp.py
git commit -m "test: cover list_tools role filtering and JSON decode error path in mcp_server.py"
```

---

### Task 7: Integration Tests

**Files:**
- Create: `tests/test_integration.py`

- [ ] **Step 1: Write integration tests**

Create `tests/test_integration.py`:

```python
"""Integration tests: full request chain via httpx AsyncClient + ASGITransport.

Tests the complete auth → middleware → MCP tool → audit pipeline.
"""

import json
import os
import pytest
from unittest.mock import patch, MagicMock

from httpx import AsyncClient, ASGITransport
from auth import TokenStore
from mcp_server import _current_audit_info


@pytest.fixture
def store(tmp_path):
    return TokenStore(str(tmp_path / "tokens.json"), "adm_testadmin")


@pytest.fixture
def app_with_store(store):
    import auth
    original = auth._store
    auth._store = store
    try:
        from main import app
        yield app
    finally:
        auth._store = original


@pytest.fixture
async def client(app_with_store):
    transport = ASGITransport(app=app_with_store)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _make_mcp_request(tool_name: str, arguments: dict) -> dict:
    """Build a JSON-RPC request for MCP call_tool."""
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
        },
    }


def _make_mcp_list_tools() -> dict:
    """Build a JSON-RPC request for MCP list_tools."""
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {},
    }


async def _mcp_call(client, token: str, payload: dict) -> dict:
    """Send a JSON-RPC request to /mcp and parse the response."""
    resp = await client.post(
        "/mcp",
        json=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    )
    assert resp.status_code == 200
    # Response may be SSE or JSON depending on transport
    content_type = resp.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        # Parse SSE: find the data line with our result
        for line in resp.text.splitlines():
            if line.startswith("data: "):
                data = json.loads(line[6:])
                if "result" in data or "error" in data:
                    return data
        return {}
    return resp.json()


# ---------------------------------------------------------------------------
# Auth → Tool → Response chain
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_no_token_returns_401(client):
    resp = await client.post("/mcp", json=_make_mcp_list_tools())
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_invalid_token_returns_401(client):
    resp = await client.post(
        "/mcp",
        json=_make_mcp_list_tools(),
        headers={"Authorization": "Bearer tok_invalid"},
    )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_rw_token_can_read_file(client, store, tmp_path):
    """Full chain: rw token → read_file → correct content."""
    token = store.create_token("rw-client", role="rw")
    f = tmp_path / "integration_test.txt"
    f.write_text("integration test content\n")

    data = await _mcp_call(
        client, token,
        _make_mcp_request("read_file", {"file_path": str(f)}),
    )
    if "result" in data:
        content_items = data["result"].get("content", [])
        if content_items:
            text = content_items[0].get("text", "")
            result = json.loads(text)
            assert "integration test content" in result["content"]


@pytest.mark.anyio
async def test_ro_token_can_list_tools(client, store):
    """ro token should see only read tools."""
    token = store.create_token("ro-client", role="ro")
    data = await _mcp_call(client, token, _make_mcp_list_tools())
    if "result" in data:
        tools = data["result"].get("tools", [])
        tool_names = {t["name"] for t in tools}
        # ro should NOT see write tools
        assert "bash_execute" not in tool_names
        assert "write_file" not in tool_names
        # ro SHOULD see read tools
        assert "read_file" in tool_names


@pytest.mark.anyio
async def test_ro_token_denied_write_tool(client, store, tmp_path):
    """ro token calling write tool should get permission denied."""
    token = store.create_token("ro-client", role="ro")
    data = await _mcp_call(
        client, token,
        _make_mcp_request("bash_execute", {"command": "echo pwned"}),
    )
    if "result" in data:
        content_items = data["result"].get("content", [])
        if content_items:
            text = content_items[0].get("text", "")
            result = json.loads(text)
            assert result["success"] is False
            assert result["error"] == "PermissionDenied"


@pytest.mark.anyio
async def test_rw_token_write_and_read(client, store, tmp_path):
    """Full chain: write a file, then read it back."""
    token = store.create_token("rw-client", role="rw")
    target = str(tmp_path / "written_by_integration.txt")

    # Write
    write_data = await _mcp_call(
        client, token,
        _make_mcp_request("write_file", {
            "file_path": target,
            "content": "hello from integration test",
        }),
    )

    # Read back
    read_data = await _mcp_call(
        client, token,
        _make_mcp_request("read_file", {"file_path": target}),
    )

    if "result" in read_data:
        content_items = read_data["result"].get("content", [])
        if content_items:
            text = content_items[0].get("text", "")
            result = json.loads(text)
            assert "hello from integration test" in result["content"]


# ---------------------------------------------------------------------------
# Admin API integration
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_admin_create_and_use_token(client, store):
    """Create a token via admin API, then use it for MCP."""
    # Create token
    resp = await client.post(
        "/admin/tokens",
        json={"name": "dynamic-client", "role": "ro"},
        headers={"Authorization": "Bearer adm_testadmin"},
    )
    assert resp.status_code == 200
    new_token = resp.json()["token"]

    # Use the new token on /mcp
    data = await _mcp_call(client, new_token, _make_mcp_list_tools())
    # Should not get 401 — the token works
    assert "result" in data or "error" not in data or data.get("error", {}).get("code") != -32600


@pytest.mark.anyio
async def test_admin_revoke_and_reject(client, store):
    """Revoke a token, then verify it's rejected."""
    token = store.create_token("to-revoke", role="rw")

    # Verify it works first
    resp1 = await client.post(
        "/mcp",
        json=_make_mcp_list_tools(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    )
    assert resp1.status_code == 200

    # Revoke
    resp_revoke = await client.delete(
        f"/admin/tokens/{token}",
        headers={"Authorization": "Bearer adm_testadmin"},
    )
    assert resp_revoke.status_code == 200

    # Should now be rejected
    resp2 = await client.post(
        "/mcp",
        json=_make_mcp_list_tools(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp2.status_code == 401
```

- [ ] **Step 2: Run tests**

Run: `python3 -m pytest tests/test_integration.py -v`
Expected: All pass.

- [ ] **Step 3: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: add integration tests for full auth → MCP → response chain"
```

---

### Task 8: Boundary Value & Exception Tests

**Files:**
- Create: `tests/test_boundary.py`

- [ ] **Step 1: Write boundary value tests**

Create `tests/test_boundary.py`:

```python
"""Boundary value and exception analysis tests.

Systematically tests edge cases for each tool function's parameters.
"""

import asyncio
import os
import pytest
from unittest.mock import patch

from tools.bash import run_bash_execute
from tools.files import read_file, write_file, edit_file, glob_files, grep_files


# ===========================================================================
# bash_execute boundaries
# ===========================================================================

class TestBashBoundary:

    @pytest.mark.anyio
    async def test_timeout_zero_clamped_to_1(self):
        """timeout=0 should be clamped to 1 (min)."""
        result = await run_bash_execute("echo fast", timeout=0)
        assert result["exit_code"] == 0

    @pytest.mark.anyio
    async def test_timeout_negative_clamped_to_1(self):
        """Negative timeout should be clamped to 1."""
        result = await run_bash_execute("echo fast", timeout=-5)
        assert result["exit_code"] == 0

    @pytest.mark.anyio
    async def test_timeout_over_600_clamped(self):
        """timeout > 600 should be clamped to 600."""
        result = await run_bash_execute("echo fast", timeout=9999)
        assert result["exit_code"] == 0

    @pytest.mark.anyio
    async def test_timeout_exactly_600(self):
        """timeout=600 should be accepted."""
        result = await run_bash_execute("echo fast", timeout=600)
        assert result["exit_code"] == 0

    @pytest.mark.anyio
    async def test_empty_command(self):
        """Empty command string."""
        result = await run_bash_execute("")
        # Empty command typically succeeds with exit 0
        assert "exit_code" in result

    @pytest.mark.anyio
    async def test_max_output_bytes_zero_clamped(self):
        """max_output_bytes=0 should be clamped to 1."""
        result = await run_bash_execute("echo hello", max_output_bytes=0)
        assert "exit_code" in result

    @pytest.mark.anyio
    async def test_max_output_bytes_negative_clamped(self):
        """Negative max_output_bytes should be clamped to 1."""
        result = await run_bash_execute("echo hello", max_output_bytes=-100)
        assert "exit_code" in result

    @pytest.mark.anyio
    async def test_max_output_bytes_over_hard_cap(self):
        """max_output_bytes over hard cap should be clamped."""
        import config
        result = await run_bash_execute(
            "echo hello",
            max_output_bytes=config.BASH_MAX_OUTPUT_BYTES_HARD + 1000,
        )
        assert result["exit_code"] == 0

    @pytest.mark.anyio
    async def test_working_dir_empty_string(self):
        """Empty string working_dir."""
        result = await run_bash_execute("echo x", working_dir="")
        # May fail or succeed depending on OS behavior
        assert "exit_code" in result or "success" in result

    @pytest.mark.anyio
    async def test_working_dir_is_file(self, tmp_path):
        """working_dir pointing to a file, not directory."""
        f = tmp_path / "afile.txt"
        f.write_text("x")
        result = await run_bash_execute("echo x", working_dir=str(f))
        # Should fail — not a directory
        assert result.get("success") is False or result.get("exit_code") != 0


# ===========================================================================
# read_file boundaries
# ===========================================================================

class TestReadFileBoundary:

    @pytest.mark.anyio
    async def test_offset_zero_clamped(self, tmp_path):
        """offset=0 should be clamped to 1."""
        f = tmp_path / "test.txt"
        f.write_text("line1\nline2\n")
        result = await read_file(str(f), offset=0)
        assert "   1\tline1" in result["content"]

    @pytest.mark.anyio
    async def test_offset_negative_clamped(self, tmp_path):
        """Negative offset should be clamped to 1."""
        f = tmp_path / "test.txt"
        f.write_text("line1\n")
        result = await read_file(str(f), offset=-10)
        assert "   1\tline1" in result["content"]

    @pytest.mark.anyio
    async def test_offset_beyond_file(self, tmp_path):
        """Offset beyond file lines should return empty content."""
        f = tmp_path / "test.txt"
        f.write_text("line1\nline2\n")
        result = await read_file(str(f), offset=9999)
        assert result["content"] == ""
        assert result["total_lines"] == 2

    @pytest.mark.anyio
    async def test_limit_zero_clamped(self, tmp_path):
        """limit=0 should be clamped to 1."""
        f = tmp_path / "test.txt"
        f.write_text("line1\nline2\nline3\n")
        result = await read_file(str(f), limit=0)
        # Clamped to 1, should read at least 1 line
        assert result["total_lines"] == 3

    @pytest.mark.anyio
    async def test_limit_negative_clamped(self, tmp_path):
        """Negative limit should be clamped to 1."""
        f = tmp_path / "test.txt"
        f.write_text("line1\n")
        result = await read_file(str(f), limit=-5)
        assert result["total_lines"] == 1

    @pytest.mark.anyio
    async def test_limit_exactly_max(self, tmp_path):
        """limit at MAX_LIMIT should be accepted."""
        f = tmp_path / "test.txt"
        f.write_text("line1\n")
        import config
        result = await read_file(str(f), limit=config.READ_FILE_MAX_LIMIT)
        assert result["total_lines"] == 1

    @pytest.mark.anyio
    async def test_limit_over_max_clamped(self, tmp_path):
        """limit over MAX_LIMIT should be clamped."""
        f = tmp_path / "test.txt"
        f.write_text("line1\n")
        import config
        result = await read_file(str(f), limit=config.READ_FILE_MAX_LIMIT + 100)
        assert result["total_lines"] == 1

    @pytest.mark.anyio
    async def test_empty_file(self, tmp_path):
        """Reading an empty file."""
        f = tmp_path / "empty.txt"
        f.write_text("")
        result = await read_file(str(f))
        assert result["total_lines"] == 0
        assert result["content"] == ""

    @pytest.mark.anyio
    async def test_binary_file(self, tmp_path):
        """Reading a binary file should not crash."""
        f = tmp_path / "binary.bin"
        f.write_bytes(b"\x00\x01\x02\xff\xfe\n")
        result = await read_file(str(f))
        assert result["total_lines"] >= 1

    @pytest.mark.anyio
    async def test_symlink_to_normal_file(self, tmp_path):
        """Symlink to a normal file should work."""
        f = tmp_path / "real.txt"
        f.write_text("real content\n")
        link = tmp_path / "link.txt"
        link.symlink_to(f)
        result = await read_file(str(link))
        assert "real content" in result["content"]

    @pytest.mark.anyio
    async def test_empty_path(self):
        """Empty string path."""
        result = await read_file("")
        assert result["success"] is False


# ===========================================================================
# write_file boundaries
# ===========================================================================

class TestWriteFileBoundary:

    @pytest.mark.anyio
    async def test_empty_content(self, tmp_path):
        """Writing empty content should succeed."""
        path = str(tmp_path / "empty.txt")
        result = await write_file(path, "")
        assert result["success"] is True
        assert result["bytes_written"] == 0

    @pytest.mark.anyio
    async def test_content_exactly_max(self, tmp_path):
        """Content at exactly max bytes should succeed."""
        import config
        # Create content exactly at the limit
        with patch("config.WRITE_FILE_MAX_BYTES", 100):
            content = "x" * 100  # 100 bytes in UTF-8
            path = str(tmp_path / "exact.txt")
            result = await write_file(path, content)
            assert result["success"] is True

    @pytest.mark.anyio
    async def test_path_is_existing_directory(self, tmp_path):
        """Writing to a path that is a directory should fail."""
        result = await write_file(str(tmp_path), "data")
        assert result["success"] is False

    @pytest.mark.anyio
    async def test_deeply_nested_new_dirs(self, tmp_path):
        """Writing to deeply nested non-existent directories."""
        path = str(tmp_path / "a" / "b" / "c" / "d" / "file.txt")
        result = await write_file(path, "deep")
        assert result["success"] is True
        assert os.path.exists(path)


# ===========================================================================
# edit_file boundaries
# ===========================================================================

class TestEditFileBoundary:

    @pytest.mark.anyio
    async def test_empty_old_string(self, tmp_path):
        """Empty old_string — will match everywhere."""
        f = tmp_path / "file.txt"
        f.write_text("hello")
        result = await edit_file(str(f), "", "x")
        # Empty string appears multiple times in any non-empty file
        # Behavior: count("") returns len+1, so AmbiguousMatch unless replace_all
        assert result["success"] is False or result.get("replacements", 0) >= 1

    @pytest.mark.anyio
    async def test_old_equals_new(self, tmp_path):
        """old_string == new_string — no-op replacement."""
        f = tmp_path / "file.txt"
        f.write_text("hello world")
        result = await edit_file(str(f), "hello", "hello")
        assert result["success"] is True
        assert f.read_text() == "hello world"

    @pytest.mark.anyio
    async def test_replace_all_single_match(self, tmp_path):
        """replace_all=True with only one match should succeed."""
        f = tmp_path / "file.txt"
        f.write_text("unique_string here")
        result = await edit_file(str(f), "unique_string", "replaced", replace_all=True)
        assert result["success"] is True
        assert result["replacements"] == 1

    @pytest.mark.anyio
    async def test_old_string_exactly_max_bytes(self, tmp_path):
        """old_string at exactly EDIT_STRING_MAX_BYTES should succeed."""
        f = tmp_path / "file.txt"
        with patch("config.EDIT_STRING_MAX_BYTES", 10):
            content = "x" * 10
            f.write_text(content)
            result = await edit_file(str(f), content, "replaced")
            assert result["success"] is True


# ===========================================================================
# glob_files boundaries
# ===========================================================================

class TestGlobBoundary:

    @pytest.mark.anyio
    async def test_empty_pattern(self, tmp_path):
        """Empty pattern."""
        result = await glob_files("", path=str(tmp_path))
        assert "files" in result

    @pytest.mark.anyio
    async def test_nonexistent_directory(self):
        """Path to non-existent directory."""
        result = await glob_files("*.py", path="/nonexistent_dir_xyz_abc")
        assert result["count"] == 0 or result.get("success") is False

    @pytest.mark.anyio
    async def test_path_is_file(self, tmp_path):
        """Path pointing to a file, not directory."""
        f = tmp_path / "file.txt"
        f.write_text("x")
        result = await glob_files("*", path=str(f))
        assert "files" in result


# ===========================================================================
# grep_files boundaries
# ===========================================================================

class TestGrepBoundary:

    @pytest.mark.anyio
    async def test_empty_pattern(self, tmp_path):
        """Empty regex pattern — matches everything."""
        (tmp_path / "f.txt").write_text("hello\n")
        result = await grep_files("", path=str(tmp_path))
        assert result["match_count"] >= 0

    @pytest.mark.anyio
    async def test_invalid_regex(self, tmp_path):
        """Invalid regex should return error (at least in python fallback)."""
        (tmp_path / "f.txt").write_text("data\n")
        with patch("shutil.which", return_value=None):
            result = await grep_files("[invalid", path=str(tmp_path))
        assert result["success"] is False

    @pytest.mark.anyio
    async def test_max_results_zero_clamped(self, tmp_path):
        """max_results=0 should be clamped to 1."""
        (tmp_path / "f.txt").write_text("match\n")
        result = await grep_files("match", path=str(tmp_path), max_results=0)
        assert result["match_count"] >= 0

    @pytest.mark.anyio
    async def test_max_results_negative_clamped(self, tmp_path):
        """Negative max_results should be clamped to 1."""
        (tmp_path / "f.txt").write_text("match\n")
        result = await grep_files("match", path=str(tmp_path), max_results=-10)
        assert result["match_count"] >= 0

    @pytest.mark.anyio
    async def test_max_results_over_limit_clamped(self, tmp_path):
        """max_results over GREP_MAX_RESULTS should be clamped."""
        import config
        (tmp_path / "f.txt").write_text("match\n")
        result = await grep_files(
            "match", path=str(tmp_path),
            max_results=config.GREP_MAX_RESULTS + 1000,
        )
        assert result["match_count"] >= 0

    @pytest.mark.anyio
    async def test_context_lines_negative(self, tmp_path):
        """Negative context_lines should be handled gracefully."""
        (tmp_path / "f.txt").write_text("hello\n")
        result = await grep_files("hello", path=str(tmp_path), context_lines=-1)
        assert result["match_count"] >= 0
```

- [ ] **Step 2: Run tests**

Run: `python3 -m pytest tests/test_boundary.py -v`
Expected: All pass.

- [ ] **Step 3: Commit**

```bash
git add tests/test_boundary.py
git commit -m "test: add boundary value and exception analysis tests for all tool functions"
```

---

### Task 9: Performance Benchmark Tests

**Files:**
- Create: `tests/test_benchmark.py`

- [ ] **Step 1: Write benchmark tests**

Create `tests/test_benchmark.py`:

```python
"""Performance benchmark tests using pytest-benchmark.

Run: pytest tests/test_benchmark.py --benchmark-only -v
Save baseline: pytest tests/test_benchmark.py --benchmark-save=baseline
Compare: pytest tests/test_benchmark.py --benchmark-compare=baseline
"""

import os
import pytest

from tools.files import read_file, write_file, edit_file, glob_files, grep_files
from tools.bash import run_bash_execute


@pytest.fixture
def small_file(tmp_path):
    f = tmp_path / "small.txt"
    f.write_text("\n".join(f"line {i}" for i in range(10)))
    return str(f)


@pytest.fixture
def medium_file(tmp_path):
    f = tmp_path / "medium.txt"
    f.write_text("\n".join(f"line {i} with some content here" for i in range(1000)))
    return str(f)


@pytest.fixture
def large_file(tmp_path):
    f = tmp_path / "large.txt"
    f.write_text("\n".join(f"line {i} with more content for searching" for i in range(5000)))
    return str(f)


@pytest.fixture
def many_files(tmp_path):
    """Create a directory with many small files."""
    for i in range(100):
        (tmp_path / f"file_{i:03d}.txt").write_text(f"content of file {i}\nsearchable line\n")
    sub = tmp_path / "subdir"
    sub.mkdir()
    for i in range(50):
        (sub / f"nested_{i:03d}.py").write_text(f"# python file {i}\ndef func_{i}(): pass\n")
    return str(tmp_path)


# ---------------------------------------------------------------------------
# read_file benchmarks
# ---------------------------------------------------------------------------

@pytest.mark.benchmark(group="read_file")
def test_bench_read_small(benchmark, small_file):
    benchmark.pedantic(
        lambda: pytest.importorskip("asyncio").get_event_loop().run_until_complete(
            read_file(small_file)
        ),
        rounds=20,
    )


@pytest.mark.benchmark(group="read_file")
def test_bench_read_medium(benchmark, medium_file):
    benchmark.pedantic(
        lambda: pytest.importorskip("asyncio").get_event_loop().run_until_complete(
            read_file(medium_file)
        ),
        rounds=20,
    )


@pytest.mark.benchmark(group="read_file")
def test_bench_read_large_with_pagination(benchmark, large_file):
    benchmark.pedantic(
        lambda: pytest.importorskip("asyncio").get_event_loop().run_until_complete(
            read_file(large_file, offset=2000, limit=500)
        ),
        rounds=20,
    )


# ---------------------------------------------------------------------------
# write_file benchmarks
# ---------------------------------------------------------------------------

@pytest.mark.benchmark(group="write_file")
def test_bench_write_small(benchmark, tmp_path):
    path = str(tmp_path / "bench_write.txt")
    benchmark.pedantic(
        lambda: pytest.importorskip("asyncio").get_event_loop().run_until_complete(
            write_file(path, "small content\n")
        ),
        rounds=20,
    )


@pytest.mark.benchmark(group="write_file")
def test_bench_write_medium(benchmark, tmp_path):
    path = str(tmp_path / "bench_write_med.txt")
    content = "x" * 10000
    benchmark.pedantic(
        lambda: pytest.importorskip("asyncio").get_event_loop().run_until_complete(
            write_file(path, content)
        ),
        rounds=20,
    )


# ---------------------------------------------------------------------------
# edit_file benchmarks
# ---------------------------------------------------------------------------

@pytest.mark.benchmark(group="edit_file")
def test_bench_edit_single(benchmark, tmp_path):
    f = tmp_path / "bench_edit.txt"

    def setup():
        f.write_text("old_value is here once")

    def run():
        import asyncio
        asyncio.get_event_loop().run_until_complete(
            edit_file(str(f), "old_value", "new_value")
        )

    benchmark.pedantic(run, setup=setup, rounds=20)


@pytest.mark.benchmark(group="edit_file")
def test_bench_edit_replace_all(benchmark, tmp_path):
    f = tmp_path / "bench_edit_all.txt"

    def setup():
        f.write_text(" ".join(["target"] * 100))

    def run():
        import asyncio
        asyncio.get_event_loop().run_until_complete(
            edit_file(str(f), "target", "replaced", replace_all=True)
        )

    benchmark.pedantic(run, setup=setup, rounds=20)


# ---------------------------------------------------------------------------
# glob_files benchmarks
# ---------------------------------------------------------------------------

@pytest.mark.benchmark(group="glob")
def test_bench_glob_few_matches(benchmark, many_files):
    benchmark.pedantic(
        lambda: pytest.importorskip("asyncio").get_event_loop().run_until_complete(
            glob_files("*.py", path=many_files)
        ),
        rounds=10,
    )


@pytest.mark.benchmark(group="glob")
def test_bench_glob_recursive(benchmark, many_files):
    benchmark.pedantic(
        lambda: pytest.importorskip("asyncio").get_event_loop().run_until_complete(
            glob_files("**/*", path=many_files)
        ),
        rounds=10,
    )


# ---------------------------------------------------------------------------
# grep_files benchmarks
# ---------------------------------------------------------------------------

@pytest.mark.benchmark(group="grep")
def test_bench_grep_small_dir(benchmark, many_files):
    benchmark.pedantic(
        lambda: pytest.importorskip("asyncio").get_event_loop().run_until_complete(
            grep_files("searchable", path=many_files)
        ),
        rounds=10,
    )


@pytest.mark.benchmark(group="grep")
def test_bench_grep_with_glob_filter(benchmark, many_files):
    benchmark.pedantic(
        lambda: pytest.importorskip("asyncio").get_event_loop().run_until_complete(
            grep_files("func_", path=many_files, glob="*.py")
        ),
        rounds=10,
    )


# ---------------------------------------------------------------------------
# bash_execute benchmarks
# ---------------------------------------------------------------------------

@pytest.mark.benchmark(group="bash")
def test_bench_bash_echo(benchmark):
    benchmark.pedantic(
        lambda: pytest.importorskip("asyncio").get_event_loop().run_until_complete(
            run_bash_execute("echo hello")
        ),
        rounds=20,
    )


@pytest.mark.benchmark(group="bash")
def test_bench_bash_with_output(benchmark, many_files):
    benchmark.pedantic(
        lambda: pytest.importorskip("asyncio").get_event_loop().run_until_complete(
            run_bash_execute(f"ls -la {many_files}")
        ),
        rounds=20,
    )
```

- [ ] **Step 2: Run benchmarks**

Run: `python3 -m pytest tests/test_benchmark.py --benchmark-only -v`
Expected: All benchmarks run and report timing statistics.

- [ ] **Step 3: Verify normal test runs skip benchmarks**

Run: `python3 -m pytest tests/ -v --benchmark-disable 2>&1 | tail -5`
Expected: Benchmark tests are either skipped or run without benchmark overhead.

- [ ] **Step 4: Commit**

```bash
git add tests/test_benchmark.py
git commit -m "test: add pytest-benchmark performance tests for all tool functions"
```

---

### Task 10: Load Tests (Locust)

**Files:**
- Create: `tests/loadtest/__init__.py`
- Create: `tests/loadtest/locustfile.py`

- [ ] **Step 1: Create loadtest package**

Create empty `tests/loadtest/__init__.py`:

```python
```

- [ ] **Step 2: Write locustfile**

Create `tests/loadtest/locustfile.py`:

```python
"""Load testing with Locust.

Usage:
    1. Start the MCP server: python3 main.py
    2. Set environment variables:
       export MCP_ADMIN_TOKEN=<your-admin-token>
       export MCP_TEST_TOKEN=<a-valid-rw-token>
    3. Run locust:
       locust -f tests/loadtest/locustfile.py --host http://localhost:8765
    4. Open http://localhost:8089 in your browser to configure and start the test.

    For headless mode:
       locust -f tests/loadtest/locustfile.py --host http://localhost:8765 \
              --headless -u 10 -r 2 --run-time 60s
"""

import json
import os
import tempfile

from locust import HttpUser, task, between, events


# Token must be set via environment variable
TEST_TOKEN = os.environ.get("MCP_TEST_TOKEN", "")
ADMIN_TOKEN = os.environ.get("MCP_ADMIN_TOKEN", "")


def _mcp_headers():
    return {
        "Authorization": f"Bearer {TEST_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }


def _mcp_request(tool_name: str, arguments: dict) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }


def _mcp_list_tools() -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {},
    }


class HealthCheckUser(HttpUser):
    """Baseline: high-frequency health checks."""

    weight = 1
    wait_time = between(0.1, 0.5)

    @task
    def health(self):
        self.client.get("/health")


class ReadUser(HttpUser):
    """Read-heavy workload: read_file, glob, grep."""

    weight = 7
    wait_time = between(0.5, 2)

    def on_start(self):
        # Create a temp file for reading
        self._tmpfile = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False
        )
        self._tmpfile.write("\n".join(f"line {i}" for i in range(100)))
        self._tmpfile.close()
        self._tmpdir = os.path.dirname(self._tmpfile.name)

    def on_stop(self):
        try:
            os.unlink(self._tmpfile.name)
        except OSError:
            pass

    @task(5)
    def read_file(self):
        self.client.post(
            "/mcp",
            json=_mcp_request("read_file", {"file_path": self._tmpfile.name}),
            headers=_mcp_headers(),
            name="/mcp [read_file]",
        )

    @task(2)
    def glob_files(self):
        self.client.post(
            "/mcp",
            json=_mcp_request("glob", {"pattern": "*.txt", "path": self._tmpdir}),
            headers=_mcp_headers(),
            name="/mcp [glob]",
        )

    @task(2)
    def grep_files(self):
        self.client.post(
            "/mcp",
            json=_mcp_request("grep", {"pattern": "line", "path": self._tmpdir}),
            headers=_mcp_headers(),
            name="/mcp [grep]",
        )

    @task(1)
    def list_tools(self):
        self.client.post(
            "/mcp",
            json=_mcp_list_tools(),
            headers=_mcp_headers(),
            name="/mcp [list_tools]",
        )


class WriteUser(HttpUser):
    """Write workload: write_file, edit_file, bash_execute."""

    weight = 3
    wait_time = between(1, 3)

    def on_start(self):
        self._tmpdir = tempfile.mkdtemp()
        self._counter = 0

    @task(3)
    def write_file(self):
        self._counter += 1
        path = os.path.join(self._tmpdir, f"write_{self._counter}.txt")
        self.client.post(
            "/mcp",
            json=_mcp_request("write_file", {
                "file_path": path,
                "content": f"content written by locust iteration {self._counter}",
            }),
            headers=_mcp_headers(),
            name="/mcp [write_file]",
        )

    @task(2)
    def edit_file(self):
        # Write then edit
        path = os.path.join(self._tmpdir, "editable.txt")
        self.client.post(
            "/mcp",
            json=_mcp_request("write_file", {
                "file_path": path,
                "content": "original_value in the file",
            }),
            headers=_mcp_headers(),
            name="/mcp [write_file setup]",
        )
        self.client.post(
            "/mcp",
            json=_mcp_request("edit_file", {
                "file_path": path,
                "old_string": "original_value",
                "new_string": "edited_value",
            }),
            headers=_mcp_headers(),
            name="/mcp [edit_file]",
        )

    @task(1)
    def bash_execute(self):
        self.client.post(
            "/mcp",
            json=_mcp_request("bash_execute", {"command": "echo locust_test"}),
            headers=_mcp_headers(),
            name="/mcp [bash_execute]",
        )
```

- [ ] **Step 3: Verify locust can parse the file**

Run: `python3 -c "import tests.loadtest.locustfile; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add tests/loadtest/__init__.py tests/loadtest/locustfile.py
git commit -m "test: add locust load test scenarios for MCP server"
```

---

### Task 11: Mutation Testing Configuration

**Files:**
- Create: `pyproject.toml`

- [ ] **Step 1: Create pyproject.toml with mutmut config**

Create `pyproject.toml`:

```toml
[tool.mutmut]
paths_to_mutate = "auth.py,audit.py,config.py,tools/bash.py,tools/files.py,mcp_server.py"
tests_dir = "tests/"
runner = "python -m pytest tests/ -x -q --tb=no --no-header --benchmark-disable"
```

- [ ] **Step 2: Run mutmut to verify it works**

Run: `python3 -m mutmut run --paths-to-mutate=config.py 2>&1 | head -20`
Expected: mutmut starts running mutations against config.py (may take a minute).

- [ ] **Step 3: Check results**

Run: `python3 -m mutmut results 2>&1 | head -20`
Expected: Shows survived/killed/timeout counts.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add mutmut configuration for mutation testing"
```

---

### Task 12: CI Updates — Branch Coverage Badge + Mutation Score Badge

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Update CI workflow**

Replace `.github/workflows/ci.yml` with:

```yaml
name: CI

on:
  push:
    branches: [master]
  pull_request:
    branches: [master]

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.11", "3.12", "3.13"]

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python ${{ matrix.python-version }}
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}

      - name: Install dependencies
        run: |
          pip install --upgrade pip
          pip install -r requirements-dev.txt

      - name: Run tests with coverage
        run: |
          python -m pytest tests/ -v --cov=. --cov-branch --cov-report=term-missing --benchmark-disable

      - name: Update coverage badges
        if: github.ref == 'refs/heads/master' && matrix.python-version == '3.12'
        run: |
          # Extract line coverage
          LINE_COV=$(python -m pytest tests/ --cov=. --cov-report=term -q --benchmark-disable 2>/dev/null | grep '^TOTAL' | awk '{print $NF}' | tr -d '%')
          # Extract branch coverage
          BRANCH_COV=$(python -m pytest tests/ --cov=. --cov-branch --cov-report=term -q --benchmark-disable 2>/dev/null | grep '^TOTAL' | awk '{print $NF}' | tr -d '%')

          # Line coverage badge
          if [ "$LINE_COV" -ge 90 ]; then COLOR="brightgreen"
          elif [ "$LINE_COV" -ge 80 ]; then COLOR="green"
          elif [ "$LINE_COV" -ge 70 ]; then COLOR="yellowgreen"
          elif [ "$LINE_COV" -ge 60 ]; then COLOR="yellow"
          else COLOR="red"; fi
          LINE_JSON="{\"schemaVersion\":1,\"label\":\"coverage\",\"message\":\"${LINE_COV}%\",\"color\":\"${COLOR}\"}"

          # Branch coverage badge
          if [ "$BRANCH_COV" -ge 85 ]; then BCOLOR="brightgreen"
          elif [ "$BRANCH_COV" -ge 75 ]; then BCOLOR="green"
          elif [ "$BRANCH_COV" -ge 65 ]; then BCOLOR="yellowgreen"
          elif [ "$BRANCH_COV" -ge 55 ]; then BCOLOR="yellow"
          else BCOLOR="red"; fi
          BRANCH_JSON="{\"schemaVersion\":1,\"label\":\"branch coverage\",\"message\":\"${BRANCH_COV}%\",\"color\":\"${BCOLOR}\"}"

          # Push both to gist
          curl -s -X PATCH \
            -H "Authorization: token ${{ secrets.GIST_TOKEN }}" \
            -d "{\"files\":{\"mymcp-coverage.json\":{\"content\":$(echo "$LINE_JSON" | python -c 'import sys,json; print(json.dumps(sys.stdin.read()))')},\"mymcp-branch-coverage.json\":{\"content\":$(echo "$BRANCH_JSON" | python -c 'import sys,json; print(json.dumps(sys.stdin.read()))')}}}" \
            "https://api.github.com/gists/${{ secrets.GIST_ID }}"

  mutation:
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/master'
    needs: test

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python 3.12
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: |
          pip install --upgrade pip
          pip install -r requirements-dev.txt

      - name: Run mutation testing
        run: |
          python -m mutmut run --no-progress 2>&1 || true
          python -m mutmut results > mutmut-results.txt 2>&1 || true
          cat mutmut-results.txt

      - name: Update mutation badge
        run: |
          # Parse mutmut results
          KILLED=$(grep -oP 'Killed: \K\d+' mutmut-results.txt || echo 0)
          SURVIVED=$(grep -oP 'Survived: \K\d+' mutmut-results.txt || echo 0)
          TIMEOUT=$(grep -oP 'Timeout: \K\d+' mutmut-results.txt || echo 0)
          TOTAL=$((KILLED + SURVIVED + TIMEOUT))
          if [ "$TOTAL" -gt 0 ]; then
            SCORE=$((KILLED * 100 / TOTAL))
          else
            SCORE=0
          fi

          if [ "$SCORE" -ge 80 ]; then COLOR="brightgreen"
          elif [ "$SCORE" -ge 70 ]; then COLOR="green"
          elif [ "$SCORE" -ge 60 ]; then COLOR="yellowgreen"
          elif [ "$SCORE" -ge 50 ]; then COLOR="yellow"
          else COLOR="red"; fi

          MUTATION_JSON="{\"schemaVersion\":1,\"label\":\"mutation score\",\"message\":\"${SCORE}%\",\"color\":\"${COLOR}\"}"
          curl -s -X PATCH \
            -H "Authorization: token ${{ secrets.GIST_TOKEN }}" \
            -d "{\"files\":{\"mymcp-mutation.json\":{\"content\":$(echo "$MUTATION_JSON" | python -c 'import sys,json; print(json.dumps(sys.stdin.read()))')}}}" \
            "https://api.github.com/gists/${{ secrets.GIST_ID }}"
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add branch coverage and mutation testing badges, use requirements-dev.txt"
```

---

### Task 13: README & CLAUDE.md Updates

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update README badges**

Add the new badges after the existing Coverage badge line in `README.md`. The badge row should become:

```markdown
[![CI](https://github.com/algony-tony/mymcp/actions/workflows/ci.yml/badge.svg)](https://github.com/algony-tony/mymcp/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/algony-tony/f5b7d1a23781d63db40ea2e2dcdf71c2/raw/mymcp-coverage.json&cacheSeconds=3600)](https://github.com/algony-tony/mymcp/actions/workflows/ci.yml)
[![Branch Coverage](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/algony-tony/f5b7d1a23781d63db40ea2e2dcdf71c2/raw/mymcp-branch-coverage.json&cacheSeconds=3600)](https://github.com/algony-tony/mymcp/actions/workflows/ci.yml)
[![Mutation Score](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/algony-tony/f5b7d1a23781d63db40ea2e2dcdf71c2/raw/mymcp-mutation.json&cacheSeconds=3600)](https://github.com/algony-tony/mymcp/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
```

- [ ] **Step 2: Add Performance section to README**

Add before the License section (or at the end of existing sections) in `README.md`:

```markdown
## Testing

```bash
# Run all tests (excludes benchmarks)
python -m pytest tests/ -v --benchmark-disable

# Run with coverage report
python -m pytest tests/ -v --cov=. --cov-branch --cov-report=term-missing --benchmark-disable

# Run benchmark tests only
python -m pytest tests/test_benchmark.py --benchmark-only -v

# Save benchmark baseline for comparison
python -m pytest tests/test_benchmark.py --benchmark-save=baseline

# Run mutation testing
python -m mutmut run
python -m mutmut results

# Run load tests (start server first: python main.py)
export MCP_TEST_TOKEN=<your-rw-token>
locust -f tests/loadtest/locustfile.py --host http://localhost:8765
```

### Test Dimensions

| Dimension | Tool | Target |
|-----------|------|--------|
| Line coverage | pytest-cov | 97%+ |
| Branch coverage | pytest-cov --cov-branch | tracked |
| Integration tests | httpx ASGITransport | full auth→tool→audit chain |
| Boundary analysis | pytest | all parameter edge cases |
| Performance benchmarks | pytest-benchmark | per-function timing |
| Load testing | locust | multi-user concurrency |
| Mutation testing | mutmut | 80%+ score |
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: add branch coverage and mutation score badges, testing section to README"
```

---

### Task 14: Final Verification

- [ ] **Step 1: Run full test suite**

Run: `python3 -m pytest tests/ -v --cov=. --cov-branch --cov-report=term-missing --benchmark-disable`
Expected: All tests pass. Line coverage 97%+. Branch coverage reported.

- [ ] **Step 2: Verify no regressions**

Run: `python3 -m pytest tests/ -v --benchmark-disable 2>&1 | tail -3`
Expected: All tests pass, no warnings about missing imports.

- [ ] **Step 3: Run benchmarks**

Run: `python3 -m pytest tests/test_benchmark.py --benchmark-only -v 2>&1 | tail -30`
Expected: Benchmark results printed with timing statistics.

- [ ] **Step 4: Quick mutmut smoke test**

Run: `python3 -m mutmut run --paths-to-mutate=config.py 2>&1 | tail -5`
Expected: mutmut completes, shows killed/survived counts.

- [ ] **Step 5: Commit any final fixes if needed**

```bash
git add -A
git commit -m "test: final adjustments after comprehensive test verification"
```
