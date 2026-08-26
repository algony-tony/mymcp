package setup

import (
	"bytes"
	"strings"
	"testing"
)

func TestSummaryNeverPrintsWildcardAsClientURL(t *testing.T) {
	p := DefaultPlan()
	p.Bind = "0.0.0.0"
	var buf bytes.Buffer
	Summary(p, ApplyOutcome{AdminToken: "tok_admin", ClientToken: "tok_client"}, &buf)
	s := buf.String()
	if strings.Contains(s, "http://0.0.0.0:") {
		t.Fatalf("0.0.0.0 is not usable in a client config:\n%s", s)
	}
	if !strings.Contains(s, "tok_client") {
		t.Error("client token must be shown")
	}
	if !strings.Contains(s, "tok_admin") {
		t.Error("admin token must be shown once")
	}
	if !strings.Contains(s, "claude mcp add") {
		t.Error("summary must include a pasteable client command")
	}
	if !strings.Contains(s, "mymcp doctor") {
		t.Error("summary must point at the next step")
	}
}

func TestSummaryKeepsExplicitBind(t *testing.T) {
	p := DefaultPlan()
	p.Bind = "127.0.0.1"
	var buf bytes.Buffer
	Summary(p, ApplyOutcome{AdminToken: "a", ClientToken: "c"}, &buf)
	if !strings.Contains(buf.String(), "http://127.0.0.1:8765/mcp") {
		t.Fatalf("explicit bind must be used verbatim:\n%s", buf.String())
	}
}

func TestPrimaryAddressResolvesWildcard(t *testing.T) {
	got := PrimaryAddress("0.0.0.0")
	if got == "0.0.0.0" || got == "" {
		t.Fatalf("PrimaryAddress(0.0.0.0) = %q, want a concrete address", got)
	}
}
