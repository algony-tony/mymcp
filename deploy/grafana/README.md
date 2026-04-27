# Grafana dashboard

`mymcp-dashboard.json` is a portable Grafana dashboard for the metrics exposed at
`/metrics`. It uses a `${datasource}` template variable so the same JSON works
on any Grafana that has at least one Prometheus datasource.

## What it shows

Four sections, ten data panels:

- **Overview Stats** — total calls (over the selected time range), error rate, p95 latency, service `up`
- **Tool Call Rate** — per-tool rate split by `result` (ok/error)
- **Latency Percentiles** — p50/p95/p99 of `mymcp_tool_duration_seconds` per tool
- **HTTP Requests** — request rate by method, path, and status
- **Process Health** — RSS, CPU, open file descriptors

## Prometheus scrape config

`/metrics` requires the bearer token from `MCP_METRICS_TOKEN`. Example scrape job:

```yaml
scrape_configs:
  - job_name: mymcp
    metrics_path: /metrics
    scheme: http   # use https if mymcp is behind TLS
    authorization:
      type: Bearer
      credentials: <MCP_METRICS_TOKEN value>
    static_configs:
      - targets: ['mymcp-host:8000']
```

The `job` label must match `mymcp.*` (the dashboard filters on
`job=~"mymcp.*"`). `mymcp` itself works.

## Import options

### Option 1: UI import

1. Grafana → Dashboards → New → Import
2. Upload `mymcp-dashboard.json`
3. Pick your Prometheus datasource when prompted
4. Save

### Option 2: File-based provisioning

Drop the JSON next to a provider config:

```
/etc/grafana/provisioning/dashboards/mymcp.yaml
/var/lib/grafana/dashboards/mymcp-dashboard.json
```

`mymcp.yaml`:

```yaml
apiVersion: 1
providers:
  - name: 'mymcp'
    orgId: 1
    folder: ''
    type: file
    disableDeletion: false
    editable: true
    allowUiUpdates: true
    options:
      path: /var/lib/grafana/dashboards
```

Reload Grafana (`systemctl reload grafana-server`) or wait for the
provisioner's poll interval (10 s by default).

## Verification / how to test

After importing:

1. Open the dashboard. The Prometheus datasource selector should default to
   your existing datasource. The `Instance` selector should populate from
   `up{job=~"mymcp.*"}`.
2. **Total Calls** uses `[$__range]`, so changing the time picker (1h → 12h →
   24h) must change the value. If it doesn't, the time variable didn't
   resolve — verify Grafana version is ≥ 8.
3. **Service Status** should read `UP` (green); if `DOWN`, the scrape target
   is unreachable or the bearer token is wrong.
4. **Tool Call Rate** and **Latency Percentiles** populate after a few tool
   calls have been made. With zero traffic the panels are empty — call any
   tool to trigger data.
5. Sanity-check the underlying metrics exist:

   ```bash
   curl -s -H "Authorization: Bearer $MCP_METRICS_TOKEN" http://mymcp-host:8000/metrics | \
     grep -E '^(mymcp_|process_resident_memory_bytes|process_cpu_seconds_total|process_open_fds)'
   ```

   Each of `mymcp_tool_calls_total`, `mymcp_tool_duration_seconds_bucket`,
   `mymcp_http_requests_total`, plus the three `process_*` series should
   appear.

## Editing

If you tweak the dashboard in Grafana's UI, export it as JSON
(Dashboard settings → JSON Model), strip the `id` and any
hardcoded datasource UID, and overwrite this file before committing.
