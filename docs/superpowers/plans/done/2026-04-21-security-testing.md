# Security Testing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `tests/test_security.py` (pytest, CI-runnable via ASGI transport) and `tests/pentest.py` (standalone black-box script for live deployments) covering auth boundary, privilege escalation, path traversal, information leakage, and HTTP layer attacks.

**Architecture:** Security tests reuse the existing ASGI transport fixture pattern from `test_integration.py`. Path traversal tests call tool functions directly (no HTTP). `pentest.py` is a standalone httpx script with no pytest dependency.

**Tech Stack:** pytest + anyio, httpx, ASGITransport, stdlib only for pentest.py

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `tests/test_security.py` | All pytest security tests (5 categories, 19 test cases) |
| Create | `tests/pentest.py` | Standalone black-box pentest script for live instances |

No existing files are modified.

---

## Task 1: Skeleton + Fixtures

**Files:**
- Create: `tests/test_security.py`

- [ ] **Step 1: Create the file with all fixtures**

```python
"""Security tests for mymcp MCP server.

Covers: auth boundary, privilege escalation, path traversal,
information leakage, HTTP layer. Uses ASGI transport (no live server needed).
"""
import json
import os
import pytest
from unittest.mock import patch

from httpx import AsyncClient, ASGITransport
from auth import TokenStore
from mcp_server import call_tool, list_tools


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sec_store(tmp_path):
    return TokenStore(str(tmp_path / "tokens.json"), "adm_sectest")


@pytest.fixture
def rw_token(sec_store):
    return sec_store.create_token("rw-client", role="rw")


@pytest.fixture
def ro_token(sec_store):
    return sec_store.create_token("ro-client", role="ro")


@pytest.fixture
def disabled_token(sec_store):
    token = sec_store.create_token("disabled", role="ro")
    with sec_store._lock:
        sec_store._data["tokens"][token]["enabled"] = False
        sec_store._save()
    return token


@pytest.fixture
def sec_app(sec_store):
    """FastAPI app wired to sec_store with a fake MCP session_manager."""
    import auth
    original = auth._store
    auth._store = sec_store
    from main import app
    from starlette.responses import JSONResponse

    async def fake_handle_request(scope, receive, send):
        from starlette.requests import Request
        request = Request(scope, receive, send)
        body = await request.body()
        try:
            rpc = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            await JSONResponse({"error": "Invalid JSON"}, status_code=400)(scope, receive, send)
            return
        method = rpc.get("method", "")
        params = rpc.get("params", {})
        rpc_id = rpc.get("id", 1)
        if method == "tools/list":
            tools = await list_tools()
            result = {"tools": [{"name": t.name} for t in tools]}
        elif method == "tools/call":
            items = await call_tool(params.get("name", ""), params.get("arguments", {}))
            result = {"content": [{"type": i.type, "text": i.text} for i in items]}
        else:
            result = {"error": f"Unknown method: {method}"}
        await JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "result": result})(scope, receive, send)

    try:
        with patch("main.session_manager") as mock_sm:
            mock_sm.handle_request = fake_handle_request
            yield app
    finally:
        auth._store = original


@pytest.fixture
async def sec_client(sec_app):
    transport = ASGITransport(app=sec_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def protected_dirs(tmp_path):
    app_dir = str(tmp_path / "mymcp")
    audit_dir = str(tmp_path / "audit")
    os.makedirs(app_dir)
    os.makedirs(audit_dir)
    with patch("config.PROTECTED_PATHS", [app_dir, audit_dir]):
        yield app_dir, audit_dir
```

- [ ] **Step 2: Verify the file parses and fixtures are importable**

```bash
python3 -m pytest tests/test_security.py --collect-only
```

Expected: `no tests ran` (no test functions yet), no import errors.

- [ ] **Step 3: Commit**

```bash
git add tests/test_security.py
git commit -m "test(security): add test_security.py skeleton and fixtures"
```

---

## Task 2: Authentication Boundary Tests

**Files:**
- Modify: `tests/test_security.py` (append 5 tests)

- [ ] **Step 1: Append the 5 auth boundary tests**

