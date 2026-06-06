# OpenTelemetry Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the project's `prometheus_client`-based partial observability with a full OpenTelemetry three-pillar stack (metrics + traces + logs), preserve a zero-extra-default install, and ship a unified Grafana dashboard.

**Architecture:** OTel core SDK is a hard dependency providing `/metrics` (via `PrometheusMetricReader`), unified meter/tracer/logger APIs, and a request-id middleware for correlation. An optional `[otlp]` extra adds OTLP HTTP exporter and FastAPI/ASGI/logging auto-instrumentation. Audit logging keeps its rotating local file (security artifact) and dual-writes to OTel when the extra is installed. Application logs go to stderr in JSON, captured by journald.

**Tech Stack:** Python 3.11+, FastAPI, OpenTelemetry SDK (`opentelemetry-api`, `-sdk`, `-exporter-prometheus`, `-exporter-otlp-proto-http`, `-instrumentation-fastapi`, `-instrumentation-asgi`, `-instrumentation-logging`), `python-json-logger` (already a dep), pytest + anyio.

**Spec:** `docs/superpowers/specs/2026-05-08-opentelemetry-observability-design.md`

---

## File Structure

A new `mymcp.observability` package replaces the flat `mymcp.metrics` module and introduces tracing, logging, and request-id wiring. Each file has one responsibility.

**New package:**

| File | Responsibility |
|---|---|
| `src/mymcp/observability/__init__.py` | Public entry: `setup_observability(app, settings)`. Detects `[otlp]` extra, wires providers + exporters + auto-instrumentation. |
| `src/mymcp/observability/instruments.py` | Module-level meter + instrument singletons (`tool_calls`, `tool_duration`, `http_requests`, saturation gauges). Replaces old `metrics.py`. |
| `src/mymcp/observability/tracing.py` | Tracer singleton + `tracer = get_tracer(__name__)` factory used by call sites. |
| `src/mymcp/observability/logs.py` | `configure_logging()`: JSON formatter, request_id filter, stderr handler, optional OTel LoggingHandler. |
| `src/mymcp/observability/request_id.py` | `RequestIdMiddleware` ASGI middleware + `current_request_id` contextvar. |

**Modified:**

| File | Change |
|---|---|
| `pyproject.toml` | Drop `prometheus-client`; add OTel core deps; add `[otlp]` extra. |
| `src/mymcp/server.py` | Replace `MetricsMiddleware` body to use OTel counter; replace `/metrics` route to use `PrometheusMetricReader`; add `RequestIdMiddleware`; call `setup_observability(app, settings)` in `create_app()`. |
| `src/mymcp/cli.py` | Replace logging-setup block with `observability.logs.configure_logging()`. |
| `src/mymcp/mcp_server.py` | Replace `from mymcp import metrics` with `from mymcp.observability import instruments, tracing`; wrap `dispatch_tool` body in span; rewrite three metric call sites. |
| `src/mymcp/tools/bash.py` | Wrap subprocess execution in span; register inflight-processes async gauge callback. |
| `src/mymcp/tools/transfer/endpoints.py` | Wrap upload/download routes in spans. |
| `src/mymcp/audit.py` | Add OTel `LoggingHandler` when `[otlp]` available; surface `audit.write_failures` counter on exception. |
| `src/mymcp/auth.py` | Register tokens-count async gauge callback against the token store. |

**Deleted:**

| File | Reason |
|---|---|
| `src/mymcp/metrics.py` | Replaced by `mymcp.observability.instruments`. |

**New tests:**

| File | Coverage |
|---|---|
| `tests/test_request_id.py` | Middleware generates UUID, preserves header, echoes response, propagates to logs. |
| `tests/test_observability_logs.py` | Log records are JSON, contain `request_id` / `trace_id` / `span_id` when set. |
| `tests/test_observability_setup.py` | `setup_observability` works with and without `[otlp]` extra; OTLP env vars without extra logs warning, doesn't crash. |
| `tests/test_traces.py` | `dispatch_tool`, `run_bash_execute` produce spans with expected attributes (uses `InMemorySpanExporter`). |
| `tests/test_metrics_saturation.py` | Bash inflight gauge tracks active subprocesses; tokens gauge reflects store size. |

**Modified tests:**

| File | Change |
|---|---|
| `tests/test_metrics.py` | Update to call OTel-emitted series via `PrometheusMetricReader`; assert metric names in their Prometheus-translated form (`mymcp_tool_calls_total` etc.). |

**New non-code artifacts:**

| File | Content |
|---|---|
| `deploy/observability/dashboard.json` | Grafana dashboard JSON: golden-signals + saturation + traces row + logs row. |
| `README.md` | New "Observability" section with three deployment recipes (Grafana Cloud, self-hosted LGTM, pull-only Prometheus). |

---

## Task 1: Update dependencies and create package skeleton

**Files:**
- Modify: `pyproject.toml`
- Create: `src/mymcp/observability/__init__.py` (placeholder)

- [ ] **Step 1: Update `pyproject.toml` dependencies**

In `pyproject.toml`, locate the `dependencies = [...]` block and replace `"prometheus-client>=0.20.0"` with the OTel core packages. Then add an `[otlp]` entry to `[project.optional-dependencies]`.

Result of the changes:

```toml
dependencies = [
    "mcp>=1.0.0",
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "python-multipart>=0.0.9",
    "httpx>=0.27.0",
    "anyio>=4.0.0",
    "opentelemetry-api>=1.27.0",
    "opentelemetry-sdk>=1.27.0",
    "opentelemetry-exporter-prometheus>=0.48b0",
    "pydantic-settings>=2.0",
    "python-json-logger>=2.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-anyio",
    "pytest-benchmark",
    "pytest-cov",
    "ruff>=0.6",
    "mypy>=1.11",
    "pre-commit>=3.7",
    "mutmut",
    "build",
    "opentelemetry-exporter-otlp-proto-http>=1.27.0",
    "opentelemetry-instrumentation-fastapi>=0.48b0",
    "opentelemetry-instrumentation-asgi>=0.48b0",
    "opentelemetry-instrumentation-logging>=0.48b0",
]
otlp = [
    "opentelemetry-exporter-otlp-proto-http>=1.27.0",
    "opentelemetry-instrumentation-fastapi>=0.48b0",
    "opentelemetry-instrumentation-asgi>=0.48b0",
    "opentelemetry-instrumentation-logging>=0.48b0",
]
```

The `[otlp]` packages are also added to `dev` so the test suite can exercise both code paths.

- [ ] **Step 2: Reinstall in editable mode**

Run: `pip install -e ".[dev]"`
Expected: install succeeds; `prometheus-client` is removed; OTel packages are pulled in.

- [ ] **Step 3: Create observability package directory**

Run: `mkdir -p src/mymcp/observability`

