package tools

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestServerOverviewAbsentPointsAtSidecar(t *testing.T) {
	d := testDeps(t)
	d.Cfg.RecorderDataDir = t.TempDir() // no overview/overview.md
	res := ServerOverview(d)
	if res["success"] != false || res["error"] != "RecorderDisabled" {
		t.Fatalf("res = %v", res)
	}
	msg, _ := res["message"].(string)
	if !strings.Contains(msg, "systemctl status mymcp-recorder") {
		t.Fatalf("message should name the sidecar, got %q", msg)
	}
	if strings.Contains(msg, "MYMCP_RECORDER_ENABLED") {
		t.Fatalf("absent-file message must not blame the config flag, got %q", msg)
	}
}

func TestServerOverviewUnreadableReportsReadFailure(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("root bypasses file permissions")
	}
	dir := t.TempDir()
	ov := filepath.Join(dir, "overview")
	if err := os.MkdirAll(ov, 0o755); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(ov, "overview.md")
	if err := os.WriteFile(path, []byte("# Server\n"), 0o000); err != nil {
		t.Fatal(err)
	}
	d := testDeps(t)
	d.Cfg.RecorderDataDir = dir
	res := ServerOverview(d)
	if res["success"] != false || res["error"] != "RecorderDisabled" {
		t.Fatalf("res = %v", res)
	}
	msg, _ := res["message"].(string)
	if !strings.Contains(msg, path) {
		t.Fatalf("unreadable message should name the path, got %q", msg)
	}
	if strings.Contains(msg, "systemctl") {
		t.Fatalf("unreadable message must not claim the sidecar never ran, got %q", msg)
	}
}

func TestServerOverviewPresentReturnsContent(t *testing.T) {
	d := testDeps(t)
	dir := t.TempDir()
	d.Cfg.RecorderDataDir = dir
	ov := filepath.Join(dir, "overview")
	os.MkdirAll(ov, 0o755)
	os.WriteFile(filepath.Join(ov, "overview.md"), []byte("# Server\nstuff\n"), 0o644)
	res := ServerOverview(d)
	if res["success"] != true || res["overview"] != "# Server\nstuff\n" {
		t.Fatalf("res = %v", res)
	}
}

func TestServerOverviewReportsFreshnessFields(t *testing.T) {
	d := testDeps(t)
	dataDir, logDir := t.TempDir(), t.TempDir()
	d.Cfg.RecorderDataDir, d.Cfg.AuditLogDir = dataDir, logDir
	seedOverview(t, dataDir, overviewHeader)

	res := ServerOverview(d)
	if res["success"] != true {
		t.Fatalf("res = %v", res)
	}
	// last_updated is always RFC3339, regardless of the header's own format
	// ("2026-07-13 02:08 UTC" here) — Finding 3: a structured consumer must
	// see one consistent format. Compute the expected instant from the same
	// header value overviewHeader embeds, rather than pasting a string.
	wantInstant := time.Date(2026, 7, 13, 2, 8, 0, 0, time.UTC)
	if res["last_updated"] != wantInstant.Format(time.RFC3339) {
		t.Fatalf("last_updated = %v, want %v", res["last_updated"], wantInstant.Format(time.RFC3339))
	}
	if res["pending_events"] != 0 {
		t.Fatalf("pending_events = %v, want 0", res["pending_events"])
	}
	if res["stale"] != false {
		t.Fatalf("stale = %v, want false", res["stale"])
	}
	if body, _ := res["overview"].(string); !strings.HasPrefix(body, "# Server Overview") {
		t.Fatalf("fresh overview must not be prefixed with a banner: %q", body)
	}
}

func TestServerOverviewPrefixesBannerWhenStale(t *testing.T) {
	d := testDeps(t)
	dataDir, logDir := t.TempDir(), t.TempDir()
	d.Cfg.RecorderDataDir, d.Cfg.AuditLogDir = dataDir, logDir
	d.Cfg.RecorderMergeIntervalSec = 300
	// Header is dated 2026-07-13; the test clock is now, so this is months old.
	seedOverview(t, dataDir, overviewHeader)
	writeAudit(t, logDir, auditLine("write_file", "ok"), auditLine("bash_execute", "ok"))

	res := ServerOverview(d)
	if res["stale"] != true {
		t.Fatalf("stale = %v, want true", res["stale"])
	}
	if res["pending_events"] != 2 {
		t.Fatalf("pending_events = %v, want 2", res["pending_events"])
	}
	body, _ := res["overview"].(string)
	if !strings.HasPrefix(body, "_⚠️") {
		t.Fatalf("stale overview must lead with a banner, got %q", body)
	}
	if !strings.Contains(body, "2 events pending") {
		t.Fatalf("banner should state the backlog, got %q", body)
	}
	if !strings.Contains(body, "systemctl status mymcp-recorder") {
		t.Fatalf("banner should state the remedy, got %q", body)
	}
	if !strings.Contains(body, "# Server Overview") {
		t.Fatalf("banner must prefix, not replace, the overview: %q", body)
	}
}