```python
# ---------------------------------------------------------------------------
# Category 1: Authentication Boundary
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_auth_no_header_returns_401(sec_client):
    resp = await sec_client.post("/mcp", content=b"{}")
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_auth_empty_token_after_bearer_returns_401(sec_client):
    resp = await sec_client.post(
        "/mcp", content=b"{}",
        headers={"Authorization": "Bearer "},
    )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_auth_no_bearer_prefix_returns_401(sec_client):
    resp = await sec_client.post(
        "/mcp", content=b"{}",
        headers={"Authorization": "tok_whatever"},
    )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_auth_admin_token_rejected_as_user_token(sec_client):
    resp = await sec_client.post(
        "/mcp", content=b"{}",
        headers={"Authorization": "Bearer adm_sectest"},
    )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_auth_disabled_token_returns_401(sec_client, disabled_token):
    resp = await sec_client.post(
        "/mcp", content=b"{}",
        headers={"Authorization": f"Bearer {disabled_token}"},
    )
    assert resp.status_code == 401
```

- [ ] **Step 2: Run the auth tests**

```bash
python3 -m pytest tests/test_security.py -k "test_auth" -v --benchmark-disable
```

Expected: 5 PASSED.

- [ ] **Step 3: Commit**

```bash
git add tests/test_security.py
git commit -m "test(security): add auth boundary tests"
```

---

## Task 3: Privilege Escalation Tests

**Files:**
- Modify: `tests/test_security.py` (append 4 tests)

- [ ] **Step 1: Append the 4 privilege escalation tests**

```python
# ---------------------------------------------------------------------------
# Category 2: Privilege Escalation
# ---------------------------------------------------------------------------

def _mcp_call_payload(tool_name: str, arguments: dict) -> dict:
    return {
        "jsonrpc": "2.0", "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }


def _mcp_list_payload() -> dict:
    return {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}


async def _call_with_token(client, token: str, payload: dict) -> dict:
    resp = await client.post(
        "/mcp", json=payload,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    return resp.json()


@pytest.mark.anyio
async def test_privesc_ro_cannot_bash_execute(sec_client, ro_token):
    data = await _call_with_token(
        sec_client, ro_token,
        _mcp_call_payload("bash_execute", {"command": "id"}),
    )
    result = json.loads(data["result"]["content"][0]["text"])
    assert result["success"] is False
    assert result["error"] == "PermissionDenied"


@pytest.mark.anyio
async def test_privesc_ro_cannot_write_file(sec_client, ro_token, tmp_path):
    data = await _call_with_token(
        sec_client, ro_token,
        _mcp_call_payload("write_file", {
            "file_path": str(tmp_path / "evil.txt"),
            "content": "x",
        }),
    )
    result = json.loads(data["result"]["content"][0]["text"])
    assert result["success"] is False
    assert result["error"] == "PermissionDenied"


@pytest.mark.anyio
async def test_privesc_ro_cannot_edit_file(sec_client, ro_token, tmp_path):
    data = await _call_with_token(
        sec_client, ro_token,
        _mcp_call_payload("edit_file", {
            "file_path": str(tmp_path / "x.txt"),
            "old_string": "a",
            "new_string": "b",
        }),
    )
    result = json.loads(data["result"]["content"][0]["text"])
    assert result["success"] is False
    assert result["error"] == "PermissionDenied"


@pytest.mark.anyio
async def test_privesc_ro_list_tools_excludes_write_tools(sec_client, ro_token):
    data = await _call_with_token(sec_client, ro_token, _mcp_list_payload())
    tool_names = {t["name"] for t in data["result"]["tools"]}
    assert "bash_execute" not in tool_names
    assert "write_file" not in tool_names
    assert "edit_file" not in tool_names
    assert "read_file" in tool_names
```

- [ ] **Step 2: Run the privilege escalation tests**

```bash
python3 -m pytest tests/test_security.py -k "test_privesc" -v --benchmark-disable
```

Expected: 4 PASSED.

- [ ] **Step 3: Commit**

```bash
git add tests/test_security.py
git commit -m "test(security): add privilege escalation tests"
```

---

## Task 4: Path Traversal Tests

**Files:**
- Modify: `tests/test_security.py` (append 4 tests)

Note: `read_file` only catches `FileNotFoundError`, `IsADirectoryError`, `PermissionError` — a `ValueError` from a null-byte path propagates to `call_tool`'s `except Exception` handler, which returns `{"success": False, "error": "InternalError", ...}`. The null-byte test therefore goes through `_call_with_token` (full MCP dispatch).

- [ ] **Step 1: Append the 4 path traversal tests**