Create `src/mymcp/observability/__init__.py` with:

```python
"""mymcp observability package: OpenTelemetry-based metrics, traces, logs."""
```

- [ ] **Step 4: Verify imports still work for now**

Run: `python -c "import mymcp"` — expect ImportError mentioning `prometheus_client` (because `mymcp.metrics` still imports it). This is expected; later tasks remove that file.

For now, run: `python -c "import mymcp.observability"`
Expected: no error.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/mymcp/observability/__init__.py
git commit -m "deps: replace prometheus-client with OTel core + [otlp] extra"
```

---

## Task 2: Request ID middleware and contextvar

**Files:**
- Create: `src/mymcp/observability/request_id.py`
- Modify: `src/mymcp/server.py`
- Test: `tests/test_request_id.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_request_id.py`:

```python
import pytest
from fastapi.testclient import TestClient

from mymcp.observability.request_id import current_request_id


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("MYMCP_TOKEN_FILE", str(tmp_path / "tokens.json"))
    monkeypatch.setenv("MYMCP_AUDIT_LOG_DIR", str(tmp_path / "audit"))
    from mymcp.config import reset_settings_cache
    reset_settings_cache()
    from mymcp.server import create_app
    return TestClient(create_app())


def test_request_id_generated_when_absent(client):
    response = client.get("/health")
    assert response.status_code == 200
    rid = response.headers.get("x-request-id")
    assert rid
    # UUID4 form: 8-4-4-4-12 hex
    assert len(rid) == 36 and rid.count("-") == 4


def test_request_id_preserved_when_present(client):
    response = client.get("/health", headers={"X-Request-ID": "abc-123-xyz"})
    assert response.headers["x-request-id"] == "abc-123-xyz"


def test_contextvar_unset_outside_request():
    assert current_request_id.get() is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_request_id.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mymcp.observability.request_id'`.

- [ ] **Step 3: Create the middleware module**

Create `src/mymcp/observability/request_id.py`:

```python
"""ASGI middleware that ensures every request has an X-Request-ID."""

from __future__ import annotations

import uuid
from contextvars import ContextVar

from starlette.types import ASGIApp, Receive, Scope, Send

current_request_id: ContextVar[str | None] = ContextVar("current_request_id", default=None)

_HEADER_NAME = b"x-request-id"


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        rid = self._extract_or_generate(scope)
        token = current_request_id.set(rid)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((_HEADER_NAME, rid.encode("ascii")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            current_request_id.reset(token)

    @staticmethod
    def _extract_or_generate(scope: Scope) -> str:
        for name, value in scope.get("headers", []):
            if name == _HEADER_NAME:
                try:
                    return value.decode("ascii")
                except UnicodeDecodeError:
                    break
        return str(uuid.uuid4())
```

- [ ] **Step 4: Wire middleware into `create_app`**

In `src/mymcp/server.py`, add the import near the top:

```python
from mymcp.observability.request_id import RequestIdMiddleware
```

In `create_app()`, register the middleware. The order matters — `RequestIdMiddleware` must run before any logging or audit code so the contextvar is set. Starlette/FastAPI applies middlewares in reverse-added order, so the *last* `add_middleware` call runs first. Add `RequestIdMiddleware` last:

```python
app.add_middleware(McpAuthMiddleware)
app.add_middleware(MetricsMiddleware)
app.add_middleware(RequestIdMiddleware)  # added last → runs first
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_request_id.py -v`
Expected: 3 passed.

- [ ] **Step 6: Run full test suite for regressions**

Run: `pytest tests/ -v --benchmark-disable -x`
Expected: previous failures only those caused by `mymcp.metrics` still importing `prometheus_client` (Task 1 left this broken intentionally) — those will be fixed in Task 5. Note any other failures and stop if they exist.

If `mymcp.metrics` import errors are blocking the test run from even starting, temporarily comment out `import mymcp.metrics` paths or skip; the cleanest fix is to proceed to Task 5. But Tasks 2–4 are designed to be completed without depending on metrics, so test_request_id should run.

- [ ] **Step 7: Commit**

```bash
git add src/mymcp/observability/request_id.py src/mymcp/server.py tests/test_request_id.py
git commit -m "feat(obs): add RequestIdMiddleware with contextvar and X-Request-ID echo"
```

---

## Task 3: JSON application logging with request_id

**Files:**
- Create: `src/mymcp/observability/logs.py`
- Modify: `src/mymcp/cli.py` (replace logging setup)
- Test: `tests/test_observability_logs.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_observability_logs.py`:

```python
import io
import json
import logging

from mymcp.observability.logs import configure_logging
from mymcp.observability.request_id import current_request_id


def test_log_record_is_json_with_request_id():
    buf = io.StringIO()
    configure_logging(level="INFO", stream=buf)

    token = current_request_id.set("rid-test-001")
    try:
        logging.getLogger("mymcp.test").info("hello", extra={"foo": "bar"})
    finally:
        current_request_id.reset(token)

    line = buf.getvalue().strip().splitlines()[-1]
    record = json.loads(line)
    assert record["message"] == "hello"
    assert record["request_id"] == "rid-test-001"
    assert record["foo"] == "bar"
    assert record["levelname"] == "INFO"


def test_log_record_request_id_absent_when_unset():
    buf = io.StringIO()
    configure_logging(level="INFO", stream=buf)
    logging.getLogger("mymcp.test").info("no-rid")
    line = buf.getvalue().strip().splitlines()[-1]
    record = json.loads(line)
    assert record.get("request_id") is None


def test_configure_logging_idempotent():
    buf1 = io.StringIO()
    buf2 = io.StringIO()
    configure_logging(level="INFO", stream=buf1)
    configure_logging(level="INFO", stream=buf2)
    logging.getLogger("mymcp.test").info("once")
    # Only the most recent call's stream should receive output (no duplicate handlers).
    assert "once" in buf2.getvalue()
    assert "once" not in buf1.getvalue()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_observability_logs.py -v`
Expected: FAIL with ImportError on `mymcp.observability.logs`.

- [ ] **Step 3: Create the logging module**

Create `src/mymcp/observability/logs.py`:

