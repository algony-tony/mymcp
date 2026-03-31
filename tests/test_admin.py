import pytest
from httpx import AsyncClient, ASGITransport
from fastapi import FastAPI
from auth import TokenStore, get_store, admin_router


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
