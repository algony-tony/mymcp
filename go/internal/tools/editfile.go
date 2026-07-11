package tools

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"strings"

	"github.com/algony-tony/mymcp/go/internal/fsutil"
)

// EditFile ports edit_file: replace old_string (unique unless replaceAll).
// The file is read with UTF-8 replacement decoding, matching Python's
// open(errors="replace"). (Python's text-mode CRLF→LF translation is not
// replicated — see plan divergence #2.)
func EditFile(d Deps, filePath, oldString, newString string, replaceAll bool) map[string]any {
	if msg := fsutil.CheckProtectedPath(filePath, fsutil.ModeWrite, d.Protected); msg != "" {
		return map[string]any{"success": false, "error": "ProtectedPath", "message": msg}
	}
	if len(oldString) > d.Cfg.EditStringMaxBytes {
		return map[string]any{"success": false, "error": "FileTooLarge", "message": "old_string exceeds 1MB limit"}
	}
	if len(newString) > d.Cfg.EditStringMaxBytes {
		return map[string]any{"success": false, "error": "FileTooLarge", "message": "new_string exceeds 1MB limit"}
	}

	raw, err := os.ReadFile(filePath)
	if err != nil {
		if errors.Is(err, fs.ErrNotExist) {
			return map[string]any{"success": false, "error": "FileNotFoundError", "message": "File not found: " + filePath}
		}
		if errors.Is(err, fs.ErrPermission) {
			return map[string]any{"success": false, "error": "PermissionError", "message": err.Error()}
		}
		return map[string]any{"success": false, "error": "OSError", "message": err.Error()}
	}
	content := fsutil.DecodeReplace(raw)

	count := strings.Count(content, oldString)
	if count == 0 {
		return map[string]any{"success": false, "error": "StringNotFound", "message": "old_string not found in file"}
	}
	if count > 1 && !replaceAll {
		return map[string]any{
			"success": false, "error": "AmbiguousMatch",
			"message": fmt.Sprintf("old_string appears %d times. Set replace_all=true to replace all occurrences.", count),
		}
	}

	var newContent string
	var replacements int
	if replaceAll {
		newContent = strings.ReplaceAll(content, oldString, newString)
		replacements = count
	} else {
		newContent = strings.Replace(content, oldString, newString, 1)
		replacements = 1
	}

	if err := writeTextFile(filePath, []byte(newContent)); err != nil {
		if errors.Is(err, fs.ErrPermission) {
			return map[string]any{"success": false, "error": "PermissionError", "message": err.Error()}
		}
		return map[string]any{"success": false, "error": "OSError", "message": err.Error()}
	}
	return map[string]any{"success": true, "replacements": replacements}
}