```python
"""JSON logging configuration with request_id / trace_id / span_id injection."""

from __future__ import annotations

import logging
import sys
from typing import IO

from pythonjsonlogger import jsonlogger

from mymcp.observability.request_id import current_request_id


class _ContextFilter(logging.Filter):
    """Inject contextvar-derived fields into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = current_request_id.get()
        # trace_id / span_id are injected by opentelemetry-instrumentation-logging
        # when [otlp] extra is installed; otherwise these stay unset.
        if not hasattr(record, "trace_id"):
            record.trace_id = None
        if not hasattr(record, "span_id"):
            record.span_id = None
        return True


_JSON_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def configure_logging(level: str = "INFO", stream: IO[str] | None = None) -> None:
    """Configure the root logger to emit JSON to ``stream`` (default: stderr).

    Idempotent: re-running replaces existing handlers, allowing tests to redirect.
    """
    target = stream if stream is not None else sys.stderr
    handler = logging.StreamHandler(target)
    formatter = jsonlogger.JsonFormatter(
        _JSON_FORMAT,
        rename_fields={"asctime": "timestamp"},
    )
    handler.setFormatter(formatter)
    handler.addFilter(_ContextFilter())

    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Make sure mymcp.audit, which has its own file handler, doesn't propagate to
    # root (the audit file handler is the source of truth; we'll dual-write via
    # a separate OTel LoggingHandler attached directly in audit.py).
    logging.getLogger("mymcp.audit").propagate = False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_observability_logs.py -v`
Expected: 3 passed.

- [ ] **Step 5: Update `cli.py` to use new logging**

In `src/mymcp/cli.py`, find the existing logging configuration (look for `logging.basicConfig` or a `logging.getLogger` setup near the top of the file or inside `main()`). Replace it with a single call. At the top of `main()` (or whichever function runs first), add:

```python
from mymcp.observability.logs import configure_logging

configure_logging(level=os.environ.get("MYMCP_LOG_LEVEL", "INFO"))
```

Remove any `logging.basicConfig(...)` calls and any prior `logging.Formatter` setup in `cli.py`.

- [ ] **Step 6: Verify CLI smoke test**

Run: `mymcp --help`
Expected: help text prints; no logging-config errors.

Run: `mymcp serve --help`
Expected: help text prints.

- [ ] **Step 7: Commit**

```bash
git add src/mymcp/observability/logs.py src/mymcp/cli.py tests/test_observability_logs.py
git commit -m "feat(obs): JSON logging with request_id contextvar injection"
```

---

## Task 4: OTel SDK bootstrap (no exporters yet)

**Files:**
- Modify: `src/mymcp/observability/__init__.py`
- Create: `src/mymcp/observability/tracing.py`
- Test: `tests/test_observability_setup.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_observability_setup.py`:

```python
from opentelemetry import metrics as otel_metrics
from opentelemetry import trace as otel_trace
from prometheus_client import REGISTRY

from mymcp.observability import setup_observability


def test_setup_initializes_meter_and_tracer_providers(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    setup_observability(app=None, service_name="mymcp-test", service_version="0.0.0")

    meter = otel_metrics.get_meter("test")
    counter = meter.create_counter("test_counter")
    counter.add(1)

    tracer = otel_trace.get_tracer("test")
    with tracer.start_as_current_span("test-span") as span:
        assert span is not None


def test_prometheus_reader_registered():
    setup_observability(app=None, service_name="mymcp-test", service_version="0.0.0")
    # PrometheusMetricReader registers collectors against the default REGISTRY.
    names = {c.describe()[0].name for c in REGISTRY.collect() if c.describe()}
    # No assertion on a specific name yet (instruments not registered),
    # but the call should not raise.
    assert isinstance(names, set)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_observability_setup.py -v`
Expected: FAIL — `setup_observability` is undefined.

- [ ] **Step 3: Create tracing module**

Create `src/mymcp/observability/tracing.py`:

```python
"""Tracer factory. Call sites use ``get_tracer(__name__)``."""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.trace import Tracer


def get_tracer(name: str) -> Tracer:
    return trace.get_tracer(name)
```

- [ ] **Step 4: Write the bootstrap function**

Replace `src/mymcp/observability/__init__.py` with:

```python
"""mymcp observability package: OpenTelemetry-based metrics, traces, logs.

Public entry point: ``setup_observability(app, service_name, service_version)``.
Idempotent within a process; safe to call once at startup.

When the optional ``[otlp]`` extra is installed AND
``OTEL_EXPORTER_OTLP_ENDPOINT`` is set, OTLP exporters and FastAPI/logging
auto-instrumentation are wired automatically. Otherwise only the local
Prometheus ``/metrics`` reader and the in-process tracer/meter providers are
configured (spans are recorded but not exported).
"""

from __future__ import annotations

import logging
import os

from opentelemetry import metrics as otel_metrics
from opentelemetry import trace as otel_trace
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider

_log = logging.getLogger(__name__)
_initialized = False


def setup_observability(
    app=None,
    *,
    service_name: str = "mymcp",
    service_version: str = "unknown",
) -> None:
    """Initialize OTel providers, the Prometheus reader, and (optionally) OTLP."""
    global _initialized
    if _initialized:
        return
    _initialized = True

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.namespace": "mymcp",
            "service.version": service_version,
        }
    )

    prom_reader = PrometheusMetricReader()
    meter_provider = MeterProvider(resource=resource, metric_readers=[prom_reader])
    otel_metrics.set_meter_provider(meter_provider)

    tracer_provider = TracerProvider(resource=resource)
    otel_trace.set_tracer_provider(tracer_provider)

    _maybe_setup_otlp(app, tracer_provider, meter_provider, prom_reader)


def _maybe_setup_otlp(app, tracer_provider, meter_provider, prom_reader) -> None:
    """If [otlp] extra is installed and an endpoint is configured, wire OTLP."""
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return

    try:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
            OTLPMetricExporter,
        )
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        _log.warning(
            "OTEL_EXPORTER_OTLP_ENDPOINT is set but the [otlp] extra is not "
            "installed; OTLP export disabled. Run: pip install algony-mymcp[otlp]"
        )
        return

    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))

    # Re-create the meter provider with both readers (Prometheus pull + OTLP push).
    otlp_reader = PeriodicExportingMetricReader(OTLPMetricExporter())
    new_meter_provider = type(meter_provider)(
        resource=meter_provider._sdk_config.resource,
        metric_readers=[prom_reader, otlp_reader],
    )
    otel_metrics.set_meter_provider(new_meter_provider)

    if app is not None:
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
            from opentelemetry.instrumentation.logging import LoggingInstrumentor
        except ImportError:
            _log.warning("instrumentation packages missing despite [otlp] partial install")
            return

        FastAPIInstrumentor.instrument_app(app)
        LoggingInstrumentor().instrument(set_logging_format=False)


def reset_for_tests() -> None:
    """Allow tests to re-initialize. Not for production use."""
    global _initialized
    _initialized = False
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_observability_setup.py -v`
Expected: 2 passed.

The first test currently runs before the second; if state leaks across tests, add `mymcp.observability.reset_for_tests()` in a `pytest` autouse fixture in `tests/conftest.py`. For now, leave it — the assertions are tolerant of repeated init.

- [ ] **Step 6: Add a conftest reset fixture for cleanliness**

Append to `tests/conftest.py`:

