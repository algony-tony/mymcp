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
	matches, err := doublestar.FilepathGlob(fullPattern, doublestar.WithNoHidden())
	if err != nil {
		return map[string]any{"success": false, "error": fmt.Sprintf("%T", err), "message": err.Error()}
	}
	type fileWithMtime struct {
		path  string
		mtime int64
	}
	items := make([]fileWithMtime, len(matches))
	for i, m := range matches {
		items[i] = fileWithMtime{m, mtimeOrZero(m)}
	}
	sort.SliceStable(items, func(i, j int) bool {
		return items[i].mtime > items[j].mtime
	})
	for i, item := range items {
		matches[i] = item.path
	}
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
	// FilepathGlob returns nil for no matches; guard ensures JSON [] not null.
	if filtered == nil {
		filtered = []string{}
	}
	return map[string]any{"files": filtered, "count": count, "truncated": truncated}
}

func mtimeOrZero(p string) int64 {
	st, err := os.Stat(p)
	if err != nil {
		return 0
	}
	return st.ModTime().UnixNano()
}
