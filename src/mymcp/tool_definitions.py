"""Static MCP tool definitions.

Pure data: every value here is a literal description or JSON Schema. The module
is intentionally excluded from mutation testing — mutating description strings
produces survivors that can only be killed by brittle full-string assertions in
tests, which is not a meaningful signal of test quality.
"""

from mcp import types


def build_tool_definitions() -> dict[str, types.Tool]:
    """Return all tool definitions keyed by name."""
    return {
        "bash_execute": types.Tool(
            name="bash_execute",
            description=(
                "Execute any shell command on the Linux server. "
                "Stateless: each call is a fresh subprocess, no persistent shell state.\n\n"
                "WARNING: bash_execute is NOT subject to MYMCP_PROTECTED_PATHS. It can read "
                "or modify any path the service user can access (including audit logs and "
                "tokens.json). Untrusted clients should be issued ro tokens, which cannot "
                "call this tool.\n\n"
                "Defaults: working_dir='/' if omitted; timeout 30s (max 600s, clamped). "
                "On timeout, exit_code is -1 and timed_out is true."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run"},
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout seconds (default 30, max 600)",
                    },
                    "working_dir": {
                        "type": "string",
                        "description": "Working directory (default /)",
                    },
                    "max_output_bytes": {
                        "type": "integer",
                        "description": "Max stdout/stderr bytes each (default 102400)",
                    },
                },
                "required": ["command"],
                "additionalProperties": False,
            },
        ),
        "read_file": types.Tool(
            name="read_file",
            description="Read a file with line numbers. Supports pagination via offset/limit.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Absolute path to file"},
                    "offset": {"type": "integer", "description": "Start line 1-based (default 1)"},
                    "limit": {
                        "type": "integer",
                        "description": (
                            "Lines to read (default MYMCP_READ_FILE_DEFAULT_LIMIT=2000, "
                            "max MYMCP_READ_FILE_MAX_LIMIT=50000)"
                        ),
                    },
                },
                "required": ["file_path"],
                "additionalProperties": False,
            },
        ),
        "write_file": types.Tool(
            name="write_file",
            description="Create or overwrite a file. Max 10MB.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Absolute path"},
                    "content": {"type": "string", "description": "File content (max 10MB)"},
                },
                "required": ["file_path", "content"],
                "additionalProperties": False,
            },
        ),
        "edit_file": types.Tool(
            name="edit_file",
            description="Replace a string in a file. old_string must be unique unless replace_all=true.",  # noqa: E501
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "old_string": {"type": "string", "description": "String to find (max 1MB)"},
                    "new_string": {"type": "string", "description": "Replacement string (max 1MB)"},
                    "replace_all": {
                        "type": "boolean",
                        "description": "Replace every occurrence (default false)",
                    },
                },
                "required": ["file_path", "old_string", "new_string"],
                "additionalProperties": False,
            },
        ),
        "glob": types.Tool(
            name="glob",
            description="Find files by glob pattern, e.g. '**/*.py'. Results sorted by mtime desc.",
            inputSchema={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern, e.g. '**/*.log'"},
                    "path": {"type": "string", "description": "Root directory (default /)"},
                },
                "required": ["pattern"],
                "additionalProperties": False,
            },
        ),
        "prepare_upload": types.Tool(
            name="prepare_upload",
            description=(
                "Mint a one-shot ticket URL for uploading bytes to a server path.\n\n"
                "Workflow: this tool RETURNS a ticket URL; it does NOT pull from the "
                "client. The client must then upload via:\n"
                "    curl -X PUT --data-binary @/local/path <ticket_url>\n"
                "Tickets are single-use and expire (default MYMCP_TRANSFER_DEFAULT_TTL_SEC=300s)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dest_path": {
                        "type": "string",
                        "description": "Absolute server path to write to",
                    },
                    "max_bytes": {
                        "type": "integer",
                        "description": "Reject upload above this many bytes",
                    },
                    "expires_in": {
                        "type": "integer",
                        "description": "Ticket TTL seconds (default 300)",
                    },
                    "overwrite": {
                        "type": "boolean",
                        "description": "If false, refuse when dest_path exists (default true)",
                    },
                },
                "required": ["dest_path"],
                "additionalProperties": False,
            },
        ),
        "prepare_download": types.Tool(
            name="prepare_download",
            description=(
                "Mint a one-shot ticket URL for downloading bytes from a server path.\n\n"
                "Workflow: this tool RETURNS a ticket URL; it does NOT push to the "
                "client. The client must then fetch via:\n"
                "    curl -o /local/path <ticket_url>\n"
                "Tickets are single-use and expire (default MYMCP_TRANSFER_DEFAULT_TTL_SEC=300s)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "src_path": {
                        "type": "string",
                        "description": "Absolute server path to read from",
                    },
                    "expires_in": {
                        "type": "integer",
                        "description": "Ticket TTL seconds (default 300)",
                    },
                },
                "required": ["src_path"],
                "additionalProperties": False,
            },
        ),
        "grep": types.Tool(
            name="grep",
            description="Search file contents with regex. Uses ripgrep if installed, else Python fallback.",  # noqa: E501
            inputSchema={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern"},
                    "path": {
                        "type": "string",
                        "description": "File or directory to search (default /)",
                    },
                    "glob": {"type": "string", "description": "File filter e.g. '*.log'"},
                    "output_mode": {
                        "type": "string",
                        "enum": ["content", "files", "count"],
                        "description": "Output mode (default content)",
                    },
                    "context_lines": {
                        "type": "integer",
                        "description": "Lines of context (default 0)",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Max matches (default 500, max 5000)",
                    },
                    "case_insensitive": {
                        "type": "boolean",
                        "description": "Case-insensitive (default false)",
                    },
                },
                "required": ["pattern"],
                "additionalProperties": False,
            },
        ),
        "server_overview": types.Tool(
            name="server_overview",
            description="Return a maintained map of this server's services, apps, data, and recent changes.",  # noqa: E501
            inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
        ),
    }


TOOL_DEFS: dict[str, types.Tool] = build_tool_definitions()
