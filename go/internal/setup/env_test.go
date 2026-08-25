package setup

import (
	"strings"
	"testing"
)

func TestRenderEnvContainsExplicitAuditTrue(t *testing.T) {
	p := DefaultPlan()
	out := RenderEnv(p, "tok_admin")
	// config.go defaults MYMCP_AUDIT_ENABLED to false, so init must be explicit.
	if !strings.Contains(out, "\nMYMCP_AUDIT_ENABLED=true\n") {
		t.Fatalf("rendered .env must set MYMCP_AUDIT_ENABLED=true explicitly:\n%s", out)
	}
	for _, want := range []string{
		"MYMCP_HOST=0.0.0.0",
		"MYMCP_PORT=8765",
		"MYMCP_ADMIN_TOKEN=tok_admin",
		"MYMCP_TOKEN_FILE=/etc/mymcp/tokens.json",
		"MYMCP_AUDIT_LOG_DIR=/var/log/mymcp",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q", want)
		}
	}
	if !strings.Contains(out, "# --- Server ---") {
		t.Error("rendered .env must keep the commented section headers")
	}
}

func TestMergeReplacesOwnedKeyInPlace(t *testing.T) {
	existing := "# my note\nMYMCP_PORT=9999\nMYMCP_PROTECTED_PATHS=/root/.ssh\n"
	got := MergeEnv(existing, map[string]string{"MYMCP_PORT": "8765"})
	if !strings.Contains(got, "MYMCP_PORT=8765") {
		t.Fatalf("owned key not replaced:\n%s", got)
	}
	if strings.Contains(got, "9999") {
		t.Fatalf("old value survived:\n%s", got)
	}
	if !strings.Contains(got, "# my note") {
		t.Error("user comment must be preserved")
	}
	if !strings.Contains(got, "MYMCP_PROTECTED_PATHS=/root/.ssh") {
		t.Error("unowned user key must be preserved verbatim")
	}
}

func TestMergeAppendsMissingKeysUnderMarkerOnce(t *testing.T) {
	first := MergeEnv("MYMCP_PORT=8765\n", map[string]string{"MYMCP_HOST": "127.0.0.1"})
	if strings.Count(first, envMarker) != 1 {
		t.Fatalf("marker should appear once:\n%s", first)
	}
	second := MergeEnv(first, map[string]string{"MYMCP_METRICS_TOKEN": "tok_m"})
	if strings.Count(second, envMarker) != 1 {
		t.Fatalf("re-run must reuse the marker, not add another:\n%s", second)
	}
	if !strings.Contains(second, "MYMCP_HOST=127.0.0.1") ||
		!strings.Contains(second, "MYMCP_METRICS_TOKEN=tok_m") {
		t.Fatalf("both appended keys must survive:\n%s", second)
	}
}

func TestMergeIgnoresCommentedOutKeys(t *testing.T) {
	// .env.example ships keys commented out; those must not be treated as present.
	got := MergeEnv("# MYMCP_PORT=8765\n", map[string]string{"MYMCP_PORT": "9000"})
	if !strings.Contains(got, "\nMYMCP_PORT=9000") {
		t.Fatalf("commented key must not satisfy the owned key:\n%s", got)
	}
	if !strings.Contains(got, "# MYMCP_PORT=8765") {
		t.Fatalf("the comment itself must be preserved:\n%s", got)
	}
}

func TestExistingAdminTokenIsFoundAndCommentsIgnored(t *testing.T) {
	if got := ExistingAdminToken("# MYMCP_ADMIN_TOKEN=tok_old\n"); got != "" {
		t.Fatalf("commented admin token must not count, got %q", got)
	}
	if got := ExistingAdminToken("MYMCP_ADMIN_TOKEN=tok_live\n"); got != "tok_live" {
		t.Fatalf("got %q, want tok_live", got)
	}
	if got := ExistingAdminToken("MYMCP_ADMIN_TOKEN=\n"); got != "" {
		t.Fatalf("empty value must count as absent, got %q", got)
	}
}
