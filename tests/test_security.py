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
    # TokenStore has no public disable-without-revoke API; direct mutation is intentional.
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
    assert "glob" in tool_names
    assert "grep" in tool_names


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
    parent = os.path.dirname(app_dir)
    dir_name = os.path.basename(app_dir)
    traversal = os.path.join(parent, dir_name, "..", dir_name, "secret.txt")
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
    if result.get("success") is not False:
        assert "root:" not in result.get("content", "")


@pytest.mark.anyio
async def test_path_traversal_exact_protected_dir_blocked(protected_dirs):
    """Passing the exact protected directory path must be blocked."""
    from tools.files import read_file
    app_dir, _ = protected_dirs
    result = await read_file(app_dir)
    assert result["success"] is False
    assert "protected" in result["message"].lower()


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