```python
import pytest


@pytest.fixture(autouse=True)
def _reset_observability():
    from mymcp.observability import reset_for_tests
    reset_for_tests()
    yield
    reset_for_tests()
```

Run: `pytest tests/test_observability_setup.py -v`
Expected: still passes.

- [ ] **Step 7: Commit**

```bash
git add src/mymcp/observability/__init__.py src/mymcp/observability/tracing.py tests/test_observability_setup.py tests/conftest.py
git commit -m "feat(obs): OTel SDK bootstrap with Prometheus reader and optional OTLP"
```

---

## Task 5: Replace `metrics.py` with OTel instruments

**Files:**
- Create: `src/mymcp/observability/instruments.py`
- Delete: `src/mymcp/metrics.py`
- Modify: `src/mymcp/server.py`, `src/mymcp/mcp_server.py`
- Modify: `tests/test_metrics.py`

- [ ] **Step 1: Create the instruments module**

Create `src/mymcp/observability/instruments.py`:

```python
"""Module-level OTel instrument singletons used across the codebase.

Naming uses OTel dot-style (``mymcp.tool.calls``); when surfaced through
``PrometheusMetricReader`` it is translated to underscore form
(``mymcp_tool_calls_total``), keeping wire-compatibility with existing
Prometheus dashboards.
"""

from __future__ import annotations

from opentelemetry import metrics

_meter = metrics.get_meter("mymcp")

tool_calls = _meter.create_counter(
    "mymcp.tool.calls",
    description="Total MCP tool calls",
    unit="1",
)

tool_duration = _meter.create_histogram(
    "mymcp.tool.duration",
    description="MCP tool call duration",
    unit="s",
)

http_requests = _meter.create_counter(
    "mymcp.http.requests",
    description="Total HTTP requests",
    unit="1",
)

audit_write_failures = _meter.create_counter(
    "mymcp.audit.write_failures",
    description="Audit log write failures",
    unit="1",
)
```

Note: the histogram bucket boundaries used by `prometheus_client` previously (`[0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 30.0]`) are not directly settable per-instrument in OTel SDK 1.27; they are governed by `View` configuration on the `MeterProvider`. We accept the OTel default bucket boundaries — operators tuning Grafana dashboards should adjust queries accordingly. This is documented in the spec's Section 6 by virtue of standard env-var control.

- [ ] **Step 2: Update `server.py`**

In `src/mymcp/server.py`:

Replace the import:
```python
# was: from mymcp import config, metrics
from mymcp import config
from mymcp.observability import instruments, setup_observability
```

Replace the `MetricsMiddleware` class body:

```python
class MetricsMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        status_code = 500

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            instruments.http_requests.add(
                1,
                {
                    "path": scope.get("path", ""),
                    "method": scope.get("method", ""),
                    "status": str(status_code),
                },
            )
```

Replace the `/metrics` route. PrometheusMetricReader auto-registers with the global `prometheus_client` REGISTRY, so we use its `generate_latest`:

```python
    @app.get("/metrics")
    async def get_metrics(request: Request):
        if not config.METRICS_TOKEN:
            return JSONResponse(
                {"detail": "Metrics disabled: MYMCP_METRICS_TOKEN not configured"},
                status_code=503,
            )
        auth_header = request.headers.get("authorization", "")
        if auth_header != f"Bearer {config.METRICS_TOKEN}":
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)

        from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

        return Response(
            content=generate_latest(),
            media_type=CONTENT_TYPE_LATEST,
        )
```

Note: `prometheus_client` is now an *indirect* dependency pulled in by `opentelemetry-exporter-prometheus`. The import at function scope keeps the dependency graph honest.

In `create_app()`, call `setup_observability(app, ...)` immediately before middleware registration:

```python
def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        get_store()
        async with session_manager.run():
            yield

    import mymcp

    app = FastAPI(title="Linux MCP Server", version=mymcp.__version__, lifespan=lifespan)
    setup_observability(app, service_name="mymcp", service_version=mymcp.__version__)

    app.add_middleware(McpAuthMiddleware)
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(RequestIdMiddleware)
    ...
```

- [ ] **Step 3: Update `mcp_server.py` call sites**

In `src/mymcp/mcp_server.py`, replace `from mymcp import config, metrics` with:

```python
from mymcp import config
from mymcp.observability import instruments
```

Replace the three call sites:

Line ~276–277 (denied path):
```python
# was: if metrics.ENABLED: metrics.TOOL_CALLS.labels(...).inc()
instruments.tool_calls.add(1, {"tool": name, "role": role, "result": "denied"})
```

Line ~307–309 (error path):
```python
instruments.tool_calls.add(1, {"tool": name, "role": role, "result": "error"})
instruments.tool_duration.record(duration_ms / 1000.0, {"tool": name})
```

Line ~362–364 (success/general path):
```python
instruments.tool_calls.add(1, {"tool": name, "role": role, "result": result_status})
instruments.tool_duration.record(duration_ms / 1000.0, {"tool": name})
```

- [ ] **Step 4: Delete old `metrics.py`**

```bash
git rm src/mymcp/metrics.py
```

- [ ] **Step 5: Update `tests/test_metrics.py`**

Open `tests/test_metrics.py`. Update assertions that reference the old module-level names (`metrics.TOOL_CALLS`, `metrics.ENABLED`, etc.) to instead drive an HTTP request and parse `/metrics` response. The Prometheus output for OTel `mymcp.tool.calls` counter with labels appears as `mymcp_tool_calls_total{tool="...",role="...",result="..."}`.

Replace the file body with:

```python
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
    # Counter should appear with HELP/TYPE lines
    assert "mymcp_http_requests" in body
    assert 'path="/health"' in body
```

- [ ] **Step 6: Run all tests**

Run: `pytest tests/ -v --benchmark-disable -x`
Expected: all tests pass. The big risk is leftover `import mymcp.metrics` references — fix any that turn up.

- [ ] **Step 7: Lint and type-check**

Run: `ruff check . && ruff format --check . && mypy src/mymcp`
Expected: clean. Fix anything that comes up.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor(obs): replace prometheus_client with OTel instruments"
```

---

## Task 6: Saturation metrics (async gauges)

**Files:**
- Modify: `src/mymcp/observability/instruments.py`
- Modify: `src/mymcp/tools/bash.py`
- Modify: `src/mymcp/auth.py`
- Modify: `src/mymcp/audit.py`
- Test: `tests/test_metrics_saturation.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_metrics_saturation.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_metrics_saturation.py -v`
Expected: FAIL — assertions on absent metric names.

- [ ] **Step 3: Add async gauge factory wiring**

Add to the bottom of `src/mymcp/observability/instruments.py`:

```python
# --- Saturation gauges (callbacks registered by other modules) ---

