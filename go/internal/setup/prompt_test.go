package setup

import (
	"bytes"
	"strings"
	"testing"
)

func TestAskReturnsDefaultOnEmptyLine(t *testing.T) {
	p := NewPrompter(strings.NewReader("\n"), &bytes.Buffer{}, newFakeSystem())
	if got := p.Ask("Port", "8765"); got != "8765" {
		t.Fatalf("Ask = %q, want the default", got)
	}
}

func TestAskTrimsAndReturnsTypedValue(t *testing.T) {
	p := NewPrompter(strings.NewReader("  9000  \n"), &bytes.Buffer{}, newFakeSystem())
	if got := p.Ask("Port", "8765"); got != "9000" {
		t.Fatalf("Ask = %q, want 9000", got)
	}
}

func TestConfirmParsesYesNoAndFallsBackToDefault(t *testing.T) {
	for _, tc := range []struct {
		in   string
		def  bool
		want bool
	}{{"y\n", false, true}, {"n\n", true, false}, {"\n", true, true}, {"\n", false, false}} {
		p := NewPrompter(strings.NewReader(tc.in), &bytes.Buffer{}, newFakeSystem())
		if got := p.Confirm("ok?", tc.def); got != tc.want {
			t.Errorf("Confirm(%q, %v) = %v, want %v", tc.in, tc.def, got, tc.want)
		}
	}
}

func TestAskSecretDisablesAndRestoresEcho(t *testing.T) {
	sys := newFakeSystem()
	p := NewPrompter(strings.NewReader("sk-secret\n"), &bytes.Buffer{}, sys)
	if got := p.AskSecret("API key"); got != "sk-secret" {
		t.Fatalf("AskSecret = %q", got)
	}
	if !sys.ran("stty -F /dev/tty -echo") || !sys.ran("stty -F /dev/tty echo") {
		t.Fatalf("echo must be disabled then restored on /dev/tty; calls=%v", sys.Calls)
	}
}

func TestAskSecretWarnsWhenEchoCannotBeDisabled(t *testing.T) {
	sys := newFakeSystem()
	sys.Errors["stty -F /dev/tty -echo"] = errNoSuchUser
	var out bytes.Buffer
	p := NewPrompter(strings.NewReader("sk-secret\n"), &out, sys)
	if got := p.AskSecret("API key"); got != "sk-secret" {
		t.Fatalf("AskSecret = %q", got)
	}
	if !strings.Contains(out.String(), "WILL be visible") {
		t.Fatalf("the user must be warned that echo is on:\n%s", out.String())
	}
	if sys.ran("stty -F /dev/tty echo") {
		t.Error("must not restore echo it never disabled")
	}
}

func TestPrompterReportsAnExhaustedReader(t *testing.T) {
	p := NewPrompter(strings.NewReader(""), &bytes.Buffer{}, newFakeSystem())
	if got := p.Ask("Port", "8765"); got != "8765" {
		t.Fatalf("Ask = %q, want the default", got)
	}
	if p.Err() == nil {
		t.Fatal("an exhausted reader must be reported via Err(), or retry loops spin forever")
	}
}

func TestPrompterHonoursAFinalLineWithoutNewline(t *testing.T) {
	p := NewPrompter(strings.NewReader("9000"), &bytes.Buffer{}, newFakeSystem())
	if got := p.Ask("Port", "8765"); got != "9000" {
		t.Fatalf("Ask = %q, want 9000 — ReadString returns the partial line with io.EOF", got)
	}
}

func TestPrompterCloseIsSafeWithoutATTY(t *testing.T) {
	// NewPrompter (used by every non-interactive test, and the compat-suite
	// wizard tests) never sets tty; Close must be a no-op rather than nil-
	// deref, since callers defer it unconditionally right after construction.
	p := NewPrompter(strings.NewReader(""), &bytes.Buffer{}, newFakeSystem())
	p.Close()
	p.Close() // idempotent: safe to call more than once too
}
