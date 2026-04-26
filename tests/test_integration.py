"""Integration tests: full request chain via httpx AsyncClient + ASGITransport.

Tests the complete auth -> middleware -> MCP tool -> audit pipeline.
Uses a patched session_manager to avoid needing a real MCP task group.
"""

import json
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from mymcp.auth import TokenStore
from mymcp.mcp_server import call_tool, list_tools


@pytest.fixture
def store(tmp_path):
    return TokenStore(str(tmp_path / "tokens.json"), "adm_testadmin")


@pytest.fixture
def app_with_store(store):
    """Create a FastAPI app with overridden store and a fake session_manager
    that properly processes MCP JSON-RPC requests."""
    from mymcp import auth

    original = auth._store
    auth._store = store

    from mymcp.server import create_app

    app = create_app()
    from starlette.responses import JSONResponse

    async def fake_handle_request(scope, receive, send):
        """Simulate MCP session_manager: parse JSON-RPC, dispatch to call_tool/list_tools."""
        from starlette.requests import Request

        request = Request(scope, receive, send)
        body = await request.body()
        try:
            rpc = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            resp = JSONResponse({"error": "Invalid JSON"}, status_code=400)
            await resp(scope, receive, send)
            return

        method = rpc.get("method", "")
        params = rpc.get("params", {})
        rpc_id = rpc.get("id", 1)

        if method == "tools/list":
            tools = await list_tools()
            result = {"tools": [{"name": t.name, "description": t.description} for t in tools]}
        elif method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            content_items = await call_tool(tool_name, arguments)
            result = {"content": [{"type": item.type, "text": item.text} for item in content_items]}
        else:
            result = {"error": f"Unknown method: {method}"}

        resp = JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "result": result,
            }
        )
        await resp(scope, receive, send)

    try:
        with patch("mymcp.server.session_manager") as mock_sm:
            mock_sm.handle_request = fake_handle_request
            yield app
    finally:
        auth._store = original


@pytest.fixture
async def client(app_with_store):
    transport = ASGITransport(app=app_with_store)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _make_mcp_request(tool_name: str, arguments: dict) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }


def _make_mcp_list_tools() -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {},
    }


def _mcp_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


async def _mcp_call(client, token: str, payload: dict) -> dict:
    resp = await client.post("/mcp", json=payload, headers=_mcp_headers(token))
    assert resp.status_code == 200
    return resp.json()


# ---------------------------------------------------------------------------
# Auth -> Tool -> Response chain
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
    """Full chain: rw token -> read_file -> correct content."""
    token = store.create_token("rw-client", role="rw")
    f = tmp_path / "integration_test.txt"
    f.write_text("integration test content\n")

    data = await _mcp_call(
        client,
        token,
        _make_mcp_request("read_file", {"file_path": str(f)}),
    )
    content_items = data["result"]["content"]
    result = json.loads(content_items[0]["text"])
    assert "integration test content" in result["content"]


@pytest.mark.anyio
async def test_ro_token_can_list_tools(client, store):
    """ro token should see only read tools."""
    token = store.create_token("ro-client", role="ro")
    data = await _mcp_call(client, token, _make_mcp_list_tools())
    tools = data["result"]["tools"]
    tool_names = {t["name"] for t in tools}
    assert "bash_execute" not in tool_names
    assert "write_file" not in tool_names
    assert "read_file" in tool_names


@pytest.mark.anyio
async def test_ro_token_denied_write_tool(client, store):
    """ro token calling write tool should get permission denied."""
    token = store.create_token("ro-client", role="ro")
    data = await _mcp_call(
        client,
        token,
        _make_mcp_request("bash_execute", {"command": "echo pwned"}),
    )
    content_items = data["result"]["content"]
    result = json.loads(content_items[0]["text"])
    assert result["success"] is False
    assert result["error"] == "PermissionDenied"


@pytest.mark.anyio
async def test_rw_token_write_and_read(client, store, tmp_path):
    """Full chain: write a file, then read it back."""
    token = store.create_token("rw-client", role="rw")
    target = str(tmp_path / "written_by_integration.txt")

    # Write
    write_data = await _mcp_call(
        client,
        token,
        _make_mcp_request(
            "write_file",
            {
                "file_path": target,
                "content": "hello from integration test",
            },
        ),
    )
    write_result = json.loads(write_data["result"]["content"][0]["text"])
    assert write_result["success"] is True

    # Read back
    read_data = await _mcp_call(
        client,
        token,
        _make_mcp_request("read_file", {"file_path": target}),
    )
    read_result = json.loads(read_data["result"]["content"][0]["text"])
    assert "hello from integration test" in read_result["content"]


@pytest.mark.anyio
async def test_rw_token_edit_file(client, store, tmp_path):
    """Full chain: write, edit, read back."""
    token = store.create_token("rw-client", role="rw")
    target = str(tmp_path / "editable.txt")

    # Write
    await _mcp_call(
        client,
        token,
        _make_mcp_request(
            "write_file",
            {
                "file_path": target,
                "content": "old_value in file",
            },
        ),
    )

    # Edit
    edit_data = await _mcp_call(
        client,
        token,
        _make_mcp_request(
            "edit_file",
            {
                "file_path": target,
                "old_string": "old_value",
                "new_string": "new_value",
            },
        ),
    )
    edit_result = json.loads(edit_data["result"]["content"][0]["text"])
    assert edit_result["success"] is True

    # Read back
    read_data = await _mcp_call(
        client,
        token,
        _make_mcp_request("read_file", {"file_path": target}),
    )
    read_result = json.loads(read_data["result"]["content"][0]["text"])
    assert "new_value" in read_result["content"]


@pytest.mark.anyio
async def test_rw_token_glob_and_grep(client, store, tmp_path):
    """Full chain: create files, glob, grep."""
    token = store.create_token("rw-client", role="rw")
    (tmp_path / "a.py").write_text("import os\n")
    (tmp_path / "b.txt").write_text("hello\n")

    # Glob
    glob_data = await _mcp_call(
        client,
        token,
        _make_mcp_request("glob", {"pattern": "*.py", "path": str(tmp_path)}),
    )
    glob_result = json.loads(glob_data["result"]["content"][0]["text"])
    assert any("a.py" in f for f in glob_result["files"])

    # Grep
    grep_data = await _mcp_call(
        client,
        token,
        _make_mcp_request("grep", {"pattern": "import", "path": str(tmp_path)}),
    )
    grep_result = json.loads(grep_data["result"]["content"][0]["text"])
    assert grep_result["match_count"] >= 1


# ---------------------------------------------------------------------------
# Admin API integration
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_admin_create_and_use_token(client, store):
    """Create a token via admin API, then use it for MCP."""
    resp = await client.post(
        "/admin/tokens",
        json={"name": "dynamic-client", "role": "ro"},
        headers={"Authorization": "Bearer adm_testadmin"},
    )
    assert resp.status_code == 200
    new_token = resp.json()["token"]

    # Use the new token on /mcp
    data = await _mcp_call(client, new_token, _make_mcp_list_tools())
    assert "result" in data
    assert "tools" in data["result"]


@pytest.mark.anyio
async def test_admin_revoke_and_reject(client, store):
    """Revoke a token, then verify it's rejected."""
    token = store.create_token("to-revoke", role="rw")

    # Verify it works first
    resp1 = await client.post(
        "/mcp",
        json=_make_mcp_list_tools(),
        headers=_mcp_headers(token),
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