```python
# ---------------------------------------------------------------------------
# Category 3: Path Traversal
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_path_traversal_dotdot_into_protected_dir(protected_dirs):
    """Path with ../ components that resolve into a protected dir must be blocked."""
    from tools.files import read_file
    app_dir, _ = protected_dirs
    secret = os.path.join(app_dir, "secret.txt")
    with open(secret, "w") as f:
        f.write("SECRET")
    # Construct a path that uses ../ but resolves to the protected dir
    parent = os.path.dirname(app_dir)
    traversal = os.path.join(parent, "mymcp", "..", "mymcp", "secret.txt")
    result = await read_file(traversal)
    assert result["success"] is False
    assert "protected" in result["message"].lower()


@pytest.mark.anyio
async def test_path_traversal_symlink_into_protected_dir(protected_dirs, tmp_path):
    """A symlink outside the protected dir that points inside must be blocked."""
    from tools.files import read_file
    app_dir, _ = protected_dirs
    secret = os.path.join(app_dir, "secret.txt")
    with open(secret, "w") as f:
        f.write("SECRET")
    link = str(tmp_path / "sneaky_link")
    os.symlink(secret, link)
    result = await read_file(link)
    assert result["success"] is False
    assert "protected" in result["message"].lower()


@pytest.mark.anyio
async def test_path_traversal_null_byte_does_not_expose_content(sec_client, rw_token):
    """A path with an embedded null byte must not return file contents; server must not crash."""
    data = await _call_with_token(
        sec_client, rw_token,
        _mcp_call_payload("read_file", {"file_path": "/tmp/pentest_null\x00/etc/shadow"}),
    )
    result = json.loads(data["result"]["content"][0]["text"])
    # Either an error OR the content doesn't contain shadow file data
    if result.get("success") is not False:
        assert "root:" not in result.get("content", "")


@pytest.mark.anyio
async def test_path_traversal_exact_protected_dir_blocked(protected_dirs):
    """Passing the exact protected directory path must be blocked."""
    from tools.files import read_file
    app_dir, _ = protected_dirs
    result = await read_file(app_dir)
    assert result["success"] is False
```

- [ ] **Step 2: Run the path traversal tests**

```bash
python3 -m pytest tests/test_security.py -k "test_path_traversal" -v --benchmark-disable
```

Expected: 4 PASSED.

- [ ] **Step 3: Commit**

```bash
git add tests/test_security.py
git commit -m "test(security): add path traversal tests"
```

---

## Task 5: Information Leakage Tests

**Files:**
- Modify: `tests/test_security.py` (append 3 tests)

- [ ] **Step 1: Append the 3 information leakage tests**

```python
# ---------------------------------------------------------------------------
# Category 4: Information Leakage
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_leakage_401_body_has_no_stack_trace(sec_client):
    """A 401 response must not contain Python stack trace fragments."""
    resp = await sec_client.post("/mcp", content=b"{}")
    assert resp.status_code == 401
    body = resp.text
    assert "Traceback" not in body
    assert 'File "' not in body


@pytest.mark.anyio
async def test_leakage_permission_denied_does_not_echo_token(sec_client, ro_token):
    """PermissionDenied error response must not contain the caller's token value."""
    data = await _call_with_token(
        sec_client, ro_token,
        _mcp_call_payload("bash_execute", {"command": "id"}),
    )
    response_text = json.dumps(data)
    assert ro_token not in response_text


@pytest.mark.anyio
async def test_leakage_internal_error_has_no_traceback(sec_client, rw_token):
    """An unhandled exception in a tool must return a generic InternalError message
    with no stack trace in the MCP response."""
    from unittest.mock import AsyncMock
    with patch("mcp_server.dispatch_tool", new_callable=AsyncMock) as mock_dispatch:
        mock_dispatch.side_effect = RuntimeError("intentional test error")
        data = await _call_with_token(
            sec_client, rw_token,
            _mcp_call_payload("read_file", {"file_path": "/tmp/x"}),
        )
    result = json.loads(data["result"]["content"][0]["text"])
    assert result["success"] is False
    assert result["error"] == "InternalError"
    assert "intentional test error" not in result.get("message", "")
    assert "Traceback" not in json.dumps(data)
```

- [ ] **Step 2: Run the information leakage tests**

```bash
python3 -m pytest tests/test_security.py -k "test_leakage" -v --benchmark-disable
```

Expected: 3 PASSED.

- [ ] **Step 3: Commit**

```bash
git add tests/test_security.py
git commit -m "test(security): add information leakage tests"
```

---

## Task 6: HTTP Layer Tests

**Files:**
- Modify: `tests/test_security.py` (append 3 tests)

- [ ] **Step 1: Append the 3 HTTP layer tests**

