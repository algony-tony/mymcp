package mcpserver

import (
	"encoding/json"
	"fmt"
)

type toolDef struct {
	Name        string
	Description string
	SchemaJSON  string
}

var toolDefs = []toolDef{
	{
		Name:        "read_file",
		Description: "Read a file with line numbers. Supports pagination via offset/limit.",
		SchemaJSON: `{
  "type": "object",
  "properties": {
    "file_path": {"type": "string", "description": "Absolute path to file"},
    "offset": {"type": "integer", "description": "Start line 1-based (default 1)"},
    "limit": {"type": "integer", "description": "Lines to read (default MYMCP_READ_FILE_DEFAULT_LIMIT=2000, max MYMCP_READ_FILE_MAX_LIMIT=50000)"}
  },
  "required": ["file_path"],
  "additionalProperties": false
}`,
	},
	{
		Name:        "glob",
		Description: "Find files by glob pattern, e.g. '**/*.py'. Results sorted by mtime desc.",
		SchemaJSON: `{
  "type": "object",
  "properties": {
    "pattern": {"type": "string", "description": "Glob pattern, e.g. '**/*.log'"},
    "path": {"type": "string", "description": "Root directory (default /)"}
  },
  "required": ["pattern"],
  "additionalProperties": false
}`,
	},
	{
		Name:        "grep",
		Description: "Search file contents with regex. Uses ripgrep if installed, else Python fallback.",
		SchemaJSON: `{
  "type": "object",
  "properties": {
    "pattern": {"type": "string", "description": "Regex pattern"},
    "path": {"type": "string", "description": "File or directory to search (default /)"},
    "glob": {"type": "string", "description": "File filter e.g. '*.log'"},
    "output_mode": {"type": "string", "enum": ["content", "files", "count"], "description": "Output mode (default content)"},
    "context_lines": {"type": "integer", "description": "Lines of context (default 0)"},
    "max_results": {"type": "integer", "description": "Max matches (default 500, max 5000)"},
    "case_insensitive": {"type": "boolean", "description": "Case-insensitive (default false)"}
  },
  "required": ["pattern"],
  "additionalProperties": false
}`,
	},
	{
		Name: "bash_execute",
		Description: "Execute any shell command on the Linux server. " +
			"Stateless: each call is a fresh subprocess, no persistent shell state.\n\n" +
			"WARNING: bash_execute is NOT subject to MYMCP_PROTECTED_PATHS. It can read " +
			"or modify any path the service user can access (including audit logs and " +
			"tokens.json). Untrusted clients should be issued ro tokens, which cannot " +
			"call this tool.\n\n" +
			"Defaults: working_dir='/' if omitted; timeout 30s (max 600s, clamped). " +
			"On timeout, exit_code is -1 and timed_out is true.",
		SchemaJSON: `{
  "type": "object",
  "properties": {
    "command": {"type": "string", "description": "Shell command to run"},
    "timeout": {"type": "integer", "description": "Timeout seconds (default 30, max 600)"},
    "working_dir": {"type": "string", "description": "Working directory (default /)"},
    "max_output_bytes": {"type": "integer", "description": "Max stdout/stderr bytes each (default 102400)"}
  },
  "required": ["command"],
  "additionalProperties": false
}`,
	},
	{
		Name:        "write_file",
		Description: "Create or overwrite a file. Max 10MB.",
		SchemaJSON: `{
  "type": "object",
  "properties": {
    "file_path": {"type": "string", "description": "Absolute path"},
    "content": {"type": "string", "description": "File content (max 10MB)"}
  },
  "required": ["file_path", "content"],
  "additionalProperties": false
}`,
	},
	{
		Name:        "edit_file",
		Description: "Replace a string in a file. old_string must be unique unless replace_all=true.",
		SchemaJSON: `{
  "type": "object",
  "properties": {
    "file_path": {"type": "string"},
    "old_string": {"type": "string", "description": "String to find (max 1MB)"},
    "new_string": {"type": "string", "description": "Replacement string (max 1MB)"},
    "replace_all": {"type": "boolean", "description": "Replace every occurrence (default false)"}
  },
  "required": ["file_path", "old_string", "new_string"],
  "additionalProperties": false
}`,
	},
	{
		Name: "prepare_upload",
		Description: "Mint a one-shot ticket URL for uploading bytes to a server path.\n\n" +
			"Workflow: this tool RETURNS a ticket URL; it does NOT pull from the " +
			"client. The client must then upload via:\n" +
			"    curl -X PUT --data-binary @/local/path <ticket_url>\n" +
			"Tickets are single-use and expire (default MYMCP_TRANSFER_DEFAULT_TTL_SEC=300s).",
		SchemaJSON: `{
  "type": "object",
  "properties": {
    "dest_path": {"type": "string", "description": "Absolute server path to write to"},
    "max_bytes": {"type": "integer", "description": "Reject upload above this many bytes"},
    "expires_in": {"type": "integer", "description": "Ticket TTL seconds (default 300)"},
    "overwrite": {"type": "boolean", "description": "If false, refuse when dest_path exists (default true)"}
  },
  "required": ["dest_path"],
  "additionalProperties": false
}`,
	},
	{
		Name: "prepare_download",
		Description: "Mint a one-shot ticket URL for downloading bytes from a server path.\n\n" +
			"Workflow: this tool RETURNS a ticket URL; it does NOT push to the " +
			"client. The client must then fetch via:\n" +
			"    curl -o /local/path <ticket_url>\n" +
			"Tickets are single-use and expire (default MYMCP_TRANSFER_DEFAULT_TTL_SEC=300s).",
		SchemaJSON: `{
  "type": "object",
  "properties": {
    "src_path": {"type": "string", "description": "Absolute server path to read from"},
    "expires_in": {"type": "integer", "description": "Ticket TTL seconds (default 300)"}
  },
  "required": ["src_path"],
  "additionalProperties": false
}`,
	},
	{
		Name:        "server_overview",
		Description: "Return a maintained map of this server's services, apps, data, and recent changes.",
		SchemaJSON:  `{"type": "object", "properties": {}, "additionalProperties": false}`,
	},
}

// mustSchema validates the raw JSON and returns it as json.RawMessage suitable
// for Tool.InputSchema (which is typed as any). Panics on bad JSON or a schema
// that is not a JSON object with type "object", so startup catches embedded
// typos immediately.
func mustSchema(raw string) json.RawMessage {
	var m map[string]any
	if err := json.Unmarshal([]byte(raw), &m); err != nil {
		panic(fmt.Sprintf("bad embedded schema: %v", err))
	}
	if m["type"] != "object" {
		panic(fmt.Sprintf("embedded schema must be a JSON object with type=object: %s", raw))
	}
	return json.RawMessage(raw)
}
