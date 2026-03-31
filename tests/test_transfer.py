import pytest
from httpx import AsyncClient, ASGITransport
from fastapi import FastAPI
from auth import TokenStore, get_store
from tools.transfer import transfer_router


@pytest.fixture
def store(tmp_path):
    return TokenStore(str(tmp_path / "tokens.json"), "adm_testadmin")


@pytest.fixture
async def client(store):
    app = FastAPI()
    app.include_router(transfer_router)
    app.dependency_overrides[get_store] = lambda: store
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
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
        "/files/download",
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
