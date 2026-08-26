package setup

import (
	"bufio"
	"fmt"
	"io"
	"os"
	"strings"
)

// Prompter asks questions. In production its reader is /dev/tty, never stdin:
// under `curl … | sudo bash` stdin is the pipe and a stdin prompt reads EOF.
type Prompter struct {
	in  *bufio.Reader
	out io.Writer
	sys System
	tty *os.File
	err error
}

func NewPrompter(r io.Reader, w io.Writer, sys System) *Prompter {
	return &Prompter{in: bufio.NewReader(r), out: w, sys: sys}
}

// OpenTTYPrompter opens /dev/tty. The error is the caller's cue to demand -yes.
func OpenTTYPrompter(sys System) (*Prompter, error) {
	f, err := os.OpenFile("/dev/tty", os.O_RDWR, 0)
	if err != nil {
		return nil, fmt.Errorf("no interactive terminal (/dev/tty): %w", err)
	}
	p := NewPrompter(f, f, sys)
	p.tty = f
	return p, nil
}

func (p *Prompter) Close() {
	if p.tty != nil {
		_ = p.tty.Close()
	}
}

// Err returns the first read error, if any. A retry loop MUST check it:
// after the reader is exhausted every Ask returns its default instantly.
func (p *Prompter) Err() error { return p.err }

func (p *Prompter) readLine() string {
	line, err := p.in.ReadString('\n')
	if err != nil && p.err == nil {
		// Record the first failure so retry loops can tell "user pressed
		// Enter" (default) apart from "the reader is exhausted" (never
		// recoverable). Without this an input-validation loop spins forever.
		p.err = err
	}
	return strings.TrimSpace(line)
}

func (p *Prompter) Ask(question, def string) string {
	if def != "" {
		fmt.Fprintf(p.out, "%s [%s]: ", question, def)
	} else {
		fmt.Fprintf(p.out, "%s: ", question)
	}
	if v := p.readLine(); v != "" {
		return v
	}
	return def
}

func (p *Prompter) Confirm(question string, def bool) bool {
	hint := "y/N"
	if def {
		hint = "Y/n"
	}
	fmt.Fprintf(p.out, "%s [%s]: ", question, hint)
	switch strings.ToLower(p.readLine()) {
	case "y", "yes":
		return true
	case "n", "no":
		return false
	default:
		return def
	}
}

// AskSecret suppresses echo so an API key never lands on screen. stty must be
// pointed at the terminal with -F: System.Run uses exec.Command, whose child
// stdin is /dev/null, so a bare `stty -echo` fails with "Inappropriate ioctl
// for device" and silently leaves echo on.
func (p *Prompter) AskSecret(question string) string {
	fmt.Fprintf(p.out, "%s: ", question)
	if _, err := p.sys.Run("stty", "-F", "/dev/tty", "-echo"); err != nil {
		fmt.Fprintf(p.out, "\n  WARNING: cannot disable terminal echo (%v);\n"+
			"  what you type next WILL be visible. Ctrl-C and pass the key via\n"+
			"  -recorder-api-key or MYMCP_RECORDER_LLM_API_KEY to avoid this.\n%s: ", err, question)
	} else {
		defer func() { _, _ = p.sys.Run("stty", "-F", "/dev/tty", "echo") }()
	}
	v := p.readLine()
	fmt.Fprintln(p.out)
	return v
}
