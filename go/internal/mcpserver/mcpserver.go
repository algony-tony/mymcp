// Package mcpserver assembles the MCP server: tool registration and the central
// callTool choke point (permission → dispatch → classify → audit → metrics),
// a line-for-line port of src/mymcp/mcp_server.py's call_tool.
package mcpserver

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/algony-tony/mymcp/go/internal/audit"
	"github.com/algony-tony/mymcp/go/internal/metrics"
	"github.com/algony-tony/mymcp/go/internal/tools"
	"github.com/algony-tony/mymcp/go/internal/version"
)

var readTools = map[string]bool{"read_file": true, "glob": true, "grep": true}
var writeTools = map[string]bool{"bash_execute": true, "write_file": true, "edit_file": true}

type ctxKey int

const authInfoKey ctxKey = 0

// AuthInfo carries the authenticated caller's identity through the context.
type AuthInfo struct {
	TokenName string
	Role      string
	IP        string
	RequestID string
}

// WithAuthInfo is called by the HTTP auth middleware to stash token info.
func WithAuthInfo(ctx context.Context, info AuthInfo) context.Context {
	return context.WithValue(ctx, authInfoKey, info)
}

func authInfoFrom(ctx context.Context) AuthInfo {
	if v, ok := ctx.Value(authInfoKey).(AuthInfo); ok {
		return v
	}
	// Least-privilege default so a propagation bug degrades to read-only.
	return AuthInfo{TokenName: "unknown", Role: "ro", IP: "unknown"}
}

// Server bundles the dependencies the callTool pipeline needs.
type Server struct {
	deps   tools.Deps
	audit  *audit.Writer
	metric *metrics.Metrics
}

// New constructs the server. audit may be a disabled writer; metric is required.
func New(d tools.Deps, a *audit.Writer, m *metrics.Metrics) *Server {
	return &Server{deps: d, audit: a, metric: m}
}

// ToolNames returns the registered tool names.
func ToolNames() []string {
	names := make([]string, 0, len(toolDefs))
	for _, td := range toolDefs {
		names = append(names, td.Name)
	}
	return names
}

// CheckToolPermission ports check_tool_permission: "" = allowed.
func CheckToolPermission(name, role string) string {
	if !readTools[name] && !writeTools[name] {
		return "Unknown tool: " + name
	}
	if role == "rw" || readTools[name] {
		return ""
	}
	return fmt.Sprintf("Permission denied: tool '%s' requires rw role", name)
}

// Build wires the SDK server. Every registered tool shares the callTool handler;
// a receiving middleware routes unknown-tool calls through callTool too (so they
// are audited/counted as denied and return Python's PermissionDenied shape).
func (s *Server) Build() *mcp.Server {
	srv := mcp.NewServer(&mcp.Implementation{Name: "linux-server", Version: version.Version}, nil)
	for _, td := range toolDefs {
		td := td
		srv.AddTool(
			&mcp.Tool{Name: td.Name, Description: td.Description, InputSchema: mustSchema(td.SchemaJSON)},
			func(ctx context.Context, req *mcp.CallToolRequest) (*mcp.CallToolResult, error) {
				return textResult(s.callTool(ctx, td.Name, req.Params.Arguments)), nil
			},
		)
	}
	srv.AddReceivingMiddleware(func(next mcp.MethodHandler) mcp.MethodHandler {
		return func(ctx context.Context, method string, req mcp.Request) (mcp.Result, error) {
			if method == "tools/call" {
				if p, ok := req.GetParams().(*mcp.CallToolParamsRaw); ok {
					if !readTools[p.Name] && !writeTools[p.Name] {
						return textResult(s.callTool(ctx, p.Name, p.Arguments)), nil
					}
				}
			}
			return next(ctx, method, req)
		}
	})
	return srv
}

