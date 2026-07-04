// Package tools implements the MCP tool behaviors, ported line-for-line from
// src/mymcp/tools/files.py. Every function returns a map that the MCP layer
// serializes to JSON — key names and error codes are part of the compat
// contract with the Python core.
package tools

import (
	"bytes"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"strings"

	"github.com/algony-tony/mymcp/go/internal/config"
	"github.com/algony-tony/mymcp/go/internal/fsutil"
)

// Deps carries config and the protected-path table into the tools.
type Deps struct {
	Cfg       *config.Config
	Protected []fsutil.ProtectedEntry
}

// ProtectedFromConfig builds the legacy protected table (audit dir + extras),
// which blocks both read and write, matching config.PROTECTED_PATHS.
func ProtectedFromConfig(cfg *config.Config) []fsutil.ProtectedEntry {
	var out []fsutil.ProtectedEntry
	for _, p := range cfg.ProtectedPaths() {
		out = append(out, fsutil.ProtectedEntry{Pattern: p, Modes: fsutil.ModeRead | fsutil.ModeWrite})
	}
	return out
}

// ReadFile ports read_file. limit == nil → config default. Returned map keys
// and error codes are the compat contract.
func ReadFile(d Deps, filePath string, offset int, limit *int) map[string]any {
	lim := d.Cfg.ReadFileDefaultLimit
	if limit != nil {
		lim = *limit
	}
	lim = min(max(1, lim), d.Cfg.ReadFileMaxLimit)
	offset = max(1, offset)

	if msg := fsutil.CheckProtectedPath(filePath, fsutil.ModeRead, d.Protected); msg != "" {
		return map[string]any{"success": false, "error": "ProtectedPath", "message": msg}
	}

	raw, err := os.ReadFile(filePath)
	if err != nil {
		switch {
		case errors.Is(err, fs.ErrNotExist):
			return map[string]any{
				"success": false, "error": "FileNotFoundError",
				"message": "File not found: " + filePath, "suggestion": "Check the file path",
			}
		case errors.Is(err, fs.ErrPermission):
			return map[string]any{
				"success": false, "error": "PermissionError",
				"message": err.Error(), "suggestion": "Check file read permissions",
			}
		default:
			// Reading a directory errors with EISDIR on Linux and lands here.
			if st, serr := os.Stat(filePath); serr == nil && st.IsDir() {
				return map[string]any{
					"success": false, "error": "IsADirectoryError",
					"message":    filePath + " is a directory",
					"suggestion": "Use glob to list directory contents",
				}
			}
			return map[string]any{"success": false, "error": "OSError", "message": err.Error()}
		}
	}

	rawLines := splitKeepLines(raw)
	total := len(rawLines)
	start := offset - 1
	end := min(start+lim, total)
	var out []string
	if start < total {
		for i, line := range rawLines[start:end] {
			line = bytes.TrimRight(line, "\n")
			line = bytes.TrimRight(line, "\r")
			var text string
			if len(line) > d.Cfg.ReadFileMaxLineBytes {
				text = fsutil.DecodeReplace(line[:d.Cfg.ReadFileMaxLineBytes]) + " [LINE TRUNCATED]"
			} else {
				text = fsutil.DecodeReplace(line)
			}
			out = append(out, fmt.Sprintf("%4d\t%s", start+1+i, text))
		}
	}
	return map[string]any{
		"content":     strings.Join(out, "\n"),
		"total_lines": total,
		"truncated":   (offset - 1 + lim) < total,
	}
}

// splitKeepLines mimics Python readlines(): split after each \n, keep the
// terminator; a final chunk without \n is still a line; empty file → 0 lines.
func splitKeepLines(b []byte) [][]byte {
	var lines [][]byte
	for len(b) > 0 {
		i := bytes.IndexByte(b, '\n')
		if i < 0 {
			lines = append(lines, b)
			break
		}
		lines = append(lines, b[:i+1])
		b = b[i+1:]
	}
	return lines
}
