import importlib
from unittest.mock import patch

import pytest

from mymcp.tools.transfer import prepare_download, prepare_upload
from mymcp.transfer import reset_ticket_store


@pytest.fixture(autouse=True)
def _reset():
    reset_ticket_store()
    yield
    reset_ticket_store()


# ---------- prepare_upload ---------------------------------------------------


@pytest.mark.anyio
async def test_prepare_upload_returns_url_and_ticket(tmp_path, monkeypatch):
    monkeypatch.setattr("mymcp.tools.transfer._public_base_url", lambda: "https://srv.example.com")
    dest = str(tmp_path / "foo.bin")
    result = await prepare_upload(
        dest_path=dest,
        max_bytes=1024,
        expires_in=120,
        token_name="rw-client",
    )
    assert result["success"] is True
    assert result["method"] == "PUT"
    assert result["url"].startswith("https://srv.example.com/files/raw/")
    assert result["dest_path"] == dest
    assert result["max_bytes"] == 1024
    assert result["expires_in"] == 120
    assert "expires_at" in result
    assert "curl_example" in result
    assert "instructions" in result
    assert "ticket" in result
    assert result["url"].endswith(result["ticket"])


@pytest.mark.anyio
async def test_prepare_upload_rejects_relative_path():
    r = await prepare_upload(dest_path="relative/path", token_name="t")
    assert r["success"] is False
    assert r["error"] == "InvalidPath"


@pytest.mark.anyio
async def test_prepare_upload_rejects_protected_path():
    with patch("mymcp.config.get_protected_paths", return_value=["/etc"]):
        r = await prepare_upload(dest_path="/etc/passwd", token_name="t")
    assert r["success"] is False
    assert r["error"] == "ProtectedPath"


@pytest.mark.anyio
async def test_prepare_upload_disabled(monkeypatch):
    monkeypatch.setenv("MYMCP_TRANSFER_ENABLED", "false")
    import mymcp.config as cfg

    importlib.reload(cfg)
    import mymcp.tools.transfer as transfer_mod

    importlib.reload(transfer_mod)
    try:
        r = await transfer_mod.prepare_upload(dest_path="/tmp/x", token_name="t")
        assert r["success"] is False
        assert r["error"] == "TransferDisabled"
    finally:
        monkeypatch.delenv("MYMCP_TRANSFER_ENABLED", raising=False)
        importlib.reload(cfg)
        importlib.reload(transfer_mod)


@pytest.mark.anyio
async def test_prepare_upload_clamps_max_bytes_and_ttl(tmp_path, monkeypatch):
    monkeypatch.setenv("MYMCP_TRANSFER_MAX_BYTES", "1024")
    monkeypatch.setenv("MYMCP_TRANSFER_MAX_TTL_SEC", "60")
    import mymcp.config as cfg

    importlib.reload(cfg)
    import mymcp.tools.transfer as transfer_mod

    importlib.reload(transfer_mod)
    try:
        r = await transfer_mod.prepare_upload(
            dest_path=str(tmp_path / "f"),
            max_bytes=10**9,
            expires_in=10**6,
            token_name="t",
        )
        assert r["success"] is True
        assert r["max_bytes"] == 1024
        assert r["expires_in"] == 60
    finally:
        monkeypatch.delenv("MYMCP_TRANSFER_MAX_BYTES", raising=False)
        monkeypatch.delenv("MYMCP_TRANSFER_MAX_TTL_SEC", raising=False)
        importlib.reload(cfg)
        importlib.reload(transfer_mod)


@pytest.mark.anyio
async def test_prepare_upload_overwrite_false_rejects_existing(tmp_path):
    p = tmp_path / "exists.bin"
    p.write_bytes(b"x")
    r = await prepare_upload(dest_path=str(p), overwrite=False, token_name="t")
    assert r["success"] is False
    assert r["error"] == "FileExists"


@pytest.mark.anyio
async def test_prepare_upload_invalid_max_bytes(tmp_path):
    r = await prepare_upload(dest_path=str(tmp_path / "f"), max_bytes=0, token_name="t")
    assert r["success"] is False
    assert r["error"] == "InvalidMaxBytes"


