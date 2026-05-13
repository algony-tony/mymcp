# OpenTelemetry Observability — Design

Date: 2026-05-08
Status: Approved (brainstorming complete, awaiting implementation plan)

## 1. Goals & Non-Goals

### Goals

- Bring mymcp from partial observability (Prometheus metrics + audit log only) to full three-pillar coverage (metrics + logs + traces).
- Standardize on OpenTelemetry as the in-process emission layer so users can plug into any backend (self-hosted Prometheus/LGTM/SigNoz or SaaS such as Grafana Cloud / Datadog / New Relic / Honeycomb) without code changes.
- Preserve a zero-extra-dependency default install (`pip install algony-mymcp` still works without any observability backend).
- Keep audit logging as a first-class, file-based, always-on artifact (security/compliance requirement).
- Ship a Grafana dashboard alongside the code so observability is operational, not just instrumented. Alert rules are intentionally **not** shipped — see Section 7.

### Non-Goals

- Bundling an OTel Collector, Prometheus, or Grafana with the project. Backends remain the user's choice.
- Backwards compatibility with the current `prometheus_client`-based metric API. Existing user base is small enough to take the breaking change.
- Distributed tracing across multiple mymcp instances. Single-host systemd is the only deployment model targeted.
- Trace sampling tuning beyond exposing the standard `OTEL_TRACES_SAMPLER*` knobs.

## 2. Architecture

```
                     ┌────────────────────────────────────────┐
                     │   mymcp (FastAPI + uvicorn, systemd)   │
                     │                                        │
   tool 调用 ──▶ Meter API ─┐                                  │
   FastAPI 请求 ─▶ Tracer API ┼─▶ OTel SDK ─┬─▶ /metrics (Prometheus reader, always on)
   logging ─────▶ LoggingHandler ┘            ├─▶ OTLP exporter (only with [otlp] extra + endpoint)
                                              └─▶ stderr (JSON, always on → journald)
                     │                                        │
                     │  audit logger ─▶ RotatingFile (always on) │
                     │                ─▶ OTel LoggingHandler (only with [otlp] extra)
                     └────────────────────────────────────────┘
                                      │
                                      ▼ OTLP (optional)
                     ┌────────────────────────────────────────┐
                     │  User-chosen: Grafana Cloud / Datadog / │
                     │  self-hosted LGTM / SigNoz / Collector  │
                     └────────────────────────────────────────┘
```

Two operating modes:

1. **Default mode** (no extra installed, no OTLP endpoint configured): `/metrics` Prometheus pull endpoint, JSON logs to stderr (captured by journald), audit log to rotating file. Tracer API is callable but spans are dropped (zero-cost no-op exporter).
2. **OTLP mode** (`[otlp]` extra installed AND `OTEL_EXPORTER_OTLP_ENDPOINT` set): Default-mode outputs all remain; additionally metrics/traces/logs (including audit) are pushed to the configured OTLP endpoint, and FastAPI/ASGI/logging are auto-instrumented.

## 3. Dependency Layout

### Main dependencies (added to `pyproject.toml`)

- `opentelemetry-api`
- `opentelemetry-sdk`
- `opentelemetry-exporter-prometheus`

Approximate added install size: ~2 MB.

### `[otlp]` optional extra

- `opentelemetry-exporter-otlp-proto-http`
- `opentelemetry-instrumentation-fastapi`
- `opentelemetry-instrumentation-asgi`
- `opentelemetry-instrumentation-logging`

Install via `pip install algony-mymcp[otlp]`.

### Removed

- `prometheus-client` (functionality replaced by OTel `PrometheusMetricReader`).

### Why this split

- OTel core SDK in main deps means `/metrics` and the unified emission API are always available — there is no "first install OTel" step before basic monitoring works.
- OTLP exporter and auto-instrumentation packages account for most of the install weight and are only useful when actually pushing to a remote backend, so they are gated behind the extra.
- The extra is named `[otlp]` (not `[otel]`) because OTel itself is core; the extra specifically enables OTLP push and auto-instrumentation.

## 4. Three Pillars

### 4.1 Metrics

`src/mymcp/metrics.py` is rewritten using the OTel Meter API. The existing three metric series are preserved with equivalent names and labels:

| Current name (prom_client) | New name (OTel) | Labels |
|---|---|---|
| `mymcp_tool_calls_total` | `mymcp.tool.calls` (counter) | `tool`, `role`, `result` |
| `mymcp_tool_duration_seconds` | `mymcp.tool.duration` (histogram, seconds, same buckets) | `tool` |
| `mymcp_http_requests_total` | `mymcp.http.requests` (counter) | `path`, `method`, `status` |

