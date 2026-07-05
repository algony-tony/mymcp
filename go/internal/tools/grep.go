package tools

import (
	"context"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode"

	"github.com/algony-tony/mymcp/go/internal/fsutil"
)

// Grep ports grep_files: ripgrep when available, native RE2 fallback
// otherwise. maxResults arrives pre-defaulted by the dispatch layer.
func Grep(d Deps, pattern, searchPath, globPat, outputMode string,
	contextLines, maxResults int, caseInsensitive bool,
) map[string]any {
	maxResults = min(max(1, maxResults), d.Cfg.GrepMaxResults)
	if rg := d.RgPath(); rg != "" {
		return grepRg(d, rg, pattern, searchPath, globPat, outputMode, contextLines, maxResults, caseInsensitive)
	}
	return grepNative(d, pattern, searchPath, globPat, outputMode, maxResults, caseInsensitive)
}

func grepRg(d Deps, rg, pattern, searchPath, globPat, outputMode string,
	contextLines, maxResults int, caseInsensitive bool,
) map[string]any {
	args := []string{"--no-heading", "-n"}
	if caseInsensitive {
		args = append(args, "-i")
	}
	if contextLines > 0 {
		args = append(args, "-C", strconv.Itoa(contextLines))
	}
	if globPat != "" {
		args = append(args, "--glob", globPat)
	}
	switch outputMode {
	case "files":
		args = append(args, "-l")
	case "count":
		args = append(args, "--count")
	}
	args = append(args, pattern, searchPath)

	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, rg, args...).Output()
	if ctx.Err() == context.DeadlineExceeded {
		return map[string]any{
			"success": false, "error": "TimeoutError",
			"message": "grep timed out after 60s",
		}
	}
	// rg exits 1 on "no matches" and 2 on real errors (bad regex, bad path, etc.).
	// We treat both like Python's _grep_rg, which never inspects the exit code —
	// any non-zero exit with empty stdout yields {"results": "", "match_count": 0}.
	// NOTE: this differs from the native fallback, which returns InvalidRegex for
	// bad patterns. That inconsistency is pre-existing in the Python core.
	var exitErr *exec.ExitError
	if err != nil && !errors.As(err, &exitErr) {
		return map[string]any{"success": false, "error": fmt.Sprintf("%T", err), "message": err.Error()}
	}

	var kept []string
	for _, line := range strings.Split(strings.TrimRight(fsutil.DecodeReplace(out), "\n"), "\n") {
		if line == "" {
			continue
		}
		p, _, _ := strings.Cut(line, ":")
		if fsutil.CheckProtectedPath(p, fsutil.ModeRead, d.Protected) == "" {
			kept = append(kept, line)
		}
	}
	return grepResult(kept, maxResults)
}

func grepNative(d Deps, pattern, searchPath, globPat, outputMode string,
	maxResults int, caseInsensitive bool,
) map[string]any {
	// contextLines is not supported by the native fallback (same as Python's _grep_python).
	if caseInsensitive {
		pattern = "(?i)" + pattern
	}
	re, err := regexp.Compile(pattern)
	if err != nil {
		return map[string]any{"success": false, "error": "InvalidRegex", "message": err.Error()}
	}

	var files []string
	if st, err := os.Stat(searchPath); err == nil && !st.IsDir() {
		files = []string{searchPath}
	} else {
		filepath.WalkDir(searchPath, func(p string, entry fs.DirEntry, err error) error {
			if err != nil || entry.IsDir() {
				return nil
			}
			if globPat != "" {
				if ok, _ := filepath.Match(globPat, entry.Name()); !ok {
					return nil
				}
			}
			files = append(files, p)
			return nil
		})
	}

	var matches []string
	for _, fpath := range files {
		if fsutil.CheckProtectedPath(fpath, fsutil.ModeRead, d.Protected) != "" {
			continue
		}
		// same break placement as Python — within-file over-accumulation is expected; grepResult truncates.
		if len(matches) >= maxResults && outputMode == "content" {
			break
		}
		raw, err := os.ReadFile(fpath)
		if err != nil {
			continue
		}
		// Python readlines() yields no phantom empty final line for files
		// ending in \n — trim one trailing newline before splitting.
		lines := strings.Split(strings.TrimSuffix(fsutil.DecodeReplace(raw), "\n"), "\n")
		switch outputMode {
		case "files":
			for _, line := range lines {
				if re.MatchString(line) {
					matches = append(matches, fpath)
					break
				}
			}
		case "count":
			n := 0
			for _, line := range lines {
				if re.MatchString(line) {
					n++
				}
			}
			if n > 0 {
				matches = append(matches, fmt.Sprintf("%s: %d", fpath, n))
			}
		default: // content
			for i, line := range lines {
				if re.MatchString(line) {
					matches = append(matches,
						fmt.Sprintf("%s:%d:%s", fpath, i+1, strings.TrimRightFunc(line, unicode.IsSpace)))
				}
			}
		}
	}
	return grepResult(matches, maxResults)
}

func grepResult(matches []string, maxResults int) map[string]any {
	total := len(matches)
	truncated := total > maxResults
	shown := matches
	if truncated {
		shown = matches[:maxResults]
	}
	result := strings.Join(shown, "\n")
	if truncated {
		result += fmt.Sprintf("\n[TRUNCATED: %d more matches not shown]", total-maxResults)
	}
	return map[string]any{"results": result, "match_count": total}
}