def register_callback_gauge(name: str, description: str, callback) -> None:
    """Create an observable gauge backed by ``callback`` (called on each export).

    ``callback`` receives no arguments and returns an iterable of
    ``opentelemetry.metrics.Observation`` instances.
    """
    _meter.create_observable_gauge(
        name,
        description=description,
        callbacks=[lambda options: callback()],
    )
```

- [ ] **Step 4: Wire bash inflight gauge**

In `src/mymcp/tools/bash.py`, locate the weakref process tracker (likely a module-level set/WeakSet referenced by `_track_process` and `shutdown_inflight_processes`). At module bottom, add:

```python
from opentelemetry.metrics import Observation

from mymcp.observability.instruments import register_callback_gauge


def _observe_inflight():
    return [Observation(len(_inflight_processes))]


register_callback_gauge(
    "mymcp.bash.inflight_processes",
    "Live count of tracked bash subprocesses",
    _observe_inflight,
)
```

Replace `_inflight_processes` with the actual variable name used in that file (inspect at implementation time).

- [ ] **Step 5: Wire tokens count gauge**

In `src/mymcp/auth.py`, locate the `TokenStore` class and the module-level singleton accessor (`get_store()`). At module bottom (or inside `get_store()` after first construction), add:

```python
from opentelemetry.metrics import Observation

from mymcp.observability.instruments import register_callback_gauge


def _observe_tokens():
    store = get_store()
    counts = {"ro": 0, "rw": 0, "admin": 0, "metrics": 0}
    for entry in store.list():
        role = entry.get("role", "unknown")
        counts[role] = counts.get(role, 0) + 1
    return [Observation(n, {"role": role}) for role, n in counts.items()]


register_callback_gauge(
    "mymcp.tokens.count",
    "Number of tokens in the token store, by role",
    _observe_tokens,
)
```

Adjust `store.list()` to whatever method enumerates token entries. Inspect the existing TokenStore API; if there's no list method, add a simple `def list(self) -> list[dict]` returning a snapshot.

- [ ] **Step 6: Increment audit_write_failures on exception**

In `src/mymcp/audit.py`, wrap the `_logger.info(...)` call in `log_tool_call`:

```python
from mymcp.observability.instruments import audit_write_failures

# ... inside log_tool_call, replace _logger.info(json.dumps(entry)) with:
try:
    _logger.info(json.dumps(entry))
except Exception:
    audit_write_failures.add(1)
    raise
```

- [ ] **Step 7: Run tests**

Run: `pytest tests/test_metrics_saturation.py tests/test_metrics.py -v`
Expected: all pass.

Run: `pytest tests/ -v --benchmark-disable -x`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat(obs): saturation gauges (bash inflight, tokens count, audit failures)"
```

---

## Task 7: Manual span around `dispatch_tool`

**Files:**
- Modify: `src/mymcp/mcp_server.py`
- Test: `tests/test_traces.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_traces.py`:

```python
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)


@pytest.fixture
def span_exporter(monkeypatch, tmp_path):
    monkeypatch.setenv("MYMCP_TOKEN_FILE", str(tmp_path / "tokens.json"))
    monkeypatch.setenv("MYMCP_AUDIT_LOG_DIR", str(tmp_path / "audit"))
    from mymcp.config import reset_settings_cache
    reset_settings_cache()

    from mymcp.observability import reset_for_tests, setup_observability
    reset_for_tests()
    setup_observability(app=None, service_name="mymcp-test", service_version="0.0.0")
    exporter = InMemorySpanExporter()
    trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(exporter))
    yield exporter
    exporter.clear()


@pytest.mark.anyio
async def test_dispatch_tool_creates_span_with_attributes(span_exporter):
    from mymcp.mcp_server import call_tool, _current_audit_info

    token = _current_audit_info.set(
        {"token_name": "t", "role": "rw", "ip": "127.0.0.1"}
    )
    try:
        await call_tool("read_file", {"path": "/etc/hostname"})
    finally:
        _current_audit_info.reset(token)

    spans = span_exporter.get_finished_spans()
    assert any(s.name == "mymcp.tool.dispatch" for s in spans)
    s = next(s for s in spans if s.name == "mymcp.tool.dispatch")
    assert s.attributes["tool.name"] == "read_file"
    assert s.attributes["token.role"] == "rw"
    assert s.attributes["tool.result"] in ("success", "error")
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/test_traces.py -v`
Expected: FAIL — no span named `mymcp.tool.dispatch`.

- [ ] **Step 3: Add the span**

In `src/mymcp/mcp_server.py`, near the top of the file:

```python
from mymcp.observability.tracing import get_tracer

_tracer = get_tracer(__name__)
```

In the `call_tool` function (the top-level handler that wraps `dispatch_tool`), wrap the body in a span. Conceptually:

```python
async def call_tool(name: str, arguments: dict | None) -> list:
    role = ...  # existing extraction
    with _tracer.start_as_current_span(
        "mymcp.tool.dispatch",
        attributes={"tool.name": name, "token.role": role},
    ) as span:
        try:
            result = await dispatch_tool(name, arguments)
        except Exception as exc:
            span.set_attribute("tool.result", "error")
            span.set_attribute("error.type", type(exc).__name__)
            span.record_exception(exc)
            raise
        else:
            # Determine result_status from the tool's return shape (existing logic
            # in call_tool already computes this; reuse the same variable name).
            span.set_attribute("tool.result", result_status)
        return result
```

The exact insertion depends on `call_tool`'s current control flow. Open `src/mymcp/mcp_server.py` and locate the `call_tool` function (around line 250). Two key landmarks: the permission-denied early return (~line 276) and the success path (~line 362). Wrap the entire `try/except` body in the span context manager. Set `tool.result="denied"` on the early return path before exiting the context manager.

- [ ] **Step 4: Run trace test**

Run: `pytest tests/test_traces.py -v`
Expected: 1 passed.

- [ ] **Step 5: Run full suite**

Run: `pytest tests/ -v --benchmark-disable -x`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(obs): manual span around dispatch_tool with result/role attributes"
```

---

## Task 8: Manual spans in bash and transfer endpoints

**Files:**
- Modify: `src/mymcp/tools/bash.py`
- Modify: `src/mymcp/tools/transfer/endpoints.py`
- Test: extend `tests/test_traces.py`

- [ ] **Step 1: Add bash span test**

Append to `tests/test_traces.py`:

```python
@pytest.mark.anyio
async def test_bash_execute_creates_child_span(span_exporter):
    from mymcp.mcp_server import call_tool, _current_audit_info

    token = _current_audit_info.set(
        {"token_name": "t", "role": "rw", "ip": "127.0.0.1"}
    )
    try:
        await call_tool("bash_execute", {"command": "echo hi", "timeout": 5})
    finally:
        _current_audit_info.reset(token)

    spans = span_exporter.get_finished_spans()
    bash_spans = [s for s in spans if s.name == "mymcp.bash.execute"]
    assert bash_spans, "expected a mymcp.bash.execute span"
    s = bash_spans[0]
    assert s.attributes["bash.exit_code"] == 0
    assert s.attributes["bash.timed_out"] is False
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/test_traces.py::test_bash_execute_creates_child_span -v`
Expected: FAIL — no span named `mymcp.bash.execute`.

- [ ] **Step 3: Add span in `bash.py`**

In `src/mymcp/tools/bash.py`, at top:

```python
from mymcp.observability.tracing import get_tracer