Note: when exposed through `PrometheusMetricReader`, OTel's dot-namespaced names are translated to underscore form for Prometheus compatibility, so scrape consumers see `mymcp_tool_calls_total` etc. — no Grafana dashboard rewrites required for users who keep using pull mode.

Newly added saturation metrics (closing the Google SRE four-golden-signals gap):

- `mymcp.bash.inflight_processes` (up-down counter / gauge): live count of tracked bash subprocesses in the weakref set.
- `mymcp.tokens.count` (gauge, label `role`): size of the token store, broken down by role.
- `mymcp.audit.write_failures` (counter): incremented when audit write encounters an exception (catches "audit silently broken" failure mode).

`/metrics` endpoint is served by `PrometheusMetricReader` registered as one of the meter providers' readers. Route path remains `/metrics`.

### 4.2 Traces

- `FastAPIInstrumentor.instrument_app(app)` covers HTTP request-level spans automatically.
- `mymcp.mcp_server.dispatch_tool` is wrapped in a manual span with attributes: `tool.name`, `token.role`, `tool.result` (`success`|`error`|`denied`), `error.code` when applicable.
- `mymcp.tools.bash.run_bash_execute` creates a child span with attributes: `bash.timeout_sec`, `bash.exit_code`, `bash.timed_out`, `bash.output_truncated`, `bash.stdout_bytes`, `bash.stderr_bytes`. Subprocess wall-clock duration is the span duration.
- `mymcp.transfer.endpoints` adds child spans for upload/download stages of large file transfer (chunk read, signed-url validation, write).
- Sampler defaults to `parentbased_traceidratio` at 1.0 (record everything in default config; users tune via env var).

When the `[otlp]` extra is not installed, the global tracer provider is left in its API no-op state, so manual span calls compile down to negligible work.

### 4.3 Logs

Application logging (everything except `mymcp.audit`):

- `cli.py`'s current logging configuration is replaced. All `mymcp.*` loggers emit JSON-formatted records to stderr. journald (the systemd unit's default stdout/stderr sink) becomes the local log source of truth.
- Each record carries `trace_id`, `span_id`, and `request_id` fields when available (auto-injected by `opentelemetry-instrumentation-logging` when `[otlp]` is installed; injected manually via a logging filter otherwise so the field is always present).
- When `[otlp]` is installed AND `OTEL_EXPORTER_OTLP_ENDPOINT` is set, an OTel `LoggingHandler` is additionally attached at root level so the same records are pushed to the OTLP endpoint.
- No application-log files are written by the project (current state already; documented here so it remains an explicit non-goal — no `RotatingFileHandler` is to be added for application logs).

Audit logging (`mymcp.audit`):

- The `RotatingFileHandler` configured in `audit.py` is **preserved unchanged**. Local audit file remains source of truth for compliance.
- When `[otlp]` is installed, an OTel `LoggingHandler` is **additionally** attached to the `mymcp.audit` logger. Same records flow to both file and remote.
- Audit records gain `trace_id` / `span_id` / `request_id` when available, supporting "find the trace for this audited tool call" workflows in the backend UI.

## 5. Request ID Correlation

A new ASGI middleware is added before `McpAuthMiddleware` in the middleware stack:

- Reads `X-Request-ID` header from incoming requests; generates a UUID4 if absent.
- Echoes `X-Request-ID` on the response.
- Stores the value in a contextvar (`_current_request_id`).
- A logging filter reads the contextvar and adds `request_id` to every log record (audit and application).
- Combined with OTel-injected `trace_id` / `span_id`, this produces a complete correlation chain: external request id → distributed trace → individual log lines.

## 6. Configuration

All knobs use OpenTelemetry standard environment variables. No project-specific aliases are introduced.

| Variable | Default | Purpose |
|---|---|---|
| `OTEL_SDK_DISABLED` | `false` | Master switch; when `true` all OTel exporters/instrumentation are no-ops. |
| `OTEL_SERVICE_NAME` | `mymcp` | Service name reported on every signal. |
| `OTEL_RESOURCE_ATTRIBUTES` | auto-injects `service.version=<package version>`, `service.namespace=mymcp` | Additional resource attributes; user-provided values are merged. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | unset | When set (and `[otlp]` extra installed), enables OTLP push for all three signals. |
| `OTEL_EXPORTER_OTLP_HEADERS` | unset | Backend auth headers, e.g., `Authorization=Basic ...`. |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `http/protobuf` | Protocol; HTTP chosen as the default because it traverses proxies and TLS termination more reliably than gRPC. |
| `OTEL_TRACES_SAMPLER` | `parentbased_traceidratio` | Sampler. |
| `OTEL_TRACES_SAMPLER_ARG` | `1.0` | Sampling ratio. |
| `OTEL_METRIC_EXPORT_INTERVAL` | `60000` (60 s) | OTLP metric push period. |
| `OTEL_LOGS_EXPORTER` | `otlp` (when extra installed and endpoint set) else `none` | Which log exporter to wire. |

