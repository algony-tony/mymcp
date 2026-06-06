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


def test_http_path_label_uses_route_template_for_dynamic_routes(monkeypatch, tmp_path):
    """Dynamic-path routes must group under one templated label value.

    Without this, hitting /synthetic/1, /synthetic/2, … N would create N
    distinct label values — Prometheus cardinality explosion on any route
    with an id segment.
    """
    monkeypatch.setenv("MYMCP_TOKEN_FILE", str(tmp_path / "tokens.json"))
    monkeypatch.setenv("MYMCP_AUDIT_LOG_DIR", str(tmp_path / "audit"))
    monkeypatch.setenv("MYMCP_METRICS_TOKEN", "test-metrics-token")
    from mymcp.config import reset_settings_cache

    reset_settings_cache()
    from mymcp.server import create_app

    app = create_app()

    @app.get("/synthetic/{item_id}")
    async def _handler(item_id: str):
        return {"ok": True, "item_id": item_id}

    client = TestClient(app)
    for i in range(5):
        client.get(f"/synthetic/{i}")

    body = client.get("/metrics", headers={"Authorization": "Bearer test-metrics-token"}).text
    # Templated form is present…
    assert 'path="/synthetic/{item_id}"' in body
    # …and none of the concrete IDs leaked through as label values.
    for i in range(5):
        assert f'path="/synthetic/{i}"' not in body
