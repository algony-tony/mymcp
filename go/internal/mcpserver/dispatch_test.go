package mcpserver

import (
	"path/filepath"
	"strings"
	"testing"

	"github.com/algony-tony/mymcp/go/internal/transfer"
)

// TestDispatchCoversTools drives every case in the Dispatch switch so the tool
// argument-defaulting layer is exercised end to end. Each tool runs against a
// throwaway temp dir; we assert the shape of a few and let the rest execute for
// coverage of the arg-parsing branches.
func TestDispatchCoversTools(t *testing.T) {
	d := deps(t)
	d.Tickets = transfer.NewTicketStore()
	info := AuthInfo{TokenName: "n", Role: "rw"}

	dir := t.TempDir()
	fp := filepath.Join(dir, "f.txt")

	if out := Dispatch(d, "write_file", map[string]any{"file_path": fp, "content": "alpha\nbeta\n"}, info); !strings.Contains(out, `"success"`) {
		t.Fatalf("write_file = %s", out)
	}
	if out := Dispatch(d, "read_file", map[string]any{"file_path": fp, "limit": float64(10), "offset": float64(1)}, info); !strings.Contains(out, "alpha") {
		t.Fatalf("read_file = %s", out)
	}
	Dispatch(d, "edit_file", map[string]any{"file_path": fp, "old_string": "alpha", "new_string": "zeta", "replace_all": true}, info)
	Dispatch(d, "glob", map[string]any{"pattern": "*.txt", "path": dir}, info)
	Dispatch(d, "grep", map[string]any{"pattern": "zeta", "path": dir, "max_results": float64(5), "context_lines": float64(1)}, info)
	if out := Dispatch(d, "bash_execute", map[string]any{"command": "echo hi", "timeout": float64(5), "max_output_bytes": float64(4096)}, info); !strings.Contains(out, "hi") {
		t.Fatalf("bash_execute = %s", out)
	}
	Dispatch(d, "server_overview", map[string]any{}, info)
	Dispatch(d, "prepare_upload", map[string]any{"dest_path": filepath.Join(dir, "up.bin"), "max_bytes": float64(1024), "expires_in": float64(60)}, info)
	Dispatch(d, "prepare_download", map[string]any{"src_path": fp, "expires_in": float64(60)}, info)

	if out := Dispatch(d, "no_such_tool", map[string]any{}, info); !strings.Contains(out, "UnknownTool") {
		t.Fatalf("unknown tool = %s", out)
	}
}
