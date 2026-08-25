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

func (p *Prompter) readLine() string {
	line, _ := p.in.ReadString('\n')
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

// AskSecret suppresses echo via stty so an API key never lands on screen.
// stdlib has no termios helper and the project forbids new dependencies.
func (p *Prompter) AskSecret(question string) string {
	fmt.Fprintf(p.out, "%s: ", question)
	_, _ = p.sys.Run("stty", "-echo")
	v := p.readLine()
	_, _ = p.sys.Run("stty", "echo")
	fmt.Fprintln(p.out)
	return v
}
