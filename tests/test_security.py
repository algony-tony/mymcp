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
