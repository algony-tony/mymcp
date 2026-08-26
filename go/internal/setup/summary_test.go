package setup

import (
	"bytes"
	neturl "net/url"
	"strings"
	"testing"
)

func TestSummaryNeverPrintsWildcardAsClientURL(t *testing.T) {
	p := DefaultPlan()
	p.Bind = "0.0.0.0"
	var buf bytes.Buffer
	Summary(p, ApplyOutcome{AdminToken: "tok_admin", ClientToken: "tok_client", ClientRole: p.ClientRole}, &buf)
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
	Summary(p, ApplyOutcome{AdminToken: "a", ClientToken: "c", ClientRole: p.ClientRole}, &buf)
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

func TestSummaryBracketsAnIPv6Address(t *testing.T) {
	// An unbracketed IPv6 literal ("http://::1:8765/mcp") is unparseable by
	// every HTTP client — the URL must survive url.Parse.
	p := DefaultPlan()
	p.Bind = "::1"
	var buf bytes.Buffer
	Summary(p, ApplyOutcome{AdminToken: "a", ClientToken: "c", ClientRole: p.ClientRole}, &buf)
	s := buf.String()
	if !strings.Contains(s, "http://[::1]:8765/mcp") {
		t.Fatalf("IPv6 host must be bracketed:\n%s", s)
	}
	if strings.Contains(s, "http://::1:") {
		t.Fatalf("unbracketed IPv6 literal present:\n%s", s)
	}
}

func TestSummaryPrintsTheStoredRoleAndFlagsAMismatch(t *testing.T) {
	p := DefaultPlan()
	p.ClientRole = "ro" // requested on this run
	var buf bytes.Buffer
	Summary(p, ApplyOutcome{AdminToken: "a", ClientToken: "c", ClientRole: "rw"}, &buf)
	s := buf.String()
	if !strings.Contains(s, "(rw, name=default)") {
		t.Fatalf("summary must print the STORED role (rw), not the requested one (ro):\n%s", s)
	}
	if !strings.Contains(s, "role is rw, not ro") {
		t.Fatalf("summary must call out the role mismatch:\n%s", s)
	}
}

func TestSummaryURLAlwaysParses(t *testing.T) {
	for _, bind := range []string{"127.0.0.1", "::1", "10.0.0.5"} {
		p := DefaultPlan()
		p.Bind = bind
		var buf bytes.Buffer
		Summary(p, ApplyOutcome{AdminToken: "a", ClientToken: "c", ClientRole: p.ClientRole}, &buf)
		for _, line := range strings.Split(buf.String(), "\n") {
			i := strings.Index(line, "http://")
			if i < 0 {
				continue
			}
			raw := strings.Fields(line[i:])[0]
			if _, err := neturl.Parse(raw); err != nil {
				t.Errorf("bind %s produced an unparseable URL %q: %v", bind, raw, err)
			}
		}
	}
}
