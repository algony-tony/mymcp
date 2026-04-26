import sys
import importlib
import pytest


def reload_metrics():
    from mymcp import metrics as _m
    try:
        from prometheus_client import REGISTRY
        for collector in list(REGISTRY._collector_to_names.keys()):
            collector_name = getattr(collector, '_name', None)
            if collector_name and collector_name.startswith('mymcp_'):
                try:
                    REGISTRY.unregister(collector)
                except Exception:
                    pass
    except (ImportError, AttributeError):
        pass
    importlib.reload(_m)
    return _m


def test_metrics_enabled_when_prometheus_installed():
    m = reload_metrics()
    if not m.ENABLED:
        pytest.skip("prometheus_client not installed")
    assert m.TOOL_CALLS is not None
    assert m.TOOL_DURATION is not None
    assert m.HTTP_REQUESTS is not None


def test_metrics_disabled_when_prometheus_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "prometheus_client", None)
    m = reload_metrics()
    assert m.ENABLED is False
    assert m.TOOL_CALLS is None
    assert m.TOOL_DURATION is None
    assert m.HTTP_REQUESTS is None


def test_tool_calls_has_correct_labels():
    m = reload_metrics()
    if not m.ENABLED:
        pytest.skip("prometheus_client not installed")
    m.TOOL_CALLS.labels(tool="bash_execute", role="rw", result="ok")


def test_tool_duration_has_custom_buckets():
    m = reload_metrics()
    if not m.ENABLED:
        pytest.skip("prometheus_client not installed")
    m.TOOL_DURATION.labels(tool="test_tool").observe(0.005)
    samples = m.TOOL_DURATION.collect()[0].samples
    bucket_bounds = {float(s.labels["le"]) for s in samples if s.name.endswith("_bucket")}
    assert 0.01 in bucket_bounds
    assert 30.0 in bucket_bounds


def test_metrics_token_defaults_to_empty():
    import os
    import importlib
    from mymcp import config
    os.environ.pop("MYMCP_METRICS_TOKEN", None)
    importlib.reload(config)
    assert config.METRICS_TOKEN == ""


@pytest.mark.anyio
async def test_call_tool_increments_tool_calls_counter():
    from mymcp import metrics
    if not metrics.ENABLED:
        pytest.skip("prometheus_client not installed")
    from prometheus_client import Counter, Histogram, CollectorRegistry
    from unittest.mock import patch
    from mymcp import mcp_server

    registry = CollectorRegistry()
    fresh_calls = Counter(
        "test_t6_calls_total", "test", ["tool", "role", "result"],
        registry=registry,
    )
    fresh_duration = Histogram(
        "test_t6_duration_seconds", "test", ["tool"],
        buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 30.0],
        registry=registry,
    )

    with patch.object(mcp_server, "metrics") as mock_metrics, \
         patch.object(mcp_server, "_current_audit_info") as mock_cv:
        mock_metrics.ENABLED = True
        mock_metrics.TOOL_CALLS = fresh_calls
        mock_metrics.TOOL_DURATION = fresh_duration
        mock_cv.get.return_value = {"token_name": "t1", "role": "ro", "ip": "127.0.0.1"}
        await mcp_server.call_tool("read_file", {"file_path": "/etc/hostname"})

    samples = list(fresh_calls.collect()[0].samples)
    # Filter for the actual counter metric (not the _created timestamp)
    total = sum(s.value for s in samples if s.labels.get("tool") == "read_file" and s.name == "test_t6_calls_total")
    assert total == 1.0


@pytest.mark.anyio
async def test_call_tool_no_error_when_metrics_disabled():
    from unittest.mock import patch
    with patch("mymcp.metrics.ENABLED", False):
        with patch("mymcp.mcp_server._current_audit_info") as mock_cv:
            mock_cv.get.return_value = {"token_name": "t1", "role": "ro", "ip": "127.0.0.1"}
            from mymcp.mcp_server import call_tool
            result = await call_tool("read_file", {"file_path": "/etc/hostname"})
    assert result is not None


from httpx import AsyncClient, ASGITransport
from mymcp.auth import TokenStore
from unittest.mock import patch


@pytest.fixture
def metrics_store(tmp_path):
    return TokenStore(str(tmp_path / "tokens.json"), "adm_testadmin")


@pytest.fixture
def metrics_app(metrics_store):
    from mymcp import auth
    original = auth._store
    auth._store = metrics_store
    try:
        from mymcp.server import create_app; app = create_app()
        yield app
    finally:
        auth._store = original


@pytest.mark.anyio
async def test_metrics_disabled_without_prometheus(metrics_app):
    with patch("mymcp.metrics.ENABLED", False):
        transport = ASGITransport(app=metrics_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/metrics")
    assert resp.status_code == 503
    assert "prometheus_client" in resp.json()["detail"]


@pytest.mark.anyio
async def test_metrics_disabled_without_token(metrics_app):
    from unittest.mock import MagicMock
    with patch("mymcp.metrics.ENABLED", True), \
         patch("mymcp.metrics.HTTP_REQUESTS", MagicMock()), \
         patch("mymcp.config.METRICS_TOKEN", ""):
        transport = ASGITransport(app=metrics_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/metrics")
    assert resp.status_code == 503
    assert "MYMCP_METRICS_TOKEN" in resp.json()["detail"]


@pytest.mark.anyio
async def test_metrics_unauthorized_with_wrong_token(metrics_app):
    from unittest.mock import MagicMock
    with patch("mymcp.metrics.ENABLED", True), \
         patch("mymcp.metrics.HTTP_REQUESTS", MagicMock()), \
         patch("mymcp.config.METRICS_TOKEN", "secret123"):
        transport = ASGITransport(app=metrics_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/metrics", headers={"Authorization": "Bearer wrongtoken"}
            )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_metrics_returns_prometheus_text_with_valid_token(metrics_app):
    from mymcp import metrics as m
    if not m.ENABLED:
        pytest.skip("prometheus_client not installed")
    with patch("mymcp.config.METRICS_TOKEN", "secret123"):
        transport = ASGITransport(app=metrics_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/metrics", headers={"Authorization": "Bearer secret123"}
            )
    assert resp.status_code == 200
    assert "mymcp_" in resp.text or "# HELP" in resp.text


@pytest.mark.anyio
async def test_metrics_does_not_break_health(metrics_app):
    transport = ASGITransport(app=metrics_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
