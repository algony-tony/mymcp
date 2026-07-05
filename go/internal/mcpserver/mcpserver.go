// Package mcpserver assembles the MCP server: tool registration, the central
// dispatch with permission checks (parity with src/mymcp/mcp_server.py), and
// role plumbing via context.
package mcpserver

import (
	"context"
	"encoding/json"
	"fmt"
	"log"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/algony-tony/mymcp/go/internal/tools"
	"github.com/algony-tony/mymcp/go/internal/version"
)

// Role sets — M1 registers only the read tools; write sets grow in M2/M3.
var readTools = map[string]bool{"read_file": true, "glob": true, "grep": true}
var writeTools = map[string]bool{}

type ctxKey int

const authInfoKey ctxKey = 0

// AuthInfo carries the authenticated caller's identity through the context.
type AuthInfo struct {
	TokenName string
	Role      string
	IP        string
}

// WithAuthInfo is called by the HTTP auth middleware to stash token info.
func WithAuthInfo(ctx context.Context, info AuthInfo) context.Context {
	return context.WithValue(ctx, authInfoKey, info)
}

func authInfoFrom(ctx context.Context) AuthInfo {
	if v, ok := ctx.Value(authInfoKey).(AuthInfo); ok {
		return v
	}
	// Role defaults to least-privilege ro so a propagation bug degrades to
	// read-only, never write; the HTTP auth middleware always sets the real role.
	return AuthInfo{TokenName: "unknown", Role: "ro", IP: "unknown"}
}

// ToolNames returns the registered tool names (M1: the three read tools).
func ToolNames() []string {
	names := make([]string, 0, len(toolDefs))
	for _, td := range toolDefs {
		names = append(names, td.Name)
	}
	return names
}

// CheckToolPermission ports check_tool_permission: "" = allowed.
// unknown name → "Unknown tool: <name>"
// ro + read tool or rw + anything → ""
// ro + write tool → "Permission denied: tool '<name>' requires rw role"
func CheckToolPermission(name, role string) string {
	if !readTools[name] && !writeTools[name] {
		return "Unknown tool: " + name
	}
	if role == "rw" || readTools[name] {
		return ""
	}
	return fmt.Sprintf("Permission denied: tool '%s' requires rw role", name)
}

// Dispatch ports dispatch_tool: run the tool, return the JSON string.
// Argument defaulting mirrors the Python dispatch layer.
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

// BuildServer wires the SDK server: every tool shares one handler that runs
// the permission check then Dispatch, recovering panics into InternalError.
// A receiving middleware intercepts unknown-tool calls to return Python's
// PermissionDenied shape instead of an MCP protocol error.
func BuildServer(d tools.Deps) *mcp.Server {
	srv := mcp.NewServer(&mcp.Implementation{Name: "linux-server", Version: version.Version}, nil)

	for _, td := range toolDefs {
		td := td
		srv.AddTool(
			&mcp.Tool{Name: td.Name, Description: td.Description, InputSchema: mustSchema(td.SchemaJSON)},
			func(ctx context.Context, req *mcp.CallToolRequest) (res *mcp.CallToolResult, _ error) {
				info := authInfoFrom(ctx)
				defer func() {
					if r := recover(); r != nil {
						log.Printf("panic in tool %s: %v", td.Name, r)
						res = textResult(fmt.Sprintf(
							`{"success": false, "error": "InternalError", "message": "Tool '%s' failed with an unexpected error"}`,
							td.Name))
					}
				}()
				// second-line backstop if the receiving middleware is ever bypassed
				if msg := CheckToolPermission(td.Name, info.Role); msg != "" {
					return textResult(permDeniedJSON(msg)), nil
				}
				args := map[string]any{}
				if len(req.Params.Arguments) > 0 {
					if err := json.Unmarshal(req.Params.Arguments, &args); err != nil {
						return textResult(permDeniedJSON("invalid arguments: " + err.Error())), nil
					}
				}
				return textResult(Dispatch(d, td.Name, args)), nil
			},
		)
	}

	// Receiving middleware: intercepts tools/call for unknown tools and
	// permission-denied before the SDK dispatches to a registered handler.
	// This ensures the Python PermissionDenied JSON shape is returned rather
	// than an MCP protocol error, matching src/mymcp/mcp_server.py behaviour.
	srv.AddReceivingMiddleware(func(next mcp.MethodHandler) mcp.MethodHandler {
		return func(ctx context.Context, method string, req mcp.Request) (mcp.Result, error) {
			if method == "tools/call" {
				if p, ok := req.GetParams().(*mcp.CallToolParamsRaw); ok {
					name := p.Name
					if !readTools[name] && !writeTools[name] {
						return textResult(permDeniedJSON("Unknown tool: " + name)), nil
					}
					info := authInfoFrom(ctx)
					if msg := CheckToolPermission(name, info.Role); msg != "" {
						return textResult(permDeniedJSON(msg)), nil
					}
				}
			}
			return next(ctx, method, req)
		}
	})

	return srv
}

func textResult(s string) *mcp.CallToolResult {
	return &mcp.CallToolResult{Content: []mcp.Content{&mcp.TextContent{Text: s}}}
}

func permDeniedJSON(msg string) string {
	raw, _ := json.Marshal(map[string]any{
		"success": false, "error": "PermissionDenied", "message": msg,
	})
	return string(raw)
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