// callTool is the single choke point. Returns the JSON text the client receives.
func (s *Server) callTool(ctx context.Context, name string, rawArgs json.RawMessage) string {
	info := authInfoFrom(ctx)
	args := parseArgs(rawArgs)

	// Permission (also catches unknown tools).
	if msg := CheckToolPermission(name, info.Role); msg != "" {
		s.metric.ToolCalls.WithLabelValues(name, info.Role, "denied").Inc()
		if s.writeAudit(info, name, args, "denied", auditExtra{reason: msg}) != nil {
			return internalErrorJSON(name)
		}
		return permDeniedJSON(msg)
	}

	start := time.Now()
	resultJSON, panicked := s.dispatchRecover(name, args)
	durationMs := int(time.Since(start).Milliseconds())

	if panicked {
		s.metric.ToolCalls.WithLabelValues(name, info.Role, "error").Inc()
		s.metric.ToolDuration.WithLabelValues(name).Observe(float64(durationMs) / 1000)
		_ = s.writeAudit(info, name, args, "error", auditExtra{
			errorCode: "InternalError", errorMessage: "Unhandled exception in " + name, durationMs: &durationMs,
		})
		return internalErrorJSON(name)
	}

	status, errorCode, errorMessage, data := classifyResult(resultJSON)
	var output map[string]any
	if status == "ok" {
		output = s.buildOutput(name, args, data)
	}
	s.metric.ToolCalls.WithLabelValues(name, info.Role, status).Inc()
	s.metric.ToolDuration.WithLabelValues(name).Observe(float64(durationMs) / 1000)
	if s.writeAudit(info, name, args, status, auditExtra{
		errorCode: errorCode, errorMessage: errorMessage, durationMs: &durationMs, output: output,
	}) != nil {
		// SOC red line: an unauditable call must not be confirmed to the client.
		return internalErrorJSON(name)
	}
	return resultJSON
}

func (s *Server) dispatchRecover(name string, args map[string]any) (result string, panicked bool) {
	defer func() {
		if r := recover(); r != nil {
			log.Printf("panic in tool %s: %v", name, r)
			panicked = true
		}
	}()
	return Dispatch(s.deps, name, args), false
}

type auditExtra struct {
	reason       string
	errorCode    string
	errorMessage string
	durationMs   *int
	output       map[string]any
}

func (s *Server) writeAudit(info AuthInfo, tool string, args map[string]any, result string, ex auditExtra) error {
	err := s.audit.Log(audit.Entry{
		TS: time.Now().UTC().Format(time.RFC3339Nano), TokenName: info.TokenName,
		Role: info.Role, IP: info.IP, Tool: tool, Params: extractParams(args), Result: result,
		RequestID: info.RequestID, Reason: ex.reason, ErrorCode: ex.errorCode,
		ErrorMessage: ex.errorMessage, DurationMs: ex.durationMs, Output: ex.output,
	})
	if err != nil {
		s.metric.IncAuditFailure()
	}
	return err
}

// classifyResult ports the audit status extraction in call_tool.
func classifyResult(resultJSON string) (status, errorCode, errorMessage string, data map[string]any) {
	if err := json.Unmarshal([]byte(resultJSON), &data); err != nil || data == nil {
		return "ok", "", "", nil
	}
	if v, ok := data["success"]; ok {
		if b, isBool := v.(bool); isBool && !b {
			return "error", asString(data["error"]), asString(data["message"]), data
		}
	}
	if tv, ok := data["timed_out"].(bool); ok && tv {
		msg := asString(data["stderr"])
		if msg == "" {
			msg = "Command timed out"
		}
		return "error", "TimeoutError", msg, data
	}
	if ec, ok := data["exit_code"]; ok {
		if code, ok := toInt(ec); ok && code != 0 {
			stderr := asString(data["stderr"])
			msg := "Non-zero exit code"
			if stderr != "" {
				msg = truncateRunes(stderr, 200)
			}
			return "error", fmt.Sprintf("ExitCode:%d", code), msg, data
		}
	}
	return "ok", "", "", data
}

// buildOutput ports the per-tool audit `output` enrichment (ok status only).
func (s *Server) buildOutput(name string, args, data map[string]any) map[string]any {
	cfg := s.deps.Cfg
	switch name {
	case "bash_execute":
		out := audit.TruncateBashOutput([]byte(asString(data["stdout"])),
			cfg.AuditOutputBashHeadBytes, cfg.AuditOutputBashTailBytes)
		out["exit_code"] = data["exit_code"]
		tv, _ := data["timed_out"].(bool)
		out["timed_out"] = tv
		return out
	case "write_file":
		return audit.WriteFileOutput(asString(args["file_path"]), []byte(asString(args["content"])))
	case "edit_file":
		replacements := 0
		if r, ok := toInt(data["replacements"]); ok {
			replacements = r
		}
		oldS := asString(args["old_string"])
		newS := asString(args["new_string"])
		return audit.EditFileOutput(asString(args["file_path"]),
			strings.Count(newS, "\n")*replacements,
			strings.Count(oldS, "\n")*replacements,
			replacements)
	}
	return nil
}

