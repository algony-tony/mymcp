# Prometheus Monitoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an optional Prometheus `/metrics` endpoint with tool call counters, tool latency histograms, and HTTP request counters, protected by `MCP_METRICS_TOKEN`.

**Architecture:** `metrics.py` defines metric objects via `try/except ImportError` — if `prometheus_client` is not installed, `ENABLED=False` and all metrics are `None`. `mcp_server.py` records tool metrics in `call_tool()`. `main.py` adds `MetricsMiddleware` for HTTP metrics and a `/metrics` endpoint. Zero overhead when `prometheus_client` is absent or `MCP_METRICS_TOKEN` is unset.

**Tech Stack:** Python, FastAPI, Starlette ASGI middleware, `prometheus_client` (optional, not in requirements.txt)

---

### Task 1: Create `metrics.py`

**Files:**
- Create: `metrics.py`
- Test: `tests/test_metrics.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_metrics.py`:

```python
import sys
import importlib
import pytest


def reload_metrics():
    if "metrics" in sys.modules:
        del sys.modules["metrics"]
    return importlib.import_module("metrics")


def test_metrics_enabled_when_prometheus_installed():
    """prometheus_client is installed in test env (pip install prometheus_client)."""
    m = reload_metrics()
    assert m.ENABLED is True
    assert m.TOOL_CALLS is not None
    assert m.TOOL_DURATION is not None
    assert m.HTTP_REQUESTS is not None


def test_metrics_disabled_when_prometheus_missing(monkeypatch):
    """Simulate prometheus_client not installed."""
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
    # labels() call should not raise
    m.TOOL_CALLS.labels(tool="bash_execute", role="rw", result="ok")


def test_tool_duration_has_custom_buckets():
    m = reload_metrics()
    if not m.ENABLED:
        pytest.skip("prometheus_client not installed")
    # Verify histogram buckets include 0.01 and 30.0
    buckets = [b for b in m.TOOL_DURATION._kwargs.get("buckets", [])]
    assert 0.01 in buckets
    assert 30.0 in buckets
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_metrics.py -v --benchmark-disable
```

Expected: `ModuleNotFoundError: No module named 'metrics'`

- [ ] **Step 3: Install prometheus_client for development**

```bash
pip install prometheus_client
```

- [ ] **Step 4: Create `metrics.py`**

```python
try:
    from prometheus_client import Counter, Histogram
    ENABLED = True
    TOOL_CALLS = Counter(
        "mymcp_tool_calls_total",
        "Total MCP tool calls",
        ["tool", "role", "result"],
    )
    TOOL_DURATION = Histogram(
        "mymcp_tool_duration_seconds",
        "MCP tool call duration",
        ["tool"],
        buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 30.0],
    )
    HTTP_REQUESTS = Counter(
        "mymcp_http_requests_total",
        "Total HTTP requests",
        ["path", "method", "status"],
    )
except ImportError:
    ENABLED = False
    TOOL_CALLS = TOOL_DURATION = HTTP_REQUESTS = None
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_metrics.py -v --benchmark-disable
```

