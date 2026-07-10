package mcpserver

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/algony-tony/mymcp/go/internal/audit"
	"github.com/algony-tony/mymcp/go/internal/config"
	"github.com/algony-tony/mymcp/go/internal/metrics"
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

func newServer(t *testing.T) *Server {
	t.Helper()
	d := deps(t)
	a, err := audit.New(false, t.TempDir(), 1<<20, 5) // disabled: tests don't assert audit here
	if err != nil {
		t.Fatal(err)
	}
	m := metrics.New(func() float64 { return 0 })
	return New(d, a, m)
}

func TestCheckToolPermission(t *testing.T) {
	if got := CheckToolPermission("read_file", "ro"); got != "" {
		t.Fatalf("ro+read must pass: %q", got)
	}
	if got := CheckToolPermission("write_file", "rw"); got != "" {
		t.Fatalf("rw+write must pass: %q", got)
	}
	if got := CheckToolPermission("write_file", "ro"); got != "Permission denied: tool 'write_file' requires rw role" {
		t.Fatalf("ro+write: %q", got)
	}
	if got := CheckToolPermission("no_such_tool", "rw"); got != "Unknown tool: no_such_tool" {
		t.Fatalf("unknown: %q", got)
	}
}

func TestAuthInfoFromDefaultsToLeastPrivilege(t *testing.T) {
	if authInfoFrom(context.Background()).Role != "ro" {
		t.Fatal("missing auth info must default to ro")
	}
}

func TestToolNamesAreSix(t *testing.T) {
	names := ToolNames()
	if len(names) != 6 {
		t.Fatalf("expected 6 tools, got %d: %v", len(names), names)
	}
	want := map[string]bool{"read_file": true, "glob": true, "grep": true,
		"bash_execute": true, "write_file": true, "edit_file": true}
	for _, n := range names {
		delete(want, n)
	}
	if len(want) != 0 {
		t.Fatalf("missing tools: %v", want)
	}
	for _, td := range toolDefs {
		_ = mustSchema(td.SchemaJSON)
	}
}

func TestDispatchWriteThenEdit(t *testing.T) {
	d := deps(t)
	p := filepath.Join(t.TempDir(), "f.txt")
	out := Dispatch(d, "write_file", map[string]any{"file_path": p, "content": "a b a"})
	if !strings.Contains(out, `"success":true`) {
		t.Fatalf("write: %s", out)
	}
	out = Dispatch(d, "edit_file", map[string]any{"file_path": p, "old_string": "b", "new_string": "B"})
	if !strings.Contains(out, `"replacements":1`) {
		t.Fatalf("edit: %s", out)
	}
	got, _ := os.ReadFile(p)
	if string(got) != "a B a" {
		t.Fatalf("file = %q", got)
	}
}

func TestClassifyResult(t *testing.T) {
	cases := []struct {
		json, status, code string
	}{
		{`{"content":"x","total_lines":1}`, "ok", ""},
		{`{"success":true,"bytes_written":3}`, "ok", ""},
		{`{"success":false,"error":"ProtectedPath","message":"m"}`, "error", "ProtectedPath"},
		{`{"stdout":"","stderr":"Command timed out after 1s","exit_code":-1,"timed_out":true}`, "error", "TimeoutError"},
		{`{"stdout":"","stderr":"boom","exit_code":3,"timed_out":false}`, "error", "ExitCode:3"},
		{`{"stdout":"ok","stderr":"","exit_code":0,"timed_out":false}`, "ok", ""},
	}
	for _, c := range cases {
		status, code, _, _ := classifyResult(c.json)
		if status != c.status || code != c.code {
			t.Fatalf("classify(%s) = (%q,%q), want (%q,%q)", c.json, status, code, c.status, c.code)
		}
	}
}

func TestExtractParamsElidesContent(t *testing.T) {
	got := extractParams(map[string]any{"file_path": "/p", "content": "hello", "old_string": "ab"})
	if got["file_path"] != "/p" {
		t.Fatalf("file_path passed through wrong: %v", got["file_path"])
	}
	if got["content"] != "<5 chars>" || got["old_string"] != "<2 chars>" {
		t.Fatalf("elision wrong: %v", got)
	}
}

func textOf(t *testing.T, res *mcp.CallToolResult) string {
	t.Helper()
	tc, ok := res.Content[0].(*mcp.TextContent)
	if !ok {
		t.Fatalf("content is %T", res.Content[0])
	}
	return tc.Text
}

func TestBuildInProcessDeniedAndUnknown(t *testing.T) {
	s := newServer(t)
	srv := s.Build()
	ctx := context.Background()
	st, ct := mcp.NewInMemoryTransports()
	ss, err := srv.Connect(ctx, st, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer ss.Close()
	client := mcp.NewClient(&mcp.Implementation{Name: "c", Version: "0"}, nil)
	cs, err := client.Connect(ctx, ct, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer cs.Close()

	// Default (no auth info in context) is ro → write tool must be denied.
	res, err := cs.CallTool(ctx, &mcp.CallToolParams{Name: "write_file",
		Arguments: map[string]any{"file_path": "/tmp/x", "content": "y"}})
	if err != nil {
		t.Fatalf("call: %v", err)
	}
	var denied map[string]any
	_ = json.Unmarshal([]byte(textOf(t, res)), &denied)
	if denied["error"] != "PermissionDenied" ||
		denied["message"] != "Permission denied: tool 'write_file' requires rw role" {
		t.Fatalf("denied shape wrong: %v", denied)
	}

	// Unknown tool → PermissionDenied "Unknown tool" via the middleware.
	res, err = cs.CallTool(ctx, &mcp.CallToolParams{Name: "no_such", Arguments: map[string]any{}})
	if err != nil {
		t.Fatalf("unknown must not be a protocol error: %v", err)
	}
	var unk map[string]any
	_ = json.Unmarshal([]byte(textOf(t, res)), &unk)
	if unk["message"] != "Unknown tool: no_such" {
		t.Fatalf("unknown shape wrong: %v", unk)
	}
}