@pytest.mark.anyio
async def test_prepare_upload_relative_url_when_no_public_base(tmp_path, monkeypatch):
    monkeypatch.setenv("MYMCP_PUBLIC_BASE_URL", "")
    import mymcp.config as cfg

    importlib.reload(cfg)
    import mymcp.tools.transfer as transfer_mod

    importlib.reload(transfer_mod)
    try:
        r = await transfer_mod.prepare_upload(
            dest_path=str(tmp_path / "x"), max_bytes=10, token_name="t"
        )
        assert r["url"].startswith("/files/raw/")
        assert r["curl_example"].startswith("curl -fsS -T ")
    finally:
        monkeypatch.delenv("MYMCP_PUBLIC_BASE_URL", raising=False)
        importlib.reload(cfg)
        importlib.reload(transfer_mod)


# ---------- prepare_download -------------------------------------------------


@pytest.mark.anyio
async def test_prepare_download_returns_url_and_size(tmp_path, monkeypatch):
    monkeypatch.setattr("mymcp.tools.transfer._public_base_url", lambda: "https://srv.example.com")
    src = tmp_path / "thing.bin"
    src.write_bytes(b"hello-bytes")
    r = await prepare_download(src_path=str(src), expires_in=60, token_name="ro")
    assert r["success"] is True
    assert r["method"] == "GET"
    assert r["src_path"] == str(src)
    assert r["size"] == len(b"hello-bytes")
    assert r["url"].startswith("https://srv.example.com/files/raw/")
    assert "curl_example" in r and "-o " in r["curl_example"]


@pytest.mark.anyio
async def test_prepare_download_missing_file(tmp_path):
    r = await prepare_download(src_path=str(tmp_path / "nope"), token_name="t")
    assert r["success"] is False
    assert r["error"] == "FileNotFound"


@pytest.mark.anyio
async def test_prepare_download_directory_rejected(tmp_path):
    r = await prepare_download(src_path=str(tmp_path), token_name="t")
    assert r["success"] is False
    assert r["error"] == "NotARegularFile"


@pytest.mark.anyio
async def test_prepare_download_protected_path():
    with patch("mymcp.config.get_protected_paths", return_value=["/etc"]):
        r = await prepare_download(src_path="/etc/shadow", token_name="t")
    assert r["success"] is False
    assert r["error"] == "ProtectedPath"


# ---------- coverage gaps ---------------------------------------------------


@pytest.mark.anyio
async def test_prepare_upload_invalid_expires_in(tmp_path):
    r = await prepare_upload(dest_path=str(tmp_path / "f"), expires_in=0, token_name="t")
    assert r["success"] is False
    assert r["error"] == "InvalidExpiresIn"


@pytest.mark.anyio
async def test_prepare_download_invalid_expires_in(tmp_path):
    p = tmp_path / "f"
    p.write_bytes(b"x")
    r = await prepare_download(src_path=str(p), expires_in=-1, token_name="t")
    assert r["success"] is False
    assert r["error"] == "InvalidExpiresIn"


@pytest.mark.anyio
async def test_prepare_download_relative_path():
    r = await prepare_download(src_path="rel/path", token_name="t")
    assert r["success"] is False
    assert r["error"] == "InvalidPath"


@pytest.mark.anyio
async def test_prepare_download_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("MYMCP_TRANSFER_ENABLED", "false")
    import mymcp.config as cfg

    importlib.reload(cfg)
    import mymcp.tools.transfer as transfer_mod

    importlib.reload(transfer_mod)
    try:
        p = tmp_path / "f"
        p.write_bytes(b"x")
        r = await transfer_mod.prepare_download(src_path=str(p), token_name="t")
        assert r["success"] is False
        assert r["error"] == "TransferDisabled"
    finally:
        monkeypatch.delenv("MYMCP_TRANSFER_ENABLED", raising=False)
        importlib.reload(cfg)
        importlib.reload(transfer_mod)
