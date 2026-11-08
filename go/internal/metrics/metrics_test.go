package metrics

import (
	"io"
	"net/http/httptest"
	"strings"
	"testing"
)

func scrape(t *testing.T, m *Metrics) string {
	t.Helper()
	req := httptest.NewRequest("GET", "/metrics", nil)
	rec := httptest.NewRecorder()
	m.Handler().ServeHTTP(rec, req)
	body, _ := io.ReadAll(rec.Result().Body)
	return string(body)
}

func TestMetricNamesPresent(t *testing.T) {
	inflight := 3
	m := New(func() float64 { return float64(inflight) })
	m.ToolCalls.WithLabelValues("read_file", "rw", "ok").Inc()
	m.ToolDuration.WithLabelValues("read_file").Observe(0.01)
	m.HTTPRequests.WithLabelValues("/mcp", "POST", "200").Inc()
	m.IncAuditFailure()

	out := scrape(t, m)
	for _, want := range []string{
		`mymcp_tool_calls_total{result="ok",role="rw",tool="read_file"} 1`,
		"mymcp_tool_duration_seconds_bucket",
		`mymcp_http_requests_total{method="POST",path="/mcp",status="200"} 1`,
		"mymcp_audit_write_failures_total 1",
		"mymcp_bash_inflight_processes 3",
	} {
		if !strings.Contains(out, want) {
			t.Fatalf("scrape missing %q\n---\n%s", want, out)
		}
	}
}

func TestRegistryIsIsolated(t *testing.T) {
	// A dedicated registry must not leak Go runtime collectors into the output.
	m := New(func() float64 { return 0 })
	out := scrape(t, m)
	if strings.Contains(out, "go_goroutines") || strings.Contains(out, "process_cpu_seconds_total") {
		t.Fatalf("registry should contain only mymcp_* metrics:\n%s", out)
	}
}

func TestAuthFailuresMetric(t *testing.T) {
	m := New(func() float64 { return 0 })
	m.AuthFailures.WithLabelValues("/mcp", "invalid_token").Inc()
	m.AuthFailures.WithLabelValues("/admin/tokens", "missing_admin_token").Inc()

	rec := httptest.NewRecorder()
	req := httptest.NewRequest("GET", "/metrics", nil)
	m.Handler().ServeHTTP(rec, req)

	body := rec.Body.String()
	if !strings.Contains(body, `mymcp_auth_failures_total{path="/mcp",reason="invalid_token"} 1`) {
		t.Errorf("metrics missing auth failure for /mcp: %s", body)
	}
	if !strings.Contains(body, `mymcp_auth_failures_total{path="/admin/tokens",reason="missing_admin_token"} 1`) {
		t.Errorf("metrics missing auth failure for /admin/tokens: %s", body)
	}
}
