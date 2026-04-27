from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from mymcp.auth import TokenStore


def test_version_attribute_is_set():
    """mymcp.__version__ is populated either from installed metadata or _version.py."""
    import mymcp

    assert isinstance(mymcp.__version__, str)
    assert mymcp.__version__ != ""


# ---------------------------------------------------------------------------
# /version and /health endpoint tests
# ---------------------------------------------------------------------------


@pytest.fixture
def store_v(tmp_path):
    return TokenStore(str(tmp_path / "tokens.json"), "adm_testadmin")


@pytest.fixture
def versioned_app(store_v):
    from mymcp import auth

    original = auth._store
    auth._store = store_v
    try:
        from mymcp.server import create_app

        app = create_app()
        yield app
    finally:
        auth._store = original


@pytest.mark.anyio
async def test_get_version_returns_200(versioned_app):
    transport = ASGITransport(app=versioned_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("mymcp.__version__", "1.2.3"):
            resp = await client.get("/version")
    assert resp.status_code == 200
    assert resp.json() == {"version": "1.2.3"}


@pytest.mark.anyio
async def test_get_version_no_auth_required(versioned_app):
    """Endpoint is accessible without an Authorization header."""
    import mymcp

    transport = ASGITransport(app=versioned_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/version")
    assert resp.status_code == 200
    assert resp.json() == {"version": mymcp.__version__}


@pytest.mark.anyio
async def test_health_includes_version(versioned_app):
    transport = ASGITransport(app=versioned_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("mymcp.__version__", "2.0.0"):
            resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"] == "2.0.0"
