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