If `[otlp]` is not installed, all `OTEL_EXPORTER_OTLP_*` variables are silently ignored (the exporter classes simply cannot be imported and initialization is skipped). Pull metrics via `/metrics` continues to work.

## 7. Operational Deliverables

Beyond code instrumentation, the design includes:

- `deploy/observability/dashboard.json` — Grafana dashboard JSON, designed to work in **both** deployment modes from a single file:
  - **Metrics row** (always populated): golden signals (latency, traffic, errors), bash subprocess saturation, token store size, audit event rate. Queries are PromQL against metric series whose names are identical whether they arrived via Prometheus scrape of `/metrics` or via OTLP push into a Prometheus-compatible store (Mimir, Grafana Cloud Metrics, etc.). The user only needs to point Grafana at the right data source.
  - **Traces row** (populated in OTLP mode only): Tempo/Jaeger panels linked from metric series via `trace_id` exemplars. In pull-only deployments these panels render Grafana's empty-state ("no data source configured") rather than failing.
  - **Logs row** (populated in OTLP mode only): Loki/equivalent panels for audit and application logs, filterable by `request_id` / `trace_id`. Same empty-state behavior in pull-only mode.
- `README.md` observability section with three end-to-end deployment recipes:
  1. Grafana Cloud (free tier): install `[otlp]`, set two env vars, done.
  2. Self-hosted LGTM stack: docker-compose snippet for Mimir + Loki + Tempo + Grafana, with mymcp pointed at the Collector.
  3. Pull-only Prometheus: existing scrape, no extra needed.

### Why no alert rules

Alerting rules are intentionally **not** shipped with the project. Each operator has different SLOs, notification channels, on-call rotations, and inhibition strategies; a one-size-fits-all `alerts.yml` would either be too noisy (firing for everyone's normal-but-different baselines) or too quiet (thresholds set so wide that real problems pass through). The dashboard's PromQL queries are the better starting point — operators copy the queries they care about into their own alerting setup with thresholds appropriate to their environment.

## 8. Breaking Changes

- `prometheus-client` removed from dependency list. Anyone importing `prometheus_client` from inside mymcp internals (none should, but flagging) breaks.
- Application log files (anything written by `mymcp.cli`) are no longer produced. Users currently tailing those files must switch to `journalctl -u mymcp`.
- `mymcp.metrics` module's public API (`TOOL_CALLS`, `TOOL_DURATION`, `HTTP_REQUESTS` symbols) is rewritten in OTel terms. Call sites in the project are updated; external code importing these symbols is not supported.
- Audit log file format and rotation behavior are unchanged.
- `/metrics` endpoint URL, content-type, and metric series names (in their Prometheus-translated form) are unchanged.

## 9. Testing

- Existing tests continue to run with no observability-related changes; metrics call-site updates are mechanical and covered by current assertions on tool result shape.
- New `tests/test_otel.py`:
  - Verifies main install (without `[otlp]`) initializes OTel SDK, exposes `/metrics`, and accepts (no-op) tracer/span calls without raising.
  - Verifies that with `[otlp]` extra simulated and endpoint set, OTLP exporters are constructed and registered.
  - Verifies graceful degradation when extra is absent but OTLP env vars are present (warning logged, no crash).
- New `tests/test_request_id.py`:
  - Middleware generates a UUID when no header is provided.
  - Middleware preserves a client-supplied `X-Request-ID`.
  - `request_id` appears in audit log records and application log records produced during the request.
  - Response carries echoed `X-Request-ID`.
- New `tests/test_traces.py` using OTel's `InMemorySpanExporter`:
  - `dispatch_tool` produces a span with expected attributes for both success and error paths.
  - `run_bash_execute` produces a child span with `bash.exit_code` and `bash.timed_out` attributes.
  - HTTP request span is parent of dispatch span when auto-instrumentation is active.
- Saturation metric tests: assert `mymcp.bash.inflight_processes` increments on bash spawn and decrements on completion.

## 10. Open Questions

None at design close. Implementation plan will revisit:

- Exact module boundaries inside `mymcp.observability` (likely a new package replacing `metrics.py` plus a `tracing.py` and `logging.py` module).
- Whether to split saturation metric updates into a periodic callback (OTel async gauge) vs. event-driven up-down counter — depends on which gives cleaner code in `tools/bash.py`.
