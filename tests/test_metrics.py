import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("MYMCP_TOKEN_FILE", str(tmp_path / "tokens.json"))
    monkeypatch.setenv("MYMCP_AUDIT_LOG_DIR", str(tmp_path / "audit"))
    monkeypatch.setenv("MYMCP_METRICS_TOKEN", "test-metrics-token")
    from mymcp.config import reset_settings_cache

    reset_settings_cache()
    from mymcp.server import create_app

    return TestClient(create_app())


def test_metrics_endpoint_requires_token(client):
    r = client.get("/metrics")
    assert r.status_code == 401


def test_metrics_endpoint_returns_prom_format(client):
    r = client.get("/metrics", headers={"Authorization": "Bearer test-metrics-token"})
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]


def test_http_requests_counter_increments(client):
    headers = {"Authorization": "Bearer test-metrics-token"}
    client.get("/health")
    client.get("/health")
    body = client.get("/metrics", headers=headers).text
    assert "mymcp_http_requests" in body
    assert 'path="/health"' in body
