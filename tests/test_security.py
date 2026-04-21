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
