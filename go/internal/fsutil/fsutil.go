// Package fsutil holds filesystem helpers shared by the file tools:
// protected-path checks (parity with src/mymcp/tools/files.py) and Python
// errors="replace"-style UTF-8 decoding.
package fsutil

import (
	"os"
	"path/filepath"
	"strings"
	"unicode/utf8"
)

type Mode uint8

const (
	ModeRead Mode = 1 << iota
	ModeWrite
)

type ProtectedEntry struct {
	Pattern string
	Modes   Mode
}

// CheckProtectedPath returns the denial message if path is protected against
// mode, or "" if allowed. Message text matches the Python core.
// Note: a symlink inside a protected dir that resolves outside is intentionally
// allowed — protection is based on the real (resolved) location, not the
// logical path traversed.
func CheckProtectedPath(path string, mode Mode, protected []ProtectedEntry) string {
	real := realPath(path)
	for _, entry := range protected {
		if entry.Modes&mode == 0 {
			continue
		}
		protReal := realPath(entry.Pattern)
		if real == protReal || strings.HasPrefix(real, protReal+string(os.PathSeparator)) {
			return "Access denied: path is within protected directory " + entry.Pattern
		}
	}
	return ""
}

// realPath mimics Python os.path.realpath: absolute + symlinks resolved, and
// it never fails — for nonexistent paths the deepest existing ancestor is
// resolved and the remaining components are appended.
func realPath(p string) string {
	abs, err := filepath.Abs(p)
	if err != nil {
		// Abs fails only if Getwd fails (deleted cwd); Python's realpath has
		// the same exposure.
		return p
	}
	if resolved, err := filepath.EvalSymlinks(abs); err == nil {
		return resolved
	}
	dir, rest := abs, ""
	for {
		parent := filepath.Dir(dir)
		rest = filepath.Join(filepath.Base(dir), rest)
		dir = parent
		if resolved, err := filepath.EvalSymlinks(dir); err == nil {
			return filepath.Join(resolved, rest)
		}
		if parent == filepath.Dir(parent) { // reached root
			return abs
		}
	}
}

// DecodeReplace decodes bytes as UTF-8, replacing EACH invalid byte with
// U+FFFD — Python bytes.decode("utf-8", errors="replace") semantics.
func DecodeReplace(b []byte) string {
	if utf8.Valid(b) {
		return string(b)
	}
	var sb strings.Builder
	// replacement chars are 3 bytes each; overshoot a little for mixed content
	sb.Grow(len(b) + len(b)/4)
	for len(b) > 0 {
		r, size := utf8.DecodeRune(b)
		if r == utf8.RuneError && size == 1 {
			sb.WriteRune('�')
		} else {
			sb.Write(b[:size])
		}
		b = b[size:]
	}
	return sb.String()
}
