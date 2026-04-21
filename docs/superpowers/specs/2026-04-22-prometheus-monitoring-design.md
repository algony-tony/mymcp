# Prometheus Monitoring Design

**Date:** 2026-04-22
**Scope:** Add Prometheus `/metrics` endpoint with HTTP and business metrics

## Background

mymcp has no observability beyond audit logs. There is no way to scrape tool call rates, error rates, or latency into a time-series database. This adds a standard Prometheus endpoint so the server can be scraped by Prometheus and visualized in Grafana.

## Goals

1. Expose `GET /metrics` in Prometheus text format
2. Protect the endpoint with a dedicated `MCP_METRICS_TOKEN` (Bearer auth)
3. Track three metric types: tool calls, tool latency, HTTP requests
4. Zero overhead when `MCP_METRICS_TOKEN` is not configured (endpoint returns 503)

## Non-Goals

- System resource metrics (CPU, memory) — use node_exporter for those
- Token count gauges — can be added later
- OpenTelemetry / distributed tracing
- Pushing metrics (pull model only)

---

## New Dependency

```
prometheus_client>=0.20.0
```

Added to `requirements.txt`.

---

## metrics.py (new file)

Defines all Prometheus metric objects as module-level singletons. No logic — only metric definitions.

```python
from prometheus_client import Counter, Histogram, REGISTRY

TOOL_CALLS = Counter(
    "mymcp_tool_calls_total",
    "Total MCP tool calls",
    ["tool", "role", "result"],  # result: ok | error | denied
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
```

---

## config.py

Add one new config value:

```python
METRICS_TOKEN: str = os.getenv("MCP_METRICS_TOKEN", "")
```

When empty, `/metrics` returns 503 (disabled). This makes the feature opt-in.

---

## mcp_server.py changes

In `call_tool()`, after `log_tool_call()`, add two lines:

```python
from metrics import TOOL_CALLS, TOOL_DURATION
TOOL_CALLS.labels(tool=name, role=role, result=result_status).inc()
TOOL_DURATION.labels(tool=name).observe(duration_ms / 1000)
```

`result_status` is already computed in `call_tool()` as `"ok"`, `"error"`, or `"denied"`.

---

## main.py changes

### HTTP metrics middleware

Add a Starlette middleware after `McpAuthMiddleware` to record HTTP metrics. It wraps the response to capture the final status code:

```python
class MetricsMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        status_code = 500
        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)
        await self.app(scope, receive, send_wrapper)
        from metrics import HTTP_REQUESTS
        HTTP_REQUESTS.labels(
            path=scope.get("path", ""),
            method=scope.get("method", ""),
            status=str(status_code),
        ).inc()
```

Registered with `app.add_middleware(MetricsMiddleware)`, called **after** `app.add_middleware(McpAuthMiddleware)` in source order. FastAPI middleware executes in reverse registration order (last registered = outermost), so `MetricsMiddleware` wraps `McpAuthMiddleware` and sees the final status code.

### /metrics endpoint

```python
@app.get("/metrics")
async def metrics(request: Request):
    if not config.METRICS_TOKEN:
        return JSONResponse({"detail": "Metrics disabled"}, status_code=503)
    auth = request.headers.get("authorization", "")
    if auth != f"Bearer {config.METRICS_TOKEN}":
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
```

---

## Files Changed

| Action | File | Change |
|--------|------|--------|
| Create | `metrics.py` | Prometheus metric definitions |
| Modify | `config.py` | Add `METRICS_TOKEN` |
| Modify | `mcp_server.py` | Record tool call counter + histogram in `call_tool()` |
| Modify | `main.py` | Add `MetricsMiddleware`; add `/metrics` endpoint |
| Modify | `requirements.txt` | Add `prometheus_client>=0.20.0` |

---

## Metrics Reference

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `mymcp_tool_calls_total` | Counter | tool, role, result | Tool invocations by outcome |
| `mymcp_tool_duration_seconds` | Histogram | tool | Latency per tool |
| `mymcp_http_requests_total` | Counter | path, method, status | All HTTP traffic |

### Example Prometheus queries

```promql
# Tool error rate
rate(mymcp_tool_calls_total{result="error"}[5m]) / rate(mymcp_tool_calls_total[5m])

# p95 latency for bash_execute
histogram_quantile(0.95, rate(mymcp_tool_duration_seconds_bucket{tool="bash_execute"}[5m]))

# Requests per second to /mcp
rate(mymcp_http_requests_total{path="/mcp"}[1m])
```

---

## Testing

- Unit test: `TOOL_CALLS`, `TOOL_DURATION` incremented correctly when `call_tool()` is called (mock prometheus_client or use a fresh registry)
- Unit test: `GET /metrics` returns 503 when `METRICS_TOKEN` is empty
- Unit test: `GET /metrics` returns 401 with wrong token
- Unit test: `GET /metrics` returns 200 with correct token and Prometheus text body
- Integration test: after a tool call, the counter for that tool is incremented in the scraped output
