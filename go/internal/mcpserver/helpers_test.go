package mcpserver

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestArgHelpers(t *testing.T) {
	args := map[string]any{
		"s":     "hello",
		"n":     float64(7), // JSON numbers decode as float64
		"ni":    int(9),
		"b":     true,
		"notin": nil,
	}
	if got := argStr(args, "s", "def"); got != "hello" {
		t.Fatalf("argStr present = %q", got)
	}
	if got := argStr(args, "missing", "def"); got != "def" {
		t.Fatalf("argStr missing = %q", got)
	}
	if got := argStr(args, "n", "def"); got != "def" {
		t.Fatalf("argStr wrong-type must fall back = %q", got)
	}
	if v, ok := argInt(args, "n"); !ok || v != 7 {
		t.Fatalf("argInt float64 = %d,%v", v, ok)
	}
	if v, ok := argInt(args, "ni"); !ok || v != 9 {
		t.Fatalf("argInt int = %d,%v", v, ok)
	}
	if _, ok := argInt(args, "s"); ok {
		t.Fatal("argInt on string must be !ok")
	}
	if _, ok := argInt(args, "missing"); ok {
		t.Fatal("argInt missing must be !ok")
	}
	if !argBool(args, "b") {
		t.Fatal("argBool true")
	}
	if argBool(args, "missing") {
		t.Fatal("argBool missing must be false")
	}
	if !argBoolDefault(args, "missing", true) {
		t.Fatal("argBoolDefault missing must use default")
	}
	if argBoolDefault(args, "b", false) != true {
		t.Fatal("argBoolDefault present must use value")
	}
}

func TestArgPtrHelpers(t *testing.T) {
	args := map[string]any{"n": float64(5)}
	if p := argIntPtr(args, "n"); p == nil || *p != 5 {
		t.Fatalf("argIntPtr present = %v", p)
	}
	if p := argIntPtr(args, "missing"); p != nil {
		t.Fatalf("argIntPtr missing must be nil")
	}
	if p := argInt64Ptr(args, "n"); p == nil || *p != 5 {
		t.Fatalf("argInt64Ptr present = %v", p)
	}
	if p := argInt64Ptr(args, "missing"); p != nil {
		t.Fatalf("argInt64Ptr missing must be nil")
	}
}

func TestScalarHelpers(t *testing.T) {
	if asString("x") != "x" || asString(42) != "" {
		t.Fatal("asString")
	}
	if v, ok := toInt(float64(3)); !ok || v != 3 {
		t.Fatal("toInt float64")
	}
	if v, ok := toInt(int(4)); !ok || v != 4 {
		t.Fatal("toInt int")
	}
	if _, ok := toInt("nope"); ok {
		t.Fatal("toInt string must be !ok")
	}
	if pyLen("héllo") != 5 { // rune count, not bytes
		t.Fatalf("pyLen string = %d", pyLen("héllo"))
	}
	if pyLen(123) != 3 { // fmt.Sprint(123) == "123"
		t.Fatalf("pyLen non-string = %d", pyLen(123))
	}
}

func TestTruncateRunes(t *testing.T) {
	if got := truncateRunes("abc", 10); got != "abc" {
		t.Fatalf("no-trunc = %q", got)
	}
	if got := truncateRunes("abcdef", 3); got != "abc" {
		t.Fatalf("trunc = %q", got)
	}
	// Multibyte: cut on rune boundary, not byte.
	if got := truncateRunes("héllo", 2); got != "hé" {
		t.Fatalf("multibyte trunc = %q", got)
	}
}

func TestParseArgs(t *testing.T) {
	if m := parseArgs(nil); len(m) != 0 {
		t.Fatalf("nil raw = %v", m)
	}
	if m := parseArgs(json.RawMessage(`{"a":1}`)); m["a"] != float64(1) {
		t.Fatalf("valid raw = %v", m)
	}
	if m := parseArgs(json.RawMessage(`not json`)); len(m) != 0 {
		t.Fatalf("invalid raw must yield empty map, got %v", m)
	}
}

func TestErrorJSONHelpers(t *testing.T) {
	var m map[string]any
	if err := json.Unmarshal([]byte(internalErrorJSON("read_file")), &m); err != nil {
		t.Fatal(err)
	}
	if m["success"] != false || m["error"] != "InternalError" || !strings.Contains(m["message"].(string), "read_file") {
		t.Fatalf("internalErrorJSON = %v", m)
	}
	m = nil
	if err := json.Unmarshal([]byte(permDeniedJSON("nope")), &m); err != nil {
		t.Fatal(err)
	}
	if m["success"] != false || m["error"] != "PermissionDenied" || m["message"] != "nope" {
		t.Fatalf("permDeniedJSON = %v", m)
	}
}

// TestClassifyResultExtraBranches covers the paths the existing
// TestClassifyResult in mcpserver_test.go does not: a non-JSON body and a
// timeout with no stderr (default message).
func TestClassifyResultExtraBranches(t *testing.T) {
	if status, _, _, data := classifyResult(`not json at all`); status != "ok" || data != nil {
		t.Fatalf("non-JSON must classify ok/nil, got %q/%v", status, data)
	}
	status, code, msg, _ := classifyResult(`{"timed_out":true}`)
	if status != "error" || code != "TimeoutError" || !strings.Contains(msg, "timed out") {
		t.Fatalf("timeout-no-stderr = %q/%q/%q", status, code, msg)
	}
}

func TestBuildOutput(t *testing.T) {
	s := newServer(t)
	// write_file
	w := s.buildOutput("write_file", map[string]any{"file_path": "/p", "content": "a\nb"}, nil)
	if w["path"] != "/p" {
		t.Fatalf("write_file output = %v", w)
	}
	// edit_file
	e := s.buildOutput("edit_file",
		map[string]any{"file_path": "/p", "old_string": "x\n", "new_string": "y\n"},
		map[string]any{"replacements": float64(1)})
	if e["path"] != "/p" || e["hunk_count"] != 1 {
		t.Fatalf("edit_file output = %v", e)
	}
	// bash_execute
	b := s.buildOutput("bash_execute", nil,
		map[string]any{"stdout": "hi", "exit_code": float64(0), "timed_out": false})
	if b["timed_out"] != false || b["stdout_head"] != "hi" {
		t.Fatalf("bash output = %v", b)
	}
	// unknown tool → nil
	if s.buildOutput("read_file", nil, nil) != nil {
		t.Fatal("read_file must have no output enrichment")
	}
}

func TestDispatchRecoverHappyPath(t *testing.T) {
	s := newServer(t)
	out, panicked := s.dispatchRecover("no_such_tool", map[string]any{}, AuthInfo{})
	if panicked {
		t.Fatal("valid dispatch must not report panic")
	}
	if !strings.Contains(out, "UnknownTool") {
		t.Fatalf("unknown tool dispatch = %q", out)
	}
}
