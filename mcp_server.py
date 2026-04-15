import contextvars
import json
import logging
import time
import traceback

from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp import types

import config
from audit import log_tool_call
from tools.bash import run_bash_execute
from tools.files import read_file, write_file, edit_file, glob_files, grep_files

logger = logging.getLogger("mymcp")

# ---------------------------------------------------------------------------
# Context variable: set by auth middleware, read by tool handlers
# ---------------------------------------------------------------------------
_current_audit_info: contextvars.ContextVar[dict] = contextvars.ContextVar(
    "_current_audit_info",
    default={"token_name": "unknown", "role": "rw", "ip": "unknown"},
)

# ---------------------------------------------------------------------------
# Tool role sets
# ---------------------------------------------------------------------------
READ_TOOLS: set[str] = {"read_file", "glob", "grep"}
WRITE_TOOLS: set[str] = {"bash_execute", "write_file", "edit_file"}
ALL_TOOLS: set[str] = READ_TOOLS | WRITE_TOOLS

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

def _build_tool_definitions() -> dict[str, types.Tool]:
    """Return all tool definitions keyed by name."""
    return {
        "bash_execute": types.Tool(
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
        "read_file": types.Tool(
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
            },
        ),
        "edit_file": types.Tool(
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
            },
        ),
        "grep": types.Tool(
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
    }


_TOOL_DEFS = _build_tool_definitions()

# ---------------------------------------------------------------------------
# Permission helpers
# ---------------------------------------------------------------------------

def filter_tools_by_role(role: str) -> list[types.Tool]:
    """Return tool definitions visible to the given role."""
    if role == "rw":
        allowed = ALL_TOOLS
    else:
        allowed = READ_TOOLS
    return [t for name, t in _TOOL_DEFS.items() if name in allowed]


def check_tool_permission(tool_name: str, role: str) -> str | None:
    """Return None if allowed, or an error message string if denied."""
    if tool_name not in ALL_TOOLS:
        return f"Unknown tool: {tool_name}"
    if role == "rw":
        return None
    if tool_name in READ_TOOLS:
        return None
    return f"Permission denied: tool '{tool_name}' requires rw role"


def _extract_params(name: str, args: dict) -> dict:
    """Extract audit-safe params (omit large content fields)."""
    omit_keys = {"content", "old_string", "new_string"}
    safe = {}
    for k, v in args.items():
        if k in omit_keys:
            safe[k] = f"<{len(str(v))} chars>"
        else:
            safe[k] = v
    return safe


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

server = Server("linux-server")
session_manager = StreamableHTTPSessionManager(server)


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    info = _current_audit_info.get()
    role = info.get("role", "rw")
    return filter_tools_by_role(role)


@server.call_tool()
async def call_tool(name: str, arguments: dict | None) -> list[types.TextContent]:
    args = arguments or {}
    info = _current_audit_info.get()
    token_name = info.get("token_name", "unknown")
    role = info.get("role", "rw")
    ip = info.get("ip", "unknown")

    # Permission check
    perm_err = check_tool_permission(name, role)
    if perm_err is not None:
        log_tool_call(
            token_name=token_name,
            role=role,
            ip=ip,
            tool=name,
            params=_extract_params(name, args),
            result="denied",
            reason=perm_err,
        )
        error_result = json.dumps({"success": False, "error": "PermissionDenied", "message": perm_err})
        return [types.TextContent(type="text", text=error_result)]

    # Execute tool
    t0 = time.monotonic()
    try:
        result_json = await dispatch_tool(name, args)
    except Exception:
        duration_ms = int((time.monotonic() - t0) * 1000)
        tb = traceback.format_exc()
        logger.error("Unhandled exception in tool %s: %s", name, tb)
        error_data = {
            "success": False,
            "error": "InternalError",
            "message": f"Tool '{name}' failed with an unexpected error",
        }
        log_tool_call(
            token_name=token_name,
            role=role,
            ip=ip,
            tool=name,
            params=_extract_params(name, args),
            result="error",
            error_code="InternalError",
            error_message=f"Unhandled exception in {name}",
            duration_ms=duration_ms,
        )
        return [types.TextContent(type="text", text=json.dumps(error_data))]

    duration_ms = int((time.monotonic() - t0) * 1000)

    # Determine result status for audit
    error_code = None
    error_message = None
    try:
        result_data = json.loads(result_json)
        if result_data.get("success", True) is False:
            result_status = "error"
            error_code = result_data.get("error", "")
            error_message = result_data.get("message", "")
        else:
            result_status = "ok"
    except (json.JSONDecodeError, AttributeError):
        result_status = "ok"

    if result_status == "error":
        logger.warning(
            "Tool %s returned error: [%s] %s (token=%s, ip=%s)",
            name, error_code, error_message, token_name, ip,
        )

    log_tool_call(
        token_name=token_name,
        role=role,
        ip=ip,
        tool=name,
        params=_extract_params(name, args),
        result=result_status,
        error_code=error_code,
        error_message=error_message,
        duration_ms=duration_ms,
    )

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
