import importlib

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from mymcp.transfer import get_ticket_store, reset_ticket_store
from mymcp.transfer.endpoints import register_transfer_routes


@pytest.fixture(autouse=True)
def _reset():
    reset_ticket_store()
    yield
    reset_ticket_store()


@pytest.fixture
def app():
    app = FastAPI()
    register_transfer_routes(app)
    return app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------- 4xx error matrix -------------------------------------------------


@pytest.mark.anyio
async def test_unknown_ticket_returns_404(client):
    r = await client.get("/files/raw/does-not-exist")
    assert r.status_code == 404
    body = r.json()
    assert body["ok"] is False
    assert body["error"] == "ticket_not_found"
    assert "hint" in body


@pytest.mark.anyio
async def test_get_on_upload_ticket_returns_405(client):
    t = get_ticket_store().mint(
        op="upload", path="/tmp/x", max_bytes=100, ttl_sec=60, created_by="t"
    )
    r = await client.get(f"/files/raw/{t.ticket_id}")
    assert r.status_code == 405
    assert r.json()["error"] == "wrong_method"


@pytest.mark.anyio
async def test_put_on_download_ticket_returns_405(client):
    t = get_ticket_store().mint(
        op="download", path="/tmp/x", max_bytes=0, ttl_sec=60, created_by="t"
    )
    r = await client.put(f"/files/raw/{t.ticket_id}", content=b"x")
    assert r.status_code == 405


@pytest.mark.anyio
async def test_consumed_ticket_returns_410(client, tmp_path):
    f = tmp_path / "f.bin"
    f.write_bytes(b"hi")
    t = get_ticket_store().mint(op="download", path=str(f), max_bytes=0, ttl_sec=60, created_by="t")
    get_ticket_store().consume(t.ticket_id)
    r = await client.get(f"/files/raw/{t.ticket_id}")
    assert r.status_code == 410


@pytest.mark.anyio
async def test_transfer_disabled_returns_404(client, monkeypatch):
    monkeypatch.setenv("MYMCP_TRANSFER_ENABLED", "false")
    import mymcp.config as cfg

    importlib.reload(cfg)
    import mymcp.transfer.endpoints as ep_mod

    importlib.reload(ep_mod)
    # Re-register routes on a fresh app since reload swapped the module.
    app2 = FastAPI()
    ep_mod.register_transfer_routes(app2)
    transport = ASGITransport(app=app2)
    try:
        t = get_ticket_store().mint(
            op="upload", path="/tmp/x", max_bytes=10, ttl_sec=60, created_by="t"
        )
        async with AsyncClient(transport=transport, base_url="http://t") as ac:
            r = await ac.put(f"/files/raw/{t.ticket_id}", content=b"x")
        assert r.status_code == 404
        assert r.json()["error"] == "transfer_disabled"
    finally:
        monkeypatch.delenv("MYMCP_TRANSFER_ENABLED", raising=False)
        importlib.reload(cfg)
        importlib.reload(ep_mod)


# ---------- upload -----------------------------------------------------------