Expected: all 4 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add metrics.py tests/test_metrics.py
git commit -m "feat(metrics): add metrics.py with optional prometheus_client"
```

---

### Task 2: Add `METRICS_TOKEN` to `config.py`

**Files:**
- Modify: `config.py`
- Test: `tests/test_metrics.py` (add case)

- [ ] **Step 1: Write failing test**

Append to `tests/test_metrics.py`:

```python
def test_metrics_token_defaults_to_empty():
    import config
    assert hasattr(config, "METRICS_TOKEN")
    # Default is empty string (feature disabled)
    import os
    os.environ.pop("MCP_METRICS_TOKEN", None)
    import importlib
    importlib.reload(config)
    assert config.METRICS_TOKEN == ""
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_metrics.py::test_metrics_token_defaults_to_empty -v --benchmark-disable
```

Expected: `AttributeError: module 'config' has no attribute 'METRICS_TOKEN'`

- [ ] **Step 3: Add `METRICS_TOKEN` to `config.py`**

Add at the end of `config.py`:

```python
# Metrics endpoint access token (empty = endpoint disabled)
METRICS_TOKEN: str = os.getenv("MCP_METRICS_TOKEN", "")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_metrics.py -v --benchmark-disable
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add config.py tests/test_metrics.py
git commit -m "feat(metrics): add METRICS_TOKEN config"
```

---

### Task 3: Record tool metrics in `mcp_server.py`

**Files:**
- Modify: `mcp_server.py`
- Test: `tests/test_metrics.py` (add cases)

The `call_tool()` function already computes `token_name`, `role`, `result_status`, and `duration_ms` before calling `log_tool_call()`. Metrics recording goes right after `log_tool_call()`.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_metrics.py`:

```python
import pytest
from unittest.mock import patch, MagicMock


@pytest.mark.anyio
async def test_call_tool_increments_tool_calls_counter():
    """After a tool call, TOOL_CALLS counter is incremented."""
    import metrics
    if not metrics.ENABLED:
        pytest.skip("prometheus_client not installed")
    from prometheus_client import REGISTRY, CollectorRegistry
    # Use a fresh registry to isolate counter state
    from prometheus_client import Counter, Histogram
    fresh_calls = Counter(
        "test_tool_calls_total", "test", ["tool", "role", "result"],
        registry=CollectorRegistry(),
    )
    fresh_duration = Histogram(
        "test_tool_duration_seconds", "test", ["tool"],
        buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 30.0],
        registry=CollectorRegistry(),
    )
    with patch("metrics.TOOL_CALLS", fresh_calls), \
         patch("metrics.TOOL_DURATION", fresh_duration), \
         patch("metrics.ENABLED", True), \
         patch("mcp_server._current_audit_info") as mock_cv:
        mock_cv.get.return_value = {"token_name": "t1", "role": "ro", "ip": "127.0.0.1"}
        from mcp_server import call_tool
        await call_tool("read_file", {"file_path": "/etc/hostname"})
    # Counter was incremented once
    samples = list(fresh_calls.collect()[0].samples)
    total = sum(s.value for s in samples if s.labels.get("tool") == "read_file")
    assert total == 1.0


@pytest.mark.anyio
async def test_call_tool_no_metrics_when_disabled():
    """When metrics.ENABLED is False, no AttributeError is raised."""
    with patch("metrics.ENABLED", False):
        from mcp_server import call_tool
        with patch("mcp_server._current_audit_info") as mock_cv:
            mock_cv.get.return_value = {"token_name": "t1", "role": "ro", "ip": "127.0.0.1"}
            # Should not raise
            await call_tool("read_file", {"file_path": "/etc/hostname"})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_metrics.py::test_call_tool_increments_tool_calls_counter -v --benchmark-disable
```

Expected: PASS with no counter incremented (fails on `assert total == 1.0`).

- [ ] **Step 3: Add metrics recording to `mcp_server.py`**

Add `import metrics` at the top of `mcp_server.py` (after the existing imports):

```python
import metrics
```

In `call_tool()`, find the `log_tool_call(...)` call that is the final audit log (around line 268, after the `result_status` determination block). Add after that `log_tool_call(...)` call:

```python
    if metrics.ENABLED:
        metrics.TOOL_CALLS.labels(tool=name, role=role, result=result_status).inc()
        metrics.TOOL_DURATION.labels(tool=name).observe(duration_ms / 1000)
```

The full end of `call_tool()` should look like:

