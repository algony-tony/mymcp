import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch
from auth import TokenStore
import config


# ---------------------------------------------------------------------------
# config._read_version() unit tests
# ---------------------------------------------------------------------------

def test_read_version_app_dir_takes_priority(tmp_path):
    app_version_file = tmp_path / "VERSION"
    app_version_file.write_text("2.0.0\n")
    repo_version_file = tmp_path / "repo_VERSION"
    repo_version_file.write_text("1.0.0\n")
    with patch("config.APP_DIR", str(tmp_path)), \
         patch("config._VERSION_FILE", str(repo_version_file)):
        result = config._read_version()
    assert result == "2.0.0"


def test_read_version_falls_back_to_repo(tmp_path):
    repo_version_file = tmp_path / "VERSION"
    repo_version_file.write_text("1.1.0\n")
    missing_app_dir = str(tmp_path / "nonexistent")
    with patch("config.APP_DIR", missing_app_dir), \
         patch("config._VERSION_FILE", str(repo_version_file)):
        result = config._read_version()
    assert result == "1.1.0"


def test_read_version_falls_back_to_unknown(tmp_path):
    missing_app_dir = str(tmp_path / "nonexistent")
    missing_repo = str(tmp_path / "noVERSION")
    with patch("config.APP_DIR", missing_app_dir), \
         patch("config._VERSION_FILE", missing_repo):
        result = config._read_version()
    assert result == "unknown"


def test_read_version_strips_whitespace(tmp_path):
    app_version_file = tmp_path / "VERSION"
    app_version_file.write_text("  1.2.3  \n")
    with patch("config.APP_DIR", str(tmp_path)), \
         patch("config._VERSION_FILE", str(tmp_path / "nofile")):
        result = config._read_version()
    assert result == "1.2.3"


def test_app_version_is_set():
    assert isinstance(config.APP_VERSION, str)
    assert config.APP_VERSION not in ("", "unknown")


# ---------------------------------------------------------------------------
# /version and /health endpoint tests
# ---------------------------------------------------------------------------

@pytest.fixture
def store_v(tmp_path):
    return TokenStore(str(tmp_path / "tokens.json"), "adm_testadmin")


@pytest.fixture
def versioned_app(store_v):
    import auth
    original = auth._store
    auth._store = store_v
    try:
        from main import app
        yield app
    finally:
        auth._store = original


@pytest.mark.anyio
async def test_get_version_returns_200(versioned_app):
    transport = ASGITransport(app=versioned_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("config.APP_VERSION", "1.2.3"):
            resp = await client.get("/version")
    assert resp.status_code == 200
    assert resp.json() == {"version": "1.2.3"}


@pytest.mark.anyio
async def test_get_version_no_auth_required(versioned_app):
    """Endpoint is accessible without an Authorization header."""
    transport = ASGITransport(app=versioned_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/version")
    assert resp.status_code == 200
    assert resp.json() == {"version": config.APP_VERSION}


@pytest.mark.anyio
async def test_health_includes_version(versioned_app):
    transport = ASGITransport(app=versioned_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("config.APP_VERSION", "2.0.0"):
            resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"] == "2.0.0"
