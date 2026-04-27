import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from mymcp.auth import TokenStore, admin_router, get_store


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


# ---------------------------------------------------------------------------
# require_auth dependency
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_require_auth_missing_bearer(store):
    """require_auth should reject requests without Bearer prefix."""
    from fastapi import Depends, FastAPI

    from mymcp.auth import get_store, require_auth

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
async def test_require_auth_invalid_token(store):
    from fastapi import Depends, FastAPI

    from mymcp.auth import get_store, require_auth

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
async def test_require_auth_valid_token(store):
    from fastapi import Depends, FastAPI

    from mymcp.auth import get_store, require_auth

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