```python
    log_tool_call(
        token_name=token_name,
        role=role,
        ip=ip,
        tool=name,
        params=_extract_params(name, args),
        result=result_status,
        error_code=error_code,
        error_message=error_message,
        duration_ms=duration_ms,
    )

    if metrics.ENABLED:
        metrics.TOOL_CALLS.labels(tool=name, role=role, result=result_status).inc()
        metrics.TOOL_DURATION.labels(tool=name).observe(duration_ms / 1000)

    return [types.TextContent(type="text", text=result_json)]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_metrics.py -v --benchmark-disable
```

Expected: all tests PASS.

- [ ] **Step 5: Run full test suite to check for regressions**

```bash
python3 -m pytest tests/ -v --benchmark-disable
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add mcp_server.py tests/test_metrics.py
git commit -m "feat(metrics): record tool call counter and latency histogram"
```

---

### Task 4: Add `MetricsMiddleware` and `/metrics` endpoint to `main.py`

**Files:**
- Modify: `main.py`
- Test: `tests/test_metrics.py` (add cases)

`MetricsMiddleware` must be registered with `app.add_middleware(MetricsMiddleware)` **after** `app.add_middleware(McpAuthMiddleware)` in source order. FastAPI/Starlette applies middleware in reverse registration order (last registered = outermost), so `MetricsMiddleware` will wrap `McpAuthMiddleware` and see the final HTTP status code.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_metrics.py`:

```python
from httpx import AsyncClient, ASGITransport
from auth import TokenStore


@pytest.fixture
def metrics_store(tmp_path):
    return TokenStore(str(tmp_path / "tokens.json"), "adm_testadmin")


@pytest.fixture
def metrics_app(metrics_store):
    import auth
    original = auth._store
    auth._store = metrics_store
    try:
        from main import app
        yield app
    finally:
        auth._store = original


