// Package metrics exposes the mymcp_* Prometheus metrics on a dedicated
// registry. Names and label sets are identical to the Python core's
// OTel→Prometheus output (src/mymcp/observability/instruments.py) so the shipped
// Grafana dashboards keep working. Histogram buckets need not match OTel.
package metrics

import (
	"net/http"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

type Metrics struct {
	registry      *prometheus.Registry
	ToolCalls     *prometheus.CounterVec
	ToolDuration  *prometheus.HistogramVec
	HTTPRequests  *prometheus.CounterVec
	auditFailures prometheus.Counter
}

// New builds the registry. inflight is a callback returning the live bash
// subprocess count (backs the mymcp_bash_inflight_processes gauge); pass a
// closure over tools.InflightCount so this package stays free of a tools import.
func New(inflight func() float64) *Metrics {
	reg := prometheus.NewRegistry()
	m := &Metrics{
		registry: reg,
		ToolCalls: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "mymcp_tool_calls_total", Help: "Total MCP tool calls",
		}, []string{"tool", "role", "result"}),
		ToolDuration: prometheus.NewHistogramVec(prometheus.HistogramOpts{
			Name: "mymcp_tool_duration_seconds", Help: "MCP tool call duration",
			Buckets: prometheus.DefBuckets,
		}, []string{"tool"}),
		HTTPRequests: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "mymcp_http_requests_total", Help: "Total HTTP requests",
		}, []string{"path", "method", "status"}),
		auditFailures: prometheus.NewCounter(prometheus.CounterOpts{
			Name: "mymcp_audit_write_failures_total", Help: "Audit log write failures",
		}),
	}
	reg.MustRegister(m.ToolCalls, m.ToolDuration, m.HTTPRequests, m.auditFailures)
	reg.MustRegister(prometheus.NewGaugeFunc(prometheus.GaugeOpts{
		Name: "mymcp_bash_inflight_processes",
		Help: "Live count of tracked bash subprocesses",
	}, inflight))
	return m
}

// Handler serves the metrics in Prometheus text format.
func (m *Metrics) Handler() http.Handler {
	return promhttp.HandlerFor(m.registry, promhttp.HandlerOpts{})
}

// IncAuditFailure bumps mymcp_audit_write_failures_total.
func (m *Metrics) IncAuditFailure() { m.auditFailures.Inc() }
