"""End-to-end: mint via prepare_upload, PUT bytes, file appears on disk;
mint via prepare_download, GET, bytes match."""

import importlib
import os

import pytest
from httpx import ASGITransport, AsyncClient

from mymcp.transfer import reset_ticket_store


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch, tmp_path):
    reset_ticket_store()
    monkeypatch.setenv("MYMCP_PUBLIC_BASE_URL", "")
    monkeypatch.setenv("MYMCP_TOKEN_FILE", str(tmp_path / "tokens.json"))
    monkeypatch.setenv("MYMCP_ADMIN_TOKEN", "adm-test")
    monkeypatch.setenv("MYMCP_AUDIT_LOG_DIR", str(tmp_path / "audit"))
    (tmp_path / "audit").mkdir()
    import mymcp.config as cfg

    importlib.reload(cfg)
    yield
    reset_ticket_store()
    importlib.reload(cfg)


@pytest.fixture
async def client():
    from mymcp.server import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        yield ac


@pytest.mark.anyio
async def test_full_upload_and_download_roundtrip(client, tmp_path):
    from mymcp.tools.transfer import prepare_download, prepare_upload

    dest = tmp_path / "round.bin"
    payload = os.urandom(50_000)

    upload_info = await prepare_upload(
        dest_path=str(dest),
        max_bytes=100_000,
        token_name="rwc",
    )
    assert upload_info["success"] is True
    r = await client.put(upload_info["url"], content=payload)
    assert r.status_code == 200, r.text
    assert dest.read_bytes() == payload

    dl_info = await prepare_download(src_path=str(dest), token_name="rwc")
    assert dl_info["success"] is True
    r = await client.get(dl_info["url"])
    assert r.status_code == 200
    assert r.content == payload


@pytest.mark.anyio
async def test_upload_url_is_single_use(client, tmp_path):
    from mymcp.tools.transfer import prepare_upload

    dest = tmp_path / "single.bin"
    info = await prepare_upload(dest_path=str(dest), max_bytes=10, token_name="t")
    r1 = await client.put(info["url"], content=b"hi")
    assert r1.status_code == 200
    r2 = await client.put(info["url"], content=b"hi")
    assert r2.status_code in (404, 410)