@pytest.mark.anyio
async def test_metrics_disabled_without_prometheus(metrics_app):
    """When prometheus_client is absent, /metrics returns 503."""
    with patch("metrics.ENABLED", False):
        transport = ASGITransport(app=metrics_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/metrics")
    assert resp.status_code == 503
    assert "prometheus_client" in resp.json()["detail"]


@pytest.mark.anyio
async def test_metrics_disabled_without_token(metrics_app):
    """When METRICS_TOKEN is empty, /metrics returns 503."""
    with patch("metrics.ENABLED", True), \
         patch("config.METRICS_TOKEN", ""):
        transport = ASGITransport(app=metrics_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/metrics")
    assert resp.status_code == 503
    assert "MCP_METRICS_TOKEN" in resp.json()["detail"]


@pytest.mark.anyio
async def test_metrics_unauthorized_with_wrong_token(metrics_app):
    """Wrong Bearer token returns 401."""
    with patch("metrics.ENABLED", True), \
         patch("config.METRICS_TOKEN", "secret123"):
        transport = ASGITransport(app=metrics_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/metrics", headers={"Authorization": "Bearer wrongtoken"})
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_metrics_returns_prometheus_text_with_valid_token(metrics_app):
    """Valid Bearer token returns 200 with Prometheus text format."""
    import metrics as m
    if not m.ENABLED:
        pytest.skip("prometheus_client not installed")
    with patch("config.METRICS_TOKEN", "secret123"):
        transport = ASGITransport(app=metrics_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/metrics", headers={"Authorization": "Bearer secret123"})
    assert resp.status_code == 200
    assert "mymcp_" in resp.text or "# HELP" in resp.text


@pytest.mark.anyio
async def test_metrics_no_auth_required_for_health(metrics_app):
    """MetricsMiddleware does not break /health."""
    transport = ASGITransport(app=metrics_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_metrics.py::test_metrics_disabled_without_prometheus tests/test_metrics.py::test_metrics_disabled_without_token -v --benchmark-disable
```

Expected: `404 Not Found` — `/metrics` endpoint does not exist yet.

- [ ] **Step 3: Update `main.py`**

Add `import metrics` to the imports in `main.py` (after `import config`):

```python
import metrics
```

Add `Response` to the starlette responses import if not already present:
```python
from starlette.responses import JSONResponse, Response
```

Add `MetricsMiddleware` class definition after `McpAuthMiddleware` (before `lifespan`):

```python
class MetricsMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http" or not metrics.ENABLED:
            await self.app(scope, receive, send)
            return
        status_code = 500

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        await self.app(scope, receive, send_wrapper)
        metrics.HTTP_REQUESTS.labels(
            path=scope.get("path", ""),
            method=scope.get("method", ""),
            status=str(status_code),
        ).inc()
```

Register middleware — add `app.add_middleware(MetricsMiddleware)` **after** `app.add_middleware(McpAuthMiddleware)` in `main.py`. The existing code is:

```python
app.add_middleware(McpAuthMiddleware)
```

Change it to:

```python
app.add_middleware(McpAuthMiddleware)
app.add_middleware(MetricsMiddleware)
```

Add the `/metrics` endpoint after `/health`:

```python
@app.get("/metrics")
async def get_metrics(request: Request):
    if not metrics.ENABLED:
        return JSONResponse(
            {"detail": "Metrics disabled: prometheus_client not installed"},
            status_code=503,
        )
    if not config.METRICS_TOKEN:
        return JSONResponse(
            {"detail": "Metrics disabled: MCP_METRICS_TOKEN not configured"},
            status_code=503,
        )
    auth_header = request.headers.get("authorization", "")
    if auth_header != f"Bearer {config.METRICS_TOKEN}":
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
```

- [ ] **Step 4: Run all metrics tests**

```bash
python3 -m pytest tests/test_metrics.py -v --benchmark-disable
```

Expected: all tests PASS.

- [ ] **Step 5: Run full test suite**

```bash
python3 -m pytest tests/ -v --benchmark-disable
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add main.py tests/test_metrics.py
git commit -m "feat(metrics): add MetricsMiddleware and /metrics endpoint"
```

---

### Task 5: Final verification

- [ ] **Step 1: Run all tests**

```bash
python3 -m pytest tests/ -v --benchmark-disable
```

Expected: all tests PASS.

- [ ] **Step 2: Manual smoke test with metrics enabled**

```bash
MCP_METRICS_TOKEN=testtoken python3 main.py &
sleep 1
# Should return 503 (disabled: no token)
curl -s http://localhost:8765/metrics | python3 -m json.tool
# Should return 401
curl -s -H "Authorization: Bearer wrongtoken" http://localhost:8765/metrics | python3 -m json.tool
# Should return Prometheus text
curl -s -H "Authorization: Bearer testtoken" http://localhost:8765/metrics | head -20
kill %1
```

Expected output for last curl:
```
# HELP mymcp_tool_calls_total Total MCP tool calls
# TYPE mymcp_tool_calls_total counter
...
```

- [ ] **Step 3: Manual smoke test with prometheus_client absent**

```bash
pip uninstall -y prometheus_client
python3 main.py &
sleep 1
curl -s http://localhost:8765/metrics | python3 -m json.tool
kill %1
pip install prometheus_client
```

Expected: `{"detail": "Metrics disabled: prometheus_client not installed"}` with status 503.

- [ ] **Step 4: Push and open PR**

```bash
git push origin version-and-metrics
gh pr create \
  --title "feat: version info endpoint and Prometheus monitoring" \
  --body "$(cat <<'EOF'
## Summary
- Add GET /version endpoint (no auth) and version field to GET /health
- upgrade.sh writes APP_DIR/VERSION on successful deploy
- Add optional Prometheus /metrics endpoint (prometheus_client optional dep)
- Track tool call counters, latency histograms, HTTP request counters

## Test plan
- [ ] Run `python3 -m pytest tests/ -v --benchmark-disable`
- [ ] Smoke test /version and /health endpoints
- [ ] Smoke test /metrics with and without MCP_METRICS_TOKEN
- [ ] Smoke test /metrics without prometheus_client installed

🤖 Generated with Claude Code
EOF
)"
```
