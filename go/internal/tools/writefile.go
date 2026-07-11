package tools

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"

	"github.com/algony-tony/mymcp/go/internal/fsutil"
)

// WriteFile ports write_file: create/overwrite a file, creating parent dirs.
func WriteFile(d Deps, filePath, content string) map[string]any {
	if msg := fsutil.CheckProtectedPath(filePath, fsutil.ModeWrite, d.Protected); msg != "" {
		return map[string]any{"success": false, "error": "ProtectedPath", "message": msg}
	}
	contentBytes := []byte(content)
	if len(contentBytes) > d.Cfg.WriteFileMaxBytes {
		return map[string]any{
			"success": false, "error": "FileTooLarge",
			"message": fmt.Sprintf("Content is %d bytes, max is %d (10MB)",
				len(contentBytes), d.Cfg.WriteFileMaxBytes),
			"suggestion": "Use the /files/upload endpoint for large files",
		}
	}
	if err := writeTextFile(filePath, contentBytes); err != nil {
		if errors.Is(err, fs.ErrPermission) {
			return map[string]any{
				"success": false, "error": "PermissionError",
				"message": err.Error(), "suggestion": "Check write permissions",
			}
		}
		return map[string]any{"success": false, "error": "OSError", "message": err.Error()}
	}
	return map[string]any{"success": true, "bytes_written": len(contentBytes)}
}

// writeTextFile mirrors _write_text: makedirs(parent), then write. Shared with
// edit_file.
func writeTextFile(path string, data []byte) error {
	parent := filepath.Dir(absOrSelf(path))
	if err := os.MkdirAll(parent, 0o755); err != nil {
		return err
	}
	return os.WriteFile(path, data, 0o644)
}

func absOrSelf(p string) string {
	if abs, err := filepath.Abs(p); err == nil {
		return abs
	}
	return p
}
