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
    import auth
    original = auth._store
    auth._store = store
    try:
        scope = {"type": "http", "method": "GET", "headers": [], "query_string": b""}
        request = Request(scope)
        error, info = _validate_token(request)
        assert error is not None
        assert info is None
    finally:
        auth._store = original


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
async def test_middleware_valid_token_forwards_to_mcp(store):
    """Valid token on /mcp should be forwarded to session_manager, not rejected."""
    from main import app
    import auth

    token = store.create_token("mcp-client", role="rw")
    original = auth._store
    auth._store = store

    forwarded = False

    async def fake_handle_request(scope, receive, send):
        nonlocal forwarded
        forwarded = True
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
                    content=b"{}",
                )
        assert resp.status_code == 200
        assert forwarded is True
    finally:
        auth._store = original


@pytest.mark.anyio
async def test_middleware_sets_contextvar(store):
    """Verify the middleware sets _current_audit_info contextvar."""
    from main import app
    from mcp_server import _current_audit_info
    import auth

    token = store.create_token("ctx-client", role="ro")
    original = auth._store
    auth._store = store

    captured = {}

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