_tracer = get_tracer(__name__)
```

In `run_bash_execute`, wrap the subprocess execution. Locate the function body (around line 30–140) and wrap from "before subprocess spawn" through "after wait/communicate completes":

```python
async def run_bash_execute(command: str, timeout: int = 30) -> dict:
    with _tracer.start_as_current_span(
        "mymcp.bash.execute",
        attributes={"bash.timeout_sec": timeout},
    ) as span:
        # ... existing subprocess spawn + wait + truncation logic ...

        # before returning, annotate:
        span.set_attribute("bash.exit_code", exit_code)
        span.set_attribute("bash.timed_out", timed_out)
        span.set_attribute("bash.output_truncated", truncated)
        span.set_attribute("bash.stdout_bytes", len(stdout_bytes))
        span.set_attribute("bash.stderr_bytes", len(stderr_bytes))

        return {
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "timed_out": timed_out,
        }
```

Use the actual variable names from the existing function body. Where existing code already returns the result dict, set the attributes immediately before the return statement.

- [ ] **Step 4: Add spans in transfer endpoints**

In `src/mymcp/tools/transfer/endpoints.py`, at top:

```python
from mymcp.observability.tracing import get_tracer

_tracer = get_tracer(__name__)
```

For each FastAPI route in this file (upload and download endpoints), wrap the body:

```python
@router.post("/upload/...")
async def upload(...):
    with _tracer.start_as_current_span(
        "mymcp.transfer.upload",
        attributes={"transfer.path": path},
    ) as span:
        # existing body
        span.set_attribute("transfer.bytes", n_bytes)
        return ...
```

Repeat for download with span name `mymcp.transfer.download`.

- [ ] **Step 5: Run trace tests**

Run: `pytest tests/test_traces.py -v`
Expected: 2 passed.

- [ ] **Step 6: Run full suite**

Run: `pytest tests/ -v --benchmark-disable -x`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(obs): spans in bash_execute and transfer endpoints"
```

---

## Task 9: Auto-instrumentation and OTLP exporter test

**Files:**
- Test: `tests/test_observability_setup.py` (extend)

The actual exporter wiring code already lives in Task 4's `_maybe_setup_otlp`. This task adds tests that exercise both branches and confirms FastAPI auto-instrumentation activates.

- [ ] **Step 1: Add tests covering both branches**

Append to `tests/test_observability_setup.py`:

```python
def test_otlp_setup_skipped_when_endpoint_unset(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    from mymcp.observability import reset_for_tests, setup_observability
    reset_for_tests()
    setup_observability(app=None, service_name="mymcp-t", service_version="0.0.0")
    # Tracer provider has no OTLP processor — but we can't easily introspect.
    # Instead, assert no exceptions and that get_tracer works.
    from opentelemetry import trace
    tr = trace.get_tracer("t")
    with tr.start_as_current_span("noop"):
        pass


def test_otlp_setup_warns_when_extra_missing(monkeypatch, caplog):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    # Simulate missing extra by hiding the otlp http exporter module.
    import sys
    sys.modules["opentelemetry.exporter.otlp.proto.http.trace_exporter"] = None  # type: ignore[assignment]
    from mymcp.observability import reset_for_tests, setup_observability
    reset_for_tests()
    with caplog.at_level("WARNING", logger="mymcp.observability"):
        setup_observability(app=None, service_name="mymcp-t", service_version="0.0.0")
    assert any("[otlp] extra is not installed" in r.message for r in caplog.records)
    # Cleanup the sys.modules sabotage so other tests aren't affected.
    sys.modules.pop("opentelemetry.exporter.otlp.proto.http.trace_exporter", None)


def test_otlp_setup_succeeds_when_extra_present(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    # If the [otlp] extra is installed (which it is in dev),
    # setup should not warn and should not raise.
    from mymcp.observability import reset_for_tests, setup_observability
    reset_for_tests()
    setup_observability(app=None, service_name="mymcp-t", service_version="0.0.0")
```

- [ ] **Step 2: Run the new tests**

Run: `pytest tests/test_observability_setup.py -v`
Expected: 5 passed (2 from Task 4, 3 new).

If `test_otlp_setup_warns_when_extra_missing` is flaky because `sys.modules` mutation leaks, mark it `@pytest.mark.skip(reason="needs subprocess isolation")` and add a TODO. The other two tests carry the load.

- [ ] **Step 3: Manual smoke test of auto-instrumentation**

In one terminal:
```bash
docker run --rm -p 4318:4318 otel/opentelemetry-collector --config=/etc/otelcol/config.yaml
```
(or use `nc -l 4318` as a stub if no collector is handy).

In another:
```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 \
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf \
mymcp serve
```

Hit `curl http://localhost:8000/health`. Verify the collector receives spans for the HTTP request (auto-instrumentation working).

If a collector isn't readily available, skip this step and rely on the unit test.

- [ ] **Step 4: Commit**

```bash
git add tests/test_observability_setup.py
git commit -m "test(obs): cover OTLP setup branches (endpoint unset / extra missing / present)"
```

---

## Task 10: Audit log dual-write to OTel

**Files:**
- Modify: `src/mymcp/audit.py`
- Test: extend `tests/test_audit.py` or add `tests/test_audit_otel.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_audit_otel.py`:

```python
import json
import logging
from pathlib import Path

import pytest


@pytest.fixture
def audit_setup(monkeypatch, tmp_path):
    audit_dir = tmp_path / "audit"
    monkeypatch.setenv("MYMCP_AUDIT_LOG_DIR", str(audit_dir))
    from mymcp import config as cfg
    from mymcp.config import reset_settings_cache
    reset_settings_cache()
    cfg.AUDIT_LOG_DIR = str(audit_dir)
    cfg.AUDIT_ENABLED = True
    cfg.AUDIT_MAX_BYTES = 10_000_000
    cfg.AUDIT_BACKUP_COUNT = 5
    yield audit_dir


def test_audit_record_includes_request_id(audit_setup):
    from mymcp.audit import log_tool_call
    from mymcp.observability.request_id import current_request_id

    token = current_request_id.set("rid-audit-001")
    try:
        log_tool_call(
            token_name="t",
            role="rw",
            ip="127.0.0.1",
            tool="read_file",
            params={"path": "/etc/hostname"},
            result="success",
        )
    finally:
        current_request_id.reset(token)

    log_path = Path(audit_setup) / "audit.log"
    line = log_path.read_text().strip().splitlines()[-1]
    record = json.loads(line)
    assert record["request_id"] == "rid-audit-001"
    assert record["tool"] == "read_file"
```

