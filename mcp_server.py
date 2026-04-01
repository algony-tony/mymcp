import json

from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp import types

import config
from tools.bash import run_bash_execute
from tools.files import read_file, write_file, edit_file, glob_files, grep_files

server = Server("linux-server")
session_manager = StreamableHTTPSessionManager(server)


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="bash_execute",
            description=(
                "Execute any shell command on the Linux server. "
                "Stateless: each call is a fresh subprocess, no persistent shell state."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run"},
                    "timeout": {"type": "integer", "description": "Timeout seconds (default 30, max 600)"},
                    "working_dir": {"type": "string", "description": "Working directory (default /)"},
                    "max_output_bytes": {"type": "integer", "description": "Max stdout/stderr bytes each (default 102400)"},
                },
                "required": ["command"],
            },
        ),
        types.Tool(
            name="read_file",
            description="Read a file with line numbers. Supports pagination via offset/limit.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Absolute path to file"},
                    "offset": {"type": "integer", "description": "Start line 1-based (default 1)"},
                    "limit": {"type": "integer", "description": "Lines to read (default 2000, max 10000)"},
                },
                "required": ["file_path"],
            },
        ),
        types.Tool(
            name="write_file",
            description=(
                "Create or overwrite a file. Max 10MB. "
                "For larger files use the /files/upload HTTP endpoint."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Absolute path"},
                    "content": {"type": "string", "description": "File content (max 10MB)"},
                },
                "required": ["file_path", "content"],
            },
        ),
        types.Tool(
            name="edit_file",
            description="Replace a string in a file. old_string must be unique unless replace_all=true.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "old_string": {"type": "string", "description": "String to find (max 1MB)"},
                    "new_string": {"type": "string", "description": "Replacement string (max 1MB)"},
                    "replace_all": {"type": "boolean", "description": "Replace every occurrence (default false)"},
                },
                "required": ["file_path", "old_string", "new_string"],
            },
        ),
        types.Tool(
            name="glob",
            description="Find files by glob pattern, e.g. '**/*.py'. Results sorted by mtime desc.",
            inputSchema={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern, e.g. '**/*.log'"},
                    "path": {"type": "string", "description": "Root directory (default /)"},
                },
                "required": ["pattern"],
            },
        ),
        types.Tool(
            name="grep",
            description="Search file contents with regex. Uses ripgrep if installed, else Python fallback.",
            inputSchema={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern"},
                    "path": {"type": "string", "description": "File or directory to search (default /)"},
                    "glob": {"type": "string", "description": "File filter e.g. '*.log'"},
                    "output_mode": {
                        "type": "string",
                        "enum": ["content", "files", "count"],
                        "description": "Output mode (default content)",
                    },
                    "context_lines": {"type": "integer", "description": "Lines of context (default 0)"},
                    "max_results": {"type": "integer", "description": "Max matches (default 250, max 5000)"},
                    "case_insensitive": {"type": "boolean", "description": "Case-insensitive (default false)"},
                },
                "required": ["pattern"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict | None) -> list[types.TextContent]:
    result_json = await dispatch_tool(name, arguments or {})
    return [types.TextContent(type="text", text=result_json)]


async def dispatch_tool(name: str, args: dict) -> str:
    """Dispatch to the appropriate tool function and return JSON string."""
    if name == "bash_execute":
        result = await run_bash_execute(
            command=args["command"],
            timeout=min(args.get("timeout", 30), 600),
            working_dir=args.get("working_dir", "/"),
            max_output_bytes=min(
                args.get("max_output_bytes", config.BASH_MAX_OUTPUT_BYTES),
                config.BASH_MAX_OUTPUT_BYTES_HARD,
            ),
        )
    elif name == "read_file":
        result = await read_file(
            file_path=args["file_path"],
            offset=args.get("offset", 1),
            limit=min(args.get("limit", config.READ_FILE_DEFAULT_LIMIT), config.READ_FILE_MAX_LIMIT),
        )
    elif name == "write_file":
        result = await write_file(
            file_path=args["file_path"],
            content=args["content"],
        )
    elif name == "edit_file":
        result = await edit_file(
            file_path=args["file_path"],
            old_string=args["old_string"],
            new_string=args["new_string"],
            replace_all=args.get("replace_all", False),
        )
    elif name == "glob":
        result = await glob_files(
            pattern=args["pattern"],
            path=args.get("path", "/"),
        )
    elif name == "grep":
        result = await grep_files(
            pattern=args["pattern"],
            path=args.get("path", "/"),
            glob=args.get("glob"),
            output_mode=args.get("output_mode", "content"),
            context_lines=args.get("context_lines", 0),
            max_results=min(
                args.get("max_results", config.GREP_DEFAULT_MAX_RESULTS),
                config.GREP_MAX_RESULTS,
            ),
            case_insensitive=args.get("case_insensitive", False),
        )
    else:
        result = {
            "success": False,
            "error": "UnknownTool",
            "message": f"No tool named '{name}'",
        }

    return json.dumps(result)