// extractParams ports _extract_params: elide large content fields.
func extractParams(args map[string]any) map[string]any {
	omit := map[string]bool{"content": true, "old_string": true, "new_string": true}
	safe := make(map[string]any, len(args))
	for k, v := range args {
		if omit[k] {
			safe[k] = fmt.Sprintf("<%d chars>", pyLen(v))
		} else {
			safe[k] = v
		}
	}
	return safe
}

// Dispatch runs the tool and returns its JSON string. Argument defaulting
// mirrors the Python dispatch layer.
func Dispatch(d tools.Deps, name string, args map[string]any) string {
	var result map[string]any
	switch name {
	case "read_file":
		var limit *int
		if v, ok := argInt(args, "limit"); ok {
			l := min(v, d.Cfg.ReadFileMaxLimit)
			limit = &l
		}
		offset := 1
		if v, ok := argInt(args, "offset"); ok {
			offset = v
		}
		result = tools.ReadFile(d, argStr(args, "file_path", ""), offset, limit)
	case "glob":
		result = tools.Glob(d, argStr(args, "pattern", ""), argStr(args, "path", "/"))
	case "grep":
		maxResults := d.Cfg.GrepDefaultMaxResults
		if v, ok := argInt(args, "max_results"); ok {
			maxResults = min(v, d.Cfg.GrepMaxResults)
		}
		contextLines := 0
		if v, ok := argInt(args, "context_lines"); ok {
			contextLines = v
		}
		result = tools.Grep(d,
			argStr(args, "pattern", ""), argStr(args, "path", "/"),
			argStr(args, "glob", ""), argStr(args, "output_mode", "content"),
			contextLines, maxResults, argBool(args, "case_insensitive"))
	case "bash_execute":
		timeout := 30
		if v, ok := argInt(args, "timeout"); ok {
			timeout = min(v, 600)
		}
		maxOut := d.Cfg.BashMaxOutputBytes
		if v, ok := argInt(args, "max_output_bytes"); ok {
			maxOut = min(v, d.Cfg.BashMaxOutputBytesHard)
		}
		result = tools.RunBash(d, argStr(args, "command", ""), timeout, argStr(args, "working_dir", "/"), maxOut)
	case "write_file":
		result = tools.WriteFile(d, argStr(args, "file_path", ""), argStr(args, "content", ""))
	case "edit_file":
		result = tools.EditFile(d, argStr(args, "file_path", ""),
			argStr(args, "old_string", ""), argStr(args, "new_string", ""), argBool(args, "replace_all"))
	default:
		result = map[string]any{
			"success": false, "error": "UnknownTool",
			"message": fmt.Sprintf("No tool named '%s'", name),
		}
	}
	raw, err := json.Marshal(result)
	if err != nil {
		return `{"success": false, "error": "InternalError", "message": "result serialization failed"}`
	}
	return string(raw)
}

func textResult(s string) *mcp.CallToolResult {
	return &mcp.CallToolResult{Content: []mcp.Content{&mcp.TextContent{Text: s}}}
}

func permDeniedJSON(msg string) string {
	raw, _ := json.Marshal(map[string]any{"success": false, "error": "PermissionDenied", "message": msg})
	return string(raw)
}

func internalErrorJSON(name string) string {
	raw, _ := json.Marshal(map[string]any{
		"success": false, "error": "InternalError",
		"message": fmt.Sprintf("Tool '%s' failed with an unexpected error", name),
	})
	return string(raw)
}

func parseArgs(raw json.RawMessage) map[string]any {
	args := map[string]any{}
	if len(raw) > 0 {
		_ = json.Unmarshal(raw, &args)
	}
	return args
}

func argStr(args map[string]any, key, def string) string {
	if v, ok := args[key].(string); ok {
		return v
	}
	return def
}

func argInt(args map[string]any, key string) (int, bool) {
	switch v := args[key].(type) {
	case float64:
		return int(v), true
	case int:
		return v, true
	}
	return 0, false
}

func argBool(args map[string]any, key string) bool {
	v, _ := args[key].(bool)
	return v
}

func asString(v any) string {
	s, _ := v.(string)
	return s
}

func toInt(v any) (int, bool) {
	switch n := v.(type) {
	case float64:
		return int(n), true
	case int:
		return n, true
	}
	return 0, false
}

func truncateRunes(s string, n int) string {
	if utf8.RuneCountInString(s) <= n {
		return s
	}
	r := []rune(s)
	return string(r[:n])
}

func pyLen(v any) int {
	if s, ok := v.(string); ok {
		return utf8.RuneCountInString(s)
	}
	return utf8.RuneCountInString(fmt.Sprint(v))
}