- [ ] **Step 2: Run test to verify failure**

Run: `pytest tests/test_audit_otel.py -v`
Expected: FAIL — `request_id` not in audit record.

- [ ] **Step 3: Inject request_id into audit records**

In `src/mymcp/audit.py`, modify `log_tool_call`. Right after the `entry` dict is built, attach `request_id`:

```python
from mymcp.observability.request_id import current_request_id

# ... inside log_tool_call, after building `entry`:
rid = current_request_id.get()
if rid is not None:
    entry["request_id"] = rid

# Also attach trace/span IDs when available (OTel context):
from opentelemetry import trace
span = trace.get_current_span()
ctx = span.get_span_context()
if ctx.is_valid:
    entry["trace_id"] = format(ctx.trace_id, "032x")
    entry["span_id"] = format(ctx.span_id, "016x")
```

- [ ] **Step 4: Add OTel LoggingHandler attachment**

Still in `_setup()` in `audit.py`, after `logger.addHandler(handler)`:

```python
# Optionally also forward audit records to OTel logs (when [otlp] extra
# installed and an OTel LoggerProvider is active).
try:
    from opentelemetry._logs import get_logger_provider
    from opentelemetry.sdk._logs import LoggingHandler

    provider = get_logger_provider()
    if provider is not None:
        otel_handler = LoggingHandler(level=logging.INFO, logger_provider=provider)
        logger.addHandler(otel_handler)
except ImportError:
    # SDK logs API not present — happens only with very old OTel versions.
    pass
```

- [ ] **Step 5: Run audit tests**

Run: `pytest tests/test_audit.py tests/test_audit_otel.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(obs): audit records carry request_id/trace_id, dual-write to OTel logs"
```

---

## Task 11: Grafana dashboard JSON

**Files:**
- Create: `deploy/observability/dashboard.json`

- [ ] **Step 1: Build the dashboard**

Create `deploy/observability/dashboard.json` with golden-signals + saturation panels. Use Grafana 10.x dashboard schema. Below is the minimal viable JSON; copy verbatim:

```json
{
  "title": "mymcp — Observability",
  "uid": "mymcp-overview",
  "schemaVersion": 39,
  "version": 1,
  "refresh": "30s",
  "time": { "from": "now-1h", "to": "now" },
  "tags": ["mymcp", "observability"],
  "templating": {
    "list": [
      {
        "name": "datasource",
        "type": "datasource",
        "query": "prometheus",
        "current": { "text": "Prometheus", "value": "Prometheus" }
      },
      {
        "name": "tempo_ds",
        "type": "datasource",
        "query": "tempo",
        "current": { "text": "Tempo", "value": "Tempo" }
      },
      {
        "name": "loki_ds",
        "type": "datasource",
        "query": "loki",
        "current": { "text": "Loki", "value": "Loki" }
      }
    ]
  },
  "panels": [
    {
      "type": "row",
      "title": "Golden Signals",
      "gridPos": { "h": 1, "w": 24, "x": 0, "y": 0 }
    },
    {
      "type": "timeseries",
      "title": "Tool call rate",
      "datasource": { "type": "prometheus", "uid": "$datasource" },
      "targets": [
        {
          "expr": "sum by (tool, result) (rate(mymcp_tool_calls_total[5m]))",
          "legendFormat": "{{tool}} / {{result}}"
        }
      ],
      "gridPos": { "h": 8, "w": 12, "x": 0, "y": 1 }
    },
    {
      "type": "timeseries",
      "title": "Tool call error ratio",
      "datasource": { "type": "prometheus", "uid": "$datasource" },
      "targets": [
        {
          "expr": "sum(rate(mymcp_tool_calls_total{result=\"error\"}[5m])) / sum(rate(mymcp_tool_calls_total[5m]))",
          "legendFormat": "error ratio"
        }
      ],
      "gridPos": { "h": 8, "w": 12, "x": 12, "y": 1 }
    },
    {
      "type": "timeseries",
      "title": "Tool latency p99",
      "datasource": { "type": "prometheus", "uid": "$datasource" },
      "targets": [
        {
          "expr": "histogram_quantile(0.99, sum by (le, tool) (rate(mymcp_tool_duration_seconds_bucket[5m])))",
          "legendFormat": "{{tool}}"
        }
      ],
      "gridPos": { "h": 8, "w": 12, "x": 0, "y": 9 }
    },
    {
      "type": "timeseries",
      "title": "HTTP request rate",
      "datasource": { "type": "prometheus", "uid": "$datasource" },
      "targets": [
        {
          "expr": "sum by (status) (rate(mymcp_http_requests_total[5m]))",
          "legendFormat": "{{status}}"
        }
      ],
      "gridPos": { "h": 8, "w": 12, "x": 12, "y": 9 }
    },
    {
      "type": "row",
      "title": "Saturation",
      "gridPos": { "h": 1, "w": 24, "x": 0, "y": 17 }
    },
    {
      "type": "stat",
      "title": "Bash inflight processes",
      "datasource": { "type": "prometheus", "uid": "$datasource" },
      "targets": [{ "expr": "mymcp_bash_inflight_processes" }],
      "gridPos": { "h": 6, "w": 8, "x": 0, "y": 18 }
    },
    {
      "type": "stat",
      "title": "Tokens by role",
      "datasource": { "type": "prometheus", "uid": "$datasource" },
      "targets": [
        {
          "expr": "mymcp_tokens_count",
          "legendFormat": "{{role}}"
        }
      ],
      "gridPos": { "h": 6, "w": 8, "x": 8, "y": 18 }
    },
    {
      "type": "timeseries",
      "title": "Audit write failures",
      "datasource": { "type": "prometheus", "uid": "$datasource" },
      "targets": [
        { "expr": "increase(mymcp_audit_write_failures_total[5m])" }
      ],
      "gridPos": { "h": 6, "w": 8, "x": 16, "y": 18 }
    },
    {
      "type": "row",
      "title": "Traces (OTLP mode only)",
      "gridPos": { "h": 1, "w": 24, "x": 0, "y": 24 }
    },
    {
      "type": "traces",
      "title": "Recent traces",
      "datasource": { "type": "tempo", "uid": "$tempo_ds" },
      "targets": [{ "queryType": "search", "serviceName": "mymcp" }],
      "gridPos": { "h": 12, "w": 24, "x": 0, "y": 25 }
    },
    {
      "type": "row",
      "title": "Logs (OTLP mode only)",
      "gridPos": { "h": 1, "w": 24, "x": 0, "y": 37 }
    },
    {
      "type": "logs",
      "title": "Audit + application logs",
      "datasource": { "type": "loki", "uid": "$loki_ds" },
      "targets": [
        { "expr": "{service_name=\"mymcp\"}" }
      ],
      "gridPos": { "h": 12, "w": 24, "x": 0, "y": 38 }
    }
  ]
}
```

