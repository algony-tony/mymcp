package mcpserver

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/modelcontextprotocol/go-sdk/mcp"

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
	// ro caller + write tool must be denied. M1 has no write tools, so register
	// a synthetic one for the duration of this assertion.
	writeTools["synthetic_write"] = true
	defer delete(writeTools, "synthetic_write")
	if got := CheckToolPermission("synthetic_write", "ro"); got != "Permission denied: tool 'synthetic_write' requires rw role" {
		t.Fatalf("ro+write: %q", got)
	}
}

func TestAuthInfoFromDefaultsToLeastPrivilege(t *testing.T) {
	info := authInfoFrom(context.Background())
	if info.Role != "ro" {
		t.Fatalf("missing auth info must default to ro (least privilege), got %q", info.Role)
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

// textOf extracts the single TextContent payload from a tool result.
func textOf(t *testing.T, res *mcp.CallToolResult) string {
	t.Helper()
	if len(res.Content) != 1 {
		t.Fatalf("expected 1 content block, got %d", len(res.Content))
	}
	tc, ok := res.Content[0].(*mcp.TextContent)
	if !ok {
		t.Fatalf("content is %T, want *mcp.TextContent", res.Content[0])
	}
	return tc.Text
}

// TestBuildServerInProcess exercises BuildServer end-to-end over the SDK's
// in-memory transport, covering tools/list, a real read_file call, and the
// unknown-tool receiving middleware (the core of Task 8).
func TestBuildServerInProcess(t *testing.T) {
	d := deps(t)
	dir := t.TempDir()
	filePath := filepath.Join(dir, "hello.txt")
	if err := os.WriteFile(filePath, []byte("hello\nworld\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	srv := BuildServer(d)

	ctx := context.Background()
	serverTransport, clientTransport := mcp.NewInMemoryTransports()
	serverSession, err := srv.Connect(ctx, serverTransport, nil)
	if err != nil {
		t.Fatalf("server connect: %v", err)
	}
	defer serverSession.Close()

	client := mcp.NewClient(&mcp.Implementation{Name: "test-client", Version: "0"}, nil)
	cs, err := client.Connect(ctx, clientTransport, nil)
	if err != nil {
		t.Fatalf("client connect: %v", err)
	}
	defer cs.Close()

	// tools/list — the three read tools must appear with a non-nil InputSchema.
	listed, err := cs.ListTools(ctx, nil)
	if err != nil {
		t.Fatalf("list tools: %v", err)
	}
	got := map[string]*mcp.Tool{}
	for _, tl := range listed.Tools {
		got[tl.Name] = tl
	}
	if len(got) != 3 {
		t.Fatalf("expected 3 tools, got %d: %v", len(got), listed.Tools)
	}
	for _, name := range []string{"read_file", "glob", "grep"} {
		tl, ok := got[name]
		if !ok {
			t.Fatalf("tool %q missing from tools/list", name)
		}
		if tl.InputSchema == nil {
			t.Fatalf("tool %q has nil InputSchema", name)
		}
	}

	// read_file over the wire — TextContent must parse to the expected JSON.
	res, err := cs.CallTool(ctx, &mcp.CallToolParams{
		Name:      "read_file",
		Arguments: map[string]any{"file_path": filePath},
	})
	if err != nil {
		t.Fatalf("call read_file: %v", err)
	}
	var parsed map[string]any
	if err := json.Unmarshal([]byte(textOf(t, res)), &parsed); err != nil {
		t.Fatalf("read_file result not JSON: %v", err)
	}
	if parsed["content"] != "   1\thello\n   2\tworld" || parsed["total_lines"] != float64(2) {
		t.Fatalf("read_file content wrong: %v", parsed)
	}

	// Unknown tool — the receiving middleware must return Python's
	// PermissionDenied shape as TextContent, not an MCP protocol error.
	res, err = cs.CallTool(ctx, &mcp.CallToolParams{
		Name:      "bash_execute",
		Arguments: map[string]any{"command": "id"},
	})
	if err != nil {
		t.Fatalf("unknown tool must not be a protocol error: %v", err)
	}
	var denied map[string]any
	if err := json.Unmarshal([]byte(textOf(t, res)), &denied); err != nil {
		t.Fatalf("unknown tool result not JSON: %v", err)
	}
	if denied["success"] != false || denied["error"] != "PermissionDenied" ||
		denied["message"] != "Unknown tool: bash_execute" {
		t.Fatalf("unknown tool shape wrong: %v", denied)
	}
}