@pytest.mark.anyio
async def test_upload_happy_path_atomic_write(client, tmp_path):
    dest = tmp_path / "uploaded.bin"
    payload = b"\x00\x01\x02hello-binary\xff" * 100
    t = get_ticket_store().mint(
        op="upload", path=str(dest), max_bytes=10_000, ttl_sec=60, created_by="t"
    )
    r = await client.put(f"/files/raw/{t.ticket_id}", content=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["bytes_written"] == len(payload)
    assert body["path"] == str(dest)
    assert dest.read_bytes() == payload
    # Ticket marked consumed
    assert get_ticket_store().consume(t.ticket_id) is False


@pytest.mark.anyio
async def test_upload_content_length_too_large_returns_413(client, tmp_path):
    dest = tmp_path / "x.bin"
    t = get_ticket_store().mint(
        op="upload", path=str(dest), max_bytes=10, ttl_sec=60, created_by="t"
    )
    payload = b"x" * 100
    r = await client.put(f"/files/raw/{t.ticket_id}", content=payload)
    assert r.status_code == 413
    assert r.json()["error"] == "size_exceeded"
    assert not dest.exists()


@pytest.mark.anyio
async def test_upload_streaming_truncation_returns_413(client, tmp_path):
    """Caller streams bytes that exceed cap mid-stream."""
    dest = tmp_path / "x.bin"
    t = get_ticket_store().mint(
        op="upload", path=str(dest), max_bytes=10, ttl_sec=60, created_by="t"
    )

    async def gen():
        yield b"x" * 5
        yield b"x" * 50

    headers = {"transfer-encoding": "chunked"}
    r = await client.put(f"/files/raw/{t.ticket_id}", content=gen(), headers=headers)
    assert r.status_code == 413
    assert not dest.exists()


@pytest.mark.anyio
async def test_upload_protected_path_at_redeem(client, tmp_path, monkeypatch):
    dest = tmp_path / "x.bin"
    t = get_ticket_store().mint(
        op="upload", path=str(dest), max_bytes=100, ttl_sec=60, created_by="t"
    )
    monkeypatch.setattr("mymcp.config.PROTECTED_PATHS", [str(tmp_path)])
    r = await client.put(f"/files/raw/{t.ticket_id}", content=b"x")
    assert r.status_code == 403
    assert r.json()["error"] == "path_protected"
    assert not dest.exists()


# ---------- download ---------------------------------------------------------


@pytest.mark.anyio
async def test_download_happy_path(client, tmp_path):
    src = tmp_path / "f.bin"
    payload = b"\x00\x01\x02" * 1000
    src.write_bytes(payload)
    t = get_ticket_store().mint(
        op="download", path=str(src), max_bytes=0, ttl_sec=60, created_by="t"
    )
    r = await client.get(f"/files/raw/{t.ticket_id}")
    assert r.status_code == 200
    assert r.content == payload
    assert r.headers["content-type"].startswith("application/octet-stream")
    assert "f.bin" in r.headers.get("content-disposition", "")
    # Consumed after success
    r2 = await client.get(f"/files/raw/{t.ticket_id}")
    assert r2.status_code in (404, 410)


@pytest.mark.anyio
async def test_download_missing_file_returns_404(client, tmp_path):
    src = tmp_path / "gone.bin"
    src.write_bytes(b"x")
    t = get_ticket_store().mint(
        op="download", path=str(src), max_bytes=0, ttl_sec=60, created_by="t"
    )
    src.unlink()
    r = await client.get(f"/files/raw/{t.ticket_id}")
    assert r.status_code == 404
    assert r.json()["error"] == "path_not_found"


@pytest.mark.anyio
async def test_download_protected_at_redeem(client, tmp_path, monkeypatch):
    src = tmp_path / "f.bin"
    src.write_bytes(b"x")
    t = get_ticket_store().mint(
        op="download", path=str(src), max_bytes=0, ttl_sec=60, created_by="t"
    )
    monkeypatch.setattr("mymcp.config.PROTECTED_PATHS", [str(tmp_path)])
    r = await client.get(f"/files/raw/{t.ticket_id}")
    assert r.status_code == 403


# ---------- audit -----------------------------------------------------------


def _reset_audit_module():
    from mymcp import audit

    audit._setup_done = False
    audit._logger = None


@pytest.mark.anyio
async def test_upload_writes_audit_entry(client, tmp_path, monkeypatch):
    audit_dir = tmp_path / "audit"
    monkeypatch.setenv("MYMCP_AUDIT_ENABLED", "true")
    monkeypatch.setenv("MYMCP_AUDIT_LOG_DIR", str(audit_dir))
    import mymcp.config as cfg

    importlib.reload(cfg)
    _reset_audit_module()
    try:
        dest = tmp_path / "f.bin"
        t = get_ticket_store().mint(
            op="upload", path=str(dest), max_bytes=100, ttl_sec=60, created_by="rwc"
        )
        r = await client.put(f"/files/raw/{t.ticket_id}", content=b"hello")
        assert r.status_code == 200
        log_path = audit_dir / "audit.log"
        assert log_path.exists()
        text = log_path.read_text()
        assert "transfer_redeem" in text
        assert "rwc" in text
        assert '"bytes": 5' in text
    finally:
        monkeypatch.delenv("MYMCP_AUDIT_ENABLED", raising=False)
        monkeypatch.delenv("MYMCP_AUDIT_LOG_DIR", raising=False)
        importlib.reload(cfg)
        _reset_audit_module()


@pytest.mark.anyio
async def test_failed_upload_writes_error_audit(client, tmp_path, monkeypatch):
    audit_dir = tmp_path / "audit"
    monkeypatch.setenv("MYMCP_AUDIT_ENABLED", "true")
    monkeypatch.setenv("MYMCP_AUDIT_LOG_DIR", str(audit_dir))
    import mymcp.config as cfg

    importlib.reload(cfg)
    _reset_audit_module()
    try:
        dest = tmp_path / "f.bin"
        t = get_ticket_store().mint(
            op="upload", path=str(dest), max_bytes=2, ttl_sec=60, created_by="t"
        )
        r = await client.put(f"/files/raw/{t.ticket_id}", content=b"too-big")
        assert r.status_code == 413
        text = (audit_dir / "audit.log").read_text()
        assert "size_exceeded" in text
    finally:
        monkeypatch.delenv("MYMCP_AUDIT_ENABLED", raising=False)
        monkeypatch.delenv("MYMCP_AUDIT_LOG_DIR", raising=False)
        importlib.reload(cfg)
        _reset_audit_module()


@pytest.mark.anyio
async def test_download_writes_audit_entry(client, tmp_path, monkeypatch):
    audit_dir = tmp_path / "audit"
    monkeypatch.setenv("MYMCP_AUDIT_ENABLED", "true")
    monkeypatch.setenv("MYMCP_AUDIT_LOG_DIR", str(audit_dir))
    import mymcp.config as cfg

    importlib.reload(cfg)
    _reset_audit_module()
    try:
        src = tmp_path / "f.bin"
        src.write_bytes(b"abcd")
        t = get_ticket_store().mint(
            op="download", path=str(src), max_bytes=0, ttl_sec=60, created_by="roc"
        )
        r = await client.get(f"/files/raw/{t.ticket_id}")
        assert r.status_code == 200
        assert r.content == b"abcd"
        text = (audit_dir / "audit.log").read_text()
        assert "transfer_redeem" in text
        assert "roc" in text
        assert '"bytes": 4' in text
    finally:
        monkeypatch.delenv("MYMCP_AUDIT_ENABLED", raising=False)
        monkeypatch.delenv("MYMCP_AUDIT_LOG_DIR", raising=False)
        importlib.reload(cfg)
        _reset_audit_module()