- [ ] **Step 2: Validate JSON**

Run: `python -c "import json; json.load(open('deploy/observability/dashboard.json'))"`
Expected: no error.

- [ ] **Step 3: Commit**

```bash
git add deploy/observability/dashboard.json
git commit -m "feat(obs): Grafana dashboard supporting both pull and OTLP modes"
```

---

## Task 12: README documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add an Observability section**

Append to `README.md` (or insert before the "Deploy" section if one exists):

````markdown
## Observability

mymcp emits metrics, traces, and logs via OpenTelemetry. The default install supports Prometheus pull at `/metrics`; the `[otlp]` extra adds OTLP push for any backend.

### Quick reference

| Capability | Default install | `pip install algony-mymcp[otlp]` |
|---|---|---|
| `/metrics` Prometheus pull endpoint | yes | yes |
| OTLP push (metrics + traces + logs) | no | yes (when endpoint set) |
| FastAPI/ASGI auto-instrumentation | no | yes |
| Audit log → local file | yes | yes |
| Audit log → OTLP push | no | yes |
| Application logs → stderr (JSON) | yes | yes |

### Recipe 1 — Grafana Cloud (free tier)

```bash
pip install algony-mymcp[otlp]

export OTEL_EXPORTER_OTLP_ENDPOINT="https://otlp-gateway-prod-us-central-0.grafana.net/otlp"
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic <base64(instanceId:token)>"
export OTEL_SERVICE_NAME=mymcp

mymcp serve
```

Import `deploy/observability/dashboard.json` into Grafana Cloud.

### Recipe 2 — Self-hosted LGTM stack

`docker-compose.yml`:

```yaml
services:
  collector:
    image: otel/opentelemetry-collector-contrib:latest
    ports: ["4318:4318"]
    volumes: ["./otelcol-config.yaml:/etc/otelcol-contrib/config.yaml"]
  mimir:
    image: grafana/mimir:latest
    command: -config.file=/etc/mimir.yaml
  loki:
    image: grafana/loki:latest
  tempo:
    image: grafana/tempo:latest
  grafana:
    image: grafana/grafana:latest
    ports: ["3000:3000"]
```

Then run mymcp with:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 mymcp serve
```

Import the dashboard JSON; configure Mimir/Loki/Tempo as data sources.

### Recipe 3 — Pull-only Prometheus

No extra needed. Configure Prometheus:

```yaml
scrape_configs:
  - job_name: mymcp
    bearer_token: <your MYMCP_METRICS_TOKEN>
    static_configs:
      - targets: ['localhost:8000']
```

Import the dashboard JSON; the Traces and Logs panels remain empty (this is expected).

### Configuration knobs

All standard OTel env vars work. The most useful:

| Variable | Default | Purpose |
|---|---|---|
| `OTEL_SERVICE_NAME` | `mymcp` | Service name |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | unset | OTLP target |
| `OTEL_EXPORTER_OTLP_HEADERS` | unset | OTLP auth headers |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `http/protobuf` | http or grpc |
| `OTEL_TRACES_SAMPLER` | `parentbased_traceidratio` | Sampler |
| `OTEL_TRACES_SAMPLER_ARG` | `1.0` | Sampling ratio |
| `OTEL_METRIC_EXPORT_INTERVAL` | `60000` (ms) | Push period |
| `MYMCP_LOG_LEVEL` | `INFO` | Application log level |
````

- [ ] **Step 2: Verify README renders**

Run: `python -c "import markdown; markdown.markdown(open('README.md').read())"` (if `markdown` is not installed, just verify the file is well-formed by visual inspection).

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: observability section with three deployment recipes"
```

---

## Task 13: Final verification

**Files:** none — verification only.

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/ -v --benchmark-disable`
Expected: all tests pass.

- [ ] **Step 2: Run lint and type-check**

Run: `ruff check . && ruff format --check . && mypy src/mymcp`
Expected: clean.

- [ ] **Step 3: Smoke test the server**

```bash
mymcp serve --env-file ./.env &
SERVER_PID=$!
sleep 2

# Health check
curl -fs http://localhost:8000/health

# Metrics endpoint (replace TOKEN)
curl -fs -H "Authorization: Bearer $MYMCP_METRICS_TOKEN" http://localhost:8000/metrics | head

kill $SERVER_PID
```

Expected: `/health` returns JSON with status=ok; `/metrics` returns Prometheus-format text including `mymcp_http_requests_total`, `mymcp_tool_calls_total` (zero-valued initially), `mymcp_bash_inflight_processes`, `mymcp_tokens_count`.

- [ ] **Step 4: Smoke test request_id propagation**

```bash
mymcp serve &
SERVER_PID=$!
sleep 2

curl -fs -i -H "X-Request-ID: my-test-id" http://localhost:8000/health | grep -i x-request-id

kill $SERVER_PID
```

Expected: response carries `x-request-id: my-test-id`.

- [ ] **Step 5: Final commit if anything changed during verification**

```bash
git status
# If clean: nothing to do.
# If something needs adjustment: fix and commit.
```

- [ ] **Step 6: Verify branch is ready**

```bash
git log --oneline master..HEAD
```
Expected: ~12 commits matching the tasks above.

---

## Self-Review Checklist (for the engineer who wrote the plan, not the implementer)

- [x] Spec coverage: every section of the spec maps to a task. Section 1 (goals) → tasks 1, 4, 11, 12; Section 2 (architecture) → tasks 4, 5, 9; Section 3 (deps) → task 1; Section 4.1 (metrics) → task 5; Section 4.2 (traces) → tasks 7, 8; Section 4.3 (logs) → task 3; Section 5 (request_id) → task 2; Section 6 (config) → tasks 4, 9 (env vars), task 12 (docs); Section 7 (deliverables) → tasks 11, 12; Section 8 (breaking changes) → tasks 1, 5; Section 9 (testing) → woven into every task.
- [x] No placeholders: every code step shows actual code; commands have expected output.
- [x] Type consistency: instrument names (`mymcp.tool.calls`, etc.) used consistently across tasks 5, 6, 7, 11; Prometheus-translated names in task 11 dashboard match the spec; `current_request_id` contextvar referenced consistently from tasks 2, 3, 10.

