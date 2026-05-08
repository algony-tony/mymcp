import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("MYMCP_TOKEN_FILE", str(tmp_path / "tokens.json"))
    monkeypatch.setenv("MYMCP_AUDIT_LOG_DIR", str(tmp_path / "audit"))
    monkeypatch.setenv("MYMCP_METRICS_TOKEN", "test-mt")
    from mymcp.config import reset_settings_cache

    reset_settings_cache()
    from mymcp.server import create_app

    return TestClient(create_app())


def test_tokens_count_gauge_present(client):
    body = client.get("/metrics", headers={"Authorization": "Bearer test-mt"}).text
    assert "mymcp_tokens_count" in body


def test_bash_inflight_gauge_present(client):
    body = client.get("/metrics", headers={"Authorization": "Bearer test-mt"}).text
    assert "mymcp_bash_inflight_processes" in body
