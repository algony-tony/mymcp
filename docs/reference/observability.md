# Observability & Metrics Reference

`algony-mymcp` provides OpenTelemetry metrics, distributed tracing, and structured logging. The core Go server natively serves a Prometheus pull endpoint at `/metrics`. When running with the Python sidecar or with OTLP configuration enabled, metrics and traces can be pushed to remote collectors.

---

## Observability Capabilities Matrix

| Capability | Core Server (default) | With `pip install "algony-mymcp[otlp]"` |
|---|---|---|
| `/metrics` Prometheus pull endpoint | Yes (Bearer auth) | Yes |
| OTLP push (metrics + traces + logs) | No | Yes (when endpoint configured) |
| FastAPI/ASGI auto-instrumentation | N/A (Go binary) | Yes (for Python sidecar endpoints) |
| Audit log to local JSONL file | Yes | Yes |
| Application logs to stderr (JSON) | Yes | Yes |

---

## Deployment Recipes

### Recipe 1: Grafana Cloud (Free Tier)

Export telemetry to Grafana Cloud via OTLP:

```bash
pip install "algony-mymcp[otlp]"

export OTEL_EXPORTER_OTLP_ENDPOINT="https://otlp-gateway-prod-us-central-0.grafana.net/otlp"
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic <base64(instanceId:token)>"
export OTEL_SERVICE_NAME="mymcp"

mymcp serve
```

Import pre-configured dashboards:
- [mymcp-dashboard.json](../../deploy/grafana/mymcp-dashboard.json): Core server metrics, tool invocations, and Recorder Health.
- [mymcp-logs-dashboard.json](../../deploy/grafana/mymcp-logs-dashboard.json): Log exploration (Loki) and Trace correlation (Tempo).

---

### Recipe 2: Self-Hosted LGTM Stack (Loki, Grafana, Tempo, Mimir)

A complete local observability stack using `docker-compose.yml`:

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

Run `mymcp` pointing to the OpenTelemetry Collector:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT="http://localhost:4318" mymcp serve
```

---

### Recipe 3: Prometheus Pull Only

Configure standard Prometheus scraping without any extra dependencies:

```yaml
scrape_configs:
  - job_name: mymcp
    bearer_token: <MYMCP_METRICS_TOKEN>
    static_configs:
      - targets: ['localhost:8765']
```

---

## Core Server Metrics

### Audit Log Integrity

| Metric | Type | Description |
|---|---|---|
| `mymcp_audit_write_failures_total` | counter | Count of failed audit log write operations (disk full, permissions, rotation locks). Rejections return `InternalError` (500) to clients. |

**Recommended SOC Alert Rule (PromQL):**
```promql
rate(mymcp_audit_write_failures_total[5m]) > 0
```

### Security & Authentication Failures

| Metric | Type | Labels | Description |
|---|---|---|---|
| `mymcp_auth_failures_total` | counter | `path`, `reason` | Total count of rejected authentication attempts across `/mcp`, `/admin/*`, and `/metrics`. |

Labels:
- `path`: Request path where failure occurred (e.g. `/mcp`, `/admin/tokens`, `/metrics`).
- `reason`: Classification of failure (`missing_token`, `invalid_token`, `missing_admin_token`, `invalid_admin_token`, `metrics_unauthorized`).

**Recommended Brute-Force Alert Rule (PromQL):**
```promql
rate(mymcp_auth_failures_total[5m]) > 0.5
```

---

## Structured Authentication Failure Logging

To facilitate integration with intrusion detection systems (IDS), SIEM, and ban tools (e.g., `fail2ban`), every authentication rejection emits a single structured line to standard error (`stderr` / `systemd-journald`):

```text
[mymcp] [AUTH_FAIL] ip=<client_ip> path=<path> reason=<reason> req_id=<req_id>
```

- `ip`: Real client IP (extracted via direct TCP peer or trusted reverse proxy `X-Forwarded-For`).
- `path`: Target URL path (e.g., `/mcp`).
- `reason`: Machine-readable reason code (`missing_token`, `invalid_token`, etc.).
- `req_id`: Unique request identifier.

> **Zero Token Disclosure**: The log entry strictly omits raw token values or partial fragments to prevent credential exposure in log aggregation pipelines.

### Example fail2ban Filter Regex
```ini
[Definition]
failregex = ^.*\[mymcp\] \[AUTH_FAIL\] ip=<HOST> path=.*$
ignoreregex =
```

---

## Recorder Metrics (Sidecar)

When the optional `mymcp-recorder` sidecar is enabled, the following series are exported on `/metrics`:

| Metric | Type | Labels | Description / Usage |
|---|---|---|---|
| `mymcp_recorder_merge_cycles_total` | counter | `reason` | Count of completed merge cycles. Reasons: `success`, `no_events`, `bootstrap_required`, `llm_error`, `max_tokens`, `empty`, `unparseable`, `schema_invalid`, `apply_error`. |
| `mymcp_recorder_merge_duration_seconds` | histogram | `reason` | Wall-clock latency per merge cycle (including LLM calls). |
| `mymcp_recorder_llm_calls_total` | counter | `phase`, `result` | HTTP-level LLM call outcomes (`success`, `error`). |
| `mymcp_recorder_llm_tokens_total` | counter | `phase`, `direction` | LLM token consumption by direction (`input` vs `output`). |
| `mymcp_recorder_pending_events` | gauge | – | Count of unmerged mutating audit events past the current cursor. |
| `mymcp_recorder_merge_last_attempt_timestamp` | gauge | – | Unix timestamp (seconds) of the last merge attempt. Does not advance on idle ticks. |
| `mymcp_recorder_merge_last_success_timestamp` | gauge | – | Unix timestamp (seconds) of the last successful merge. |
| `mymcp_recorder_circuit_open` | gauge | – | Set to `1` when consecutive failures exceed threshold; `0` when operational. |
| `mymcp_recorder_event_loss_total` | counter | – | Unprocessed audit records lost due to log rotation exceeding cursor retention. |
| `mymcp_recorder_events_consumed_total` | counter | `tool` | Total mutating events successfully folded into the overview, labeled by tool. |

### Recommended Staleness Alert (PromQL)

To detect a stuck or failing recorder without false positives on idle servers:

```promql
( mymcp_recorder_pending_events > 0
  AND time() - mymcp_recorder_merge_last_attempt_timestamp > 1800 )
OR mymcp_recorder_circuit_open == 1
```

---

## OpenTelemetry Configuration Reference

| Environment Variable | Default | Description |
|---|---|---|
| `OTEL_SERVICE_NAME` | `mymcp` | Logical service name for telemetry |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | *(unset)* | OTLP collector endpoint URL |
| `OTEL_EXPORTER_OTLP_HEADERS` | *(unset)* | Key-value authentication headers |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `http/protobuf` | Protocol format (`http/protobuf` or `grpc`) |
| `OTEL_TRACES_SAMPLER` | `parentbased_traceidratio` | Sampling strategy |
| `OTEL_TRACES_SAMPLER_ARG` | `1.0` | Sampling ratio (0.0 to 1.0) |
| `OTEL_METRIC_EXPORT_INTERVAL` | `60000` | Metric push interval in milliseconds (60s) |
| `MYMCP_LOG_LEVEL` | `INFO` | Server logging level (`DEBUG`, `INFO`, `WARN`, `ERROR`) |