```python
# ---------------------------------------------------------------------------
# Category 5: HTTP Layer
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_http_oversized_authorization_header(sec_client):
    """An Authorization header larger than 8KB must not crash the server."""
    oversized_token = "Bearer " + "x" * 9000
    resp = await sec_client.post(
        "/mcp", content=b"{}",
        headers={"Authorization": oversized_token},
    )
    assert resp.status_code in (400, 401)


@pytest.mark.anyio
async def test_http_bearer_token_with_newline_rejected(sec_client):
    """A Bearer token containing a newline must be rejected."""
    resp = await sec_client.post(
        "/mcp", content=b"{}",
        headers={"Authorization": "Bearer tok_valid\nX-Injected: evil"},
    )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_http_bearer_token_with_null_byte_rejected(sec_client):
    """A Bearer token containing a null byte must be rejected."""
    resp = await sec_client.post(
        "/mcp", content=b"{}",
        headers={"Authorization": "Bearer tok_valid\x00extra"},
    )
    assert resp.status_code == 401
```

- [ ] **Step 2: Run the HTTP layer tests**

```bash
python3 -m pytest tests/test_security.py -k "test_http" -v --benchmark-disable
```

Expected: 3 PASSED.

- [ ] **Step 3: Run the full test_security.py suite**

```bash
python3 -m pytest tests/test_security.py -v --benchmark-disable
```

Expected: 19 PASSED.

- [ ] **Step 4: Run the full test suite to check for regressions**

```bash
python3 -m pytest tests/ -v --benchmark-disable --ignore=tests/loadtest
```

Expected: all existing tests still pass.

- [ ] **Step 5: Commit**

```bash
git add tests/test_security.py
git commit -m "test(security): add HTTP layer tests, complete test_security.py"
```

---

## Task 7: Standalone Pentest Script

**Files:**
- Create: `tests/pentest.py`

- [ ] **Step 1: Create tests/pentest.py**

