package tools

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"

	"github.com/bmatcuk/doublestar/v4"

	"github.com/algony-tony/mymcp/go/internal/fsutil"
)

// Glob ports glob_files: recursive glob under path, mtime-desc, protected
// paths filtered, truncated at GlobMaxResults. count is the post-filter,
// pre-truncation total.
func Glob(d Deps, pattern, path string) map[string]any {
	base, err := filepath.Abs(path)
	if err != nil {
		return map[string]any{"success": false, "error": fmt.Sprintf("%T", err), "message": err.Error()}
	}
	fullPattern := filepath.Join(base, pattern)
	matches, err := doublestar.FilepathGlob(fullPattern)
	if err != nil {
		return map[string]any{"success": false, "error": fmt.Sprintf("%T", err), "message": err.Error()}
	}
	sort.SliceStable(matches, func(i, j int) bool {
		return mtimeOrZero(matches[i]) > mtimeOrZero(matches[j])
	})
	filtered := matches[:0]
	for _, m := range matches {
		if fsutil.CheckProtectedPath(m, fsutil.ModeRead, d.Protected) == "" {
			filtered = append(filtered, m)
		}
	}
	count := len(filtered)
	truncated := count > d.Cfg.GlobMaxResults
	if truncated {
		filtered = filtered[:d.Cfg.GlobMaxResults]
	}
	if filtered == nil {
		filtered = []string{}
	}
	return map[string]any{"files": []string(filtered), "count": count, "truncated": truncated}
}

func mtimeOrZero(p string) int64 {
	st, err := os.Stat(p)
	if err != nil {
		return 0
	}
	return st.ModTime().UnixNano()
}
