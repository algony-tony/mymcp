package setup

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// recorderSys returns a fakeSystem configured so recorderChecks gets past
// the "sidecar installed" and "sidecar active" checks and reaches the
// /metrics scrape, which is the part under test here.
func recorderSys() *fakeSystem {
	sys := newFakeSystem()
	sys.Paths["mymcp-recorder"] = "/usr/local/bin/mymcp-recorder"
	sys.Outputs["systemctl is-active mymcp-recorder"] = "active"
	return sys
}

func metricsServer(t *testing.T, body string) (host, port string) {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/metrics" {
			t.Errorf("unexpected path %q", r.URL.Path)
		}
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(body))
	}))
	t.Cleanup(srv.Close)
	return splitHostPort(t, srv.URL)
}

func backlogCheck(checks []Check) *Check { return findCheck(checks, "backlog") }

func TestRecorderChecksFlagsAStalledBacklog(t *testing.T) {
	stale := time.Now().Add(-20 * time.Minute).Unix() // > 2*300s default interval
	body := fmt.Sprintf("mymcp_recorder_pending_events 12\nmymcp_recorder_merge_last_attempt_timestamp %d\n", stale)
	host, port := metricsServer(t, body)

	checks := recorderChecks(recorderSys(), "", host, port)
	c := backlogCheck(checks)
	if c == nil || c.Severity != SevFail {
		t.Fatalf("backlog check = %+v, want SevFail for a stalled recorder", c)
	}
	if !strings.Contains(c.Detail, "12 events pending") {
		t.Errorf("detail = %q, want it to name the backlog", c.Detail)
	}
}

func TestRecorderChecksIdleServerIsNeverStale(t *testing.T) {
	// The project's documented rule: pending_events == 0 must never be
	// reported stale, even though merge_last_attempt_timestamp == 0 (never
	// attempted) looks arbitrarily old.
	body := "mymcp_recorder_pending_events 0\nmymcp_recorder_merge_last_attempt_timestamp 0\n"
	host, port := metricsServer(t, body)

	checks := recorderChecks(recorderSys(), "", host, port)
	c := backlogCheck(checks)
	if c == nil || c.Severity != SevOK {
		t.Fatalf("backlog check = %+v, want SevOK for an idle recorder with no backlog", c)
	}
}

func TestRecorderChecksFlagsAnOpenCircuitBreaker(t *testing.T) {
	body := "mymcp_recorder_circuit_open 1\nmymcp_recorder_pending_events 0\nmymcp_recorder_merge_last_attempt_timestamp 0\n"
	host, port := metricsServer(t, body)

	checks := recorderChecks(recorderSys(), "", host, port)
	c := findCheck(checks, "circuit breaker")
	if c == nil || c.Severity != SevFail {
		t.Fatalf("circuit breaker check = %+v, want SevFail when the breaker is open", c)
	}
}

func TestRecorderChecksWarnsWhenMetricsUnscrapable(t *testing.T) {
	sys := recorderSys()
	checks := recorderChecks(sys, "", "127.0.0.1", "1") // nothing listening
	c := backlogCheck(checks)
	if c == nil || c.Severity != SevWarn {
		t.Fatalf("backlog check = %+v, want SevWarn when /metrics cannot be scraped", c)
	}
}

func TestMetricValue(t *testing.T) {
	tests := []struct {
		name   string
		scrape string
		metric string
		want   float64
		wantOK bool
	}{
		{"plain line parses", "foo_metric 42\n", "foo_metric", 42, true},
		{"HELP comment does not match", "# HELP foo_metric total foo count\n", "foo_metric", 0, false},
		{"labelled series does not match unlabelled lookup",
			`foo{label="x"} 1` + "\n", "foo", 0, false},
		{"name that is a prefix of another metric does not collide",
			"foo_total 5\n", "foo", 0, false},
		{"missing metric is not found", "bar 1\n", "foo", 0, false},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got, ok := metricValue(tc.scrape, tc.metric)
			if ok != tc.wantOK {
				t.Fatalf("metricValue(%q) ok = %v, want %v", tc.scrape, ok, tc.wantOK)
			}
			if ok && got != tc.want {
				t.Fatalf("metricValue(%q) = %v, want %v", tc.scrape, got, tc.want)
			}
		})
	}
}
