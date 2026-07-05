package mcpserver

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/algony-tony/mymcp/go/internal/config"
	"github.com/algony-tony/mymcp/go/internal/tools"
)

func deps(t *testing.T) tools.Deps {
	t.Helper()
	t.Setenv("MYMCP_AUDIT_LOG_DIR", filepath.Join(t.TempDir(), "audit"))
	cfg, err := config.Load()
	if err != nil {
		t.Fatal(err)
	}
	return tools.Deps{Cfg: cfg, Protected: tools.ProtectedFromConfig(cfg)}
}

func TestCheckToolPermission(t *testing.T) {
	if got := CheckToolPermission("read_file", "ro"); got != "" {
		t.Fatalf("ro+read must pass: %q", got)
	}
	if got := CheckToolPermission("read_file", "rw"); got != "" {
		t.Fatalf("rw+read must pass: %q", got)
	}
	if got := CheckToolPermission("no_such_tool", "rw"); got != "Unknown tool: no_such_tool" {
		t.Fatalf("unknown: %q", got)
	}
}

func TestDispatchReadFile(t *testing.T) {
	d := deps(t)
	p := filepath.Join(t.TempDir(), "x.txt")
	os.WriteFile(p, []byte("hello\n"), 0o644)
	out := Dispatch(d, "read_file", map[string]any{"file_path": p})
	var res map[string]any
	if err := json.Unmarshal([]byte(out), &res); err != nil {
		t.Fatal(err)
	}
	if res["content"] != "   1\thello" || res["total_lines"] != float64(1) {
		t.Fatalf("res = %v", res)
	}
}

func TestDispatchGrepDefaultsMaxResults(t *testing.T) {
	d := deps(t)
	dir := t.TempDir()
	os.WriteFile(filepath.Join(dir, "f.txt"), []byte("m\nm\nm\n"), 0o644)
	out := Dispatch(d, "grep", map[string]any{"pattern": "m", "path": dir})
	if !strings.Contains(out, `"match_count":3`) && !strings.Contains(out, `"match_count": 3`) {
		t.Fatalf("out = %s", out)
	}
}

func TestDispatchUnknownTool(t *testing.T) {
	d := deps(t)
	out := Dispatch(d, "bash_execute", map[string]any{"command": "id"})
	if !strings.Contains(out, `"UnknownTool"`) {
		t.Fatalf("out = %s", out)
	}
}

func TestSchemasParseAndListNames(t *testing.T) {
	names := ToolNames()
	want := []string{"read_file", "glob", "grep"}
	if len(names) != 3 {
		t.Fatalf("names = %v", names)
	}
	for _, w := range want {
		found := false
		for _, n := range names {
			if n == w {
				found = true
			}
		}
		if !found {
			t.Fatalf("missing %s in %v", w, names)
		}
	}
	for _, td := range toolDefs {
		_ = mustSchema(td.SchemaJSON) // panics on bad JSON
	}
}
