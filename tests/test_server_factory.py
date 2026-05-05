"""Server module must expose a side-effect-free `create_app()` factory."""

import importlib

import pytest


def test_create_app_returns_fastapi_instance(monkeypatch, tmp_path):
    monkeypatch.setenv("MYMCP_ADMIN_TOKEN", "tok_test")
    monkeypatch.setenv("MYMCP_TOKEN_FILE", str(tmp_path / "tokens.json"))
    monkeypatch.setenv("MYMCP_AUDIT_LOG_DIR", str(tmp_path / "audit"))
    (tmp_path / "audit").mkdir()
    monkeypatch.delenv("MYMCP_ENV_FILE", raising=False)

    from mymcp import config

    config.reset_settings_cache()

    from mymcp.server import create_app

    app = create_app()

    from fastapi import FastAPI

    assert isinstance(app, FastAPI)


def test_importing_server_does_not_configure_logging():
    """Importing mymcp.server must not call logging.basicConfig()."""
    import logging

    root = logging.getLogger()
    pre_handlers = list(root.handlers)
    pre_level = root.level

    import importlib

    import mymcp.server

    importlib.reload(mymcp.server)

    assert list(root.handlers) == pre_handlers
    assert root.level == pre_level


@pytest.mark.anyio
async def test_files_raw_route_does_not_require_bearer_token(tmp_path, monkeypatch):
    """The bypass endpoint authenticates by ticket; no Bearer header should be needed."""
    monkeypatch.setenv("MYMCP_TOKEN_FILE", str(tmp_path / "tokens.json"))
    monkeypatch.setenv("MYMCP_ADMIN_TOKEN", "adm-test")
    import mymcp.config as cfg

    importlib.reload(cfg)

    from httpx import ASGITransport, AsyncClient

    from mymcp.server import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.get("/files/raw/nonexistent-ticket")
    # 404 from ticket lookup, NOT 401 from auth middleware
    assert r.status_code == 404
    body = r.json()
    assert body.get("error") == "ticket_not_found"


@pytest.mark.anyio
async def test_mcp_route_still_requires_bearer_token(tmp_path, monkeypatch):
    monkeypatch.setenv("MYMCP_TOKEN_FILE", str(tmp_path / "tokens.json"))
    monkeypatch.setenv("MYMCP_ADMIN_TOKEN", "adm-test")
    import mymcp.config as cfg

    importlib.reload(cfg)

    from httpx import ASGITransport, AsyncClient

    from mymcp.server import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/mcp", json={})
    assert r.status_code == 401