```python
#!/usr/bin/env python3
"""Black-box security test script for mymcp MCP server.

Targets a live running instance. No pytest dependency.

Usage:
    python3 tests/pentest.py --url http://localhost:8000 --token tok_xxxx
    python3 tests/pentest.py --url http://localhost:8000 --token tok_xxxx --ro-token tok_yyyy
"""
import argparse
import json
import sys

import httpx


def _tool_call(name: str, arguments: dict) -> dict:
    return {
        "jsonrpc": "2.0", "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


class PentestRunner:
    def __init__(self, url: str, token: str, ro_token: str | None):
        self.base_url = url.rstrip("/")
        self.token = token
        self.ro_token = ro_token
        self.passed = 0
        self.failed = 0

    def _post(self, payload: dict, token: str | None = None) -> httpx.Response:
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
            headers["Content-Type"] = "application/json"
        with httpx.Client(timeout=10) as client:
            return client.post(f"{self.base_url}/mcp", json=payload, headers=headers)

    def _post_raw(self, content: bytes, headers: dict) -> httpx.Response:
        with httpx.Client(timeout=10) as client:
            return client.post(f"{self.base_url}/mcp", content=content, headers=headers)

    def _check(self, name: str, passed: bool, detail: str = "") -> None:
        if passed:
            self.passed += 1
            print(f"[PASS] {name}")
        else:
            self.failed += 1
            msg = f"[FAIL] {name}"
            if detail:
                msg += f": {detail}"
            print(msg)

    def _parse_tool_result(self, data: dict) -> dict:
        try:
            return json.loads(data["result"]["content"][0]["text"])
        except (KeyError, IndexError, json.JSONDecodeError):
            return {}

    def run_auth_checks(self) -> None:
        print("\n--- Auth Checks ---")

        resp = self._post_raw(b"{}", {})
        self._check("No token → 401", resp.status_code == 401,
                    f"got {resp.status_code}")

        resp = self._post_raw(b"{}", {"Authorization": "Bearer tok_invalid_xxxxxxxxxxx"})
        self._check("Invalid token → 401", resp.status_code == 401,
                    f"got {resp.status_code}")

        resp = self._post_raw(b"{}", {"Authorization": "Bearer adm_fakeadmin"})
        self._check("Admin-format token → 401", resp.status_code == 401,
                    f"got {resp.status_code}")

        resp = self._post_raw(b"{}", {})
        body = resp.text
        no_trace = "Traceback" not in body and 'File "' not in body
        self._check("401 body has no stack trace", no_trace,
                    f"body snippet: {body[:200]}")

    def run_path_traversal(self) -> None:
        print("\n--- Path Traversal ---")

        payload = _tool_call("read_file", {"file_path": "/opt/mymcp/../mymcp/config.py"})
        resp = self._post(payload, self.token)
        if resp.status_code == 200:
            result = self._parse_tool_result(resp.json())
            blocked = result.get("success") is False
        else:
            blocked = False
        self._check("dotdot path into APP_DIR blocked", blocked,
                    f"success={not blocked}")

        null_payload = _tool_call("read_file", {"file_path": "/tmp/pentest_null\x00/etc/shadow"})
        try:
            resp = self._post(null_payload, self.token)
            if resp.status_code == 200:
                result = self._parse_tool_result(resp.json())
                safe = result.get("success") is False or "root:" not in result.get("content", "")
            else:
                safe = resp.status_code != 500
        except Exception as exc:
            safe = False
            self._check("Null-byte path does not expose /etc/shadow", False,
                        f"exception: {exc}")
            return
        self._check("Null-byte path does not expose /etc/shadow", safe)

    def run_privilege_escalation(self) -> None:
        print("\n--- Privilege Escalation ---")
        if not self.ro_token:
            print("[SKIP] --ro-token not provided, skipping privilege escalation tests")
            return

        for tool, args in [
            ("bash_execute", {"command": "id"}),
            ("write_file", {"file_path": "/tmp/pentest_privesc.txt", "content": "x"}),
            ("edit_file", {"file_path": "/tmp/pentest_privesc.txt",
                           "old_string": "x", "new_string": "y"}),
        ]:
            resp = self._post(_tool_call(tool, args), self.ro_token)
            if resp.status_code == 200:
                result = self._parse_tool_result(resp.json())
                denied = result.get("success") is False and result.get("error") == "PermissionDenied"
            else:
                denied = False
            self._check(f"ro token cannot call {tool}", denied,
                        f"error={result.get('error') if resp.status_code == 200 else resp.status_code}")

    def run_info_leakage(self) -> None:
        print("\n--- Information Leakage ---")

        resp = self._post_raw(b"{}", {"Authorization": f"Bearer {self.token}_invalid_suffix"})
        body = resp.text
        self._check(
            "401 body does not echo token value",
            self.token not in body,
            f"token found in: {body[:200]}",
        )

    def run_bash_edge_cases(self) -> None:
        print("\n--- Bash Edge Cases ---")

        for label, args in [
            ("timeout=0", {"command": "id", "timeout": 0}),
            ("timeout=99999", {"command": "id", "timeout": 99999}),
            ("empty command", {"command": ""}),
        ]:
            try:
                resp = self._post(_tool_call("bash_execute", args), self.token)
                ok = resp.status_code in (200, 400)
            except Exception as exc:
                ok = False
                self._check(f"bash_execute {label} does not crash", False,
                            f"exception: {exc}")
                continue
            self._check(f"bash_execute {label} does not crash", ok,
                        f"status={resp.status_code}")

    def run(self) -> int:
        print(f"Target: {self.base_url}")
        self.run_auth_checks()
        self.run_path_traversal()
        self.run_privilege_escalation()
        self.run_info_leakage()
        self.run_bash_edge_cases()
        print(f"\nResults: {self.passed} passed, {self.failed} failed")
        return 0 if self.failed == 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="mymcp security pentest script")
    parser.add_argument("--url", required=True,
                        help="Base URL of the server, e.g. http://localhost:8000")
    parser.add_argument("--token", required=True,
                        help="Valid rw Bearer token (tok_...)")
    parser.add_argument("--ro-token", dest="ro_token",
                        help="Valid ro Bearer token for privilege escalation tests")
    args = parser.parse_args()
    runner = PentestRunner(args.url, args.token, args.ro_token)
    sys.exit(runner.run())


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify the script parses without errors**

```bash
python3 tests/pentest.py --help
```

Expected: prints usage with `--url`, `--token`, `--ro-token` options.

- [ ] **Step 3: Commit**

```bash
git add tests/pentest.py
git commit -m "test(security): add pentest.py black-box script"
```

---

## Task 8: Push and PR

- [ ] **Step 1: Push branch**

```bash
git push -u origin security-testing
```

- [ ] **Step 2: Open PR**

```bash
gh pr create \
  --title "test(security): add security test suite and pentest script" \
  --body "$(cat <<'EOF'
## Summary
- Adds `tests/test_security.py` with 19 pytest security tests covering auth boundary, privilege escalation, path traversal, information leakage, and HTTP layer attacks
- Adds `tests/pentest.py`, a standalone black-box script for testing live deployments (mirrors `tests/loadtest/` pattern)

## Test plan
- [ ] `python3 -m pytest tests/test_security.py -v --benchmark-disable` → 19 PASSED
- [ ] `python3 -m pytest tests/ -v --benchmark-disable --ignore=tests/loadtest` → no regressions
- [ ] `python3 tests/pentest.py --help` → usage prints cleanly

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
