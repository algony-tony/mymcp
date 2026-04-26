import os

HOST = os.getenv("MCP_HOST", "0.0.0.0")
PORT = int(os.getenv("MCP_PORT", "8765"))
TOKEN_FILE = os.getenv("MCP_TOKEN_FILE", "./tokens.json")
ADMIN_TOKEN = os.getenv("MCP_ADMIN_TOKEN", "")

# bash_execute output limits
BASH_MAX_OUTPUT_BYTES = int(os.getenv("MCP_BASH_MAX_OUTPUT_BYTES", "102400"))           # 100 KB default
BASH_MAX_OUTPUT_BYTES_HARD = int(os.getenv("MCP_BASH_MAX_OUTPUT_BYTES_HARD", "1048576"))  # 1 MB hard cap

# read_file limits
READ_FILE_DEFAULT_LIMIT = int(os.getenv("MCP_READ_FILE_DEFAULT_LIMIT", "2000"))         # lines
READ_FILE_MAX_LIMIT = int(os.getenv("MCP_READ_FILE_MAX_LIMIT", "50000"))                # lines
READ_FILE_MAX_LINE_BYTES = int(os.getenv("MCP_READ_FILE_MAX_LINE_BYTES", "32768"))      # 32 KB per line

# write_file limit
WRITE_FILE_MAX_BYTES = int(os.getenv("MCP_WRITE_FILE_MAX_BYTES", str(10 * 1024 * 1024)))  # 10 MB

# edit_file limit
EDIT_STRING_MAX_BYTES = int(os.getenv("MCP_EDIT_STRING_MAX_BYTES", str(1024 * 1024)))   # 1 MB per old/new string

# glob limit
GLOB_MAX_RESULTS = int(os.getenv("MCP_GLOB_MAX_RESULTS", "1000"))

# grep limits
GREP_DEFAULT_MAX_RESULTS = int(os.getenv("MCP_GREP_DEFAULT_MAX_RESULTS", "500"))
GREP_MAX_RESULTS = int(os.getenv("MCP_GREP_MAX_RESULTS", "5000"))

# Audit logging
AUDIT_ENABLED = os.getenv("MCP_AUDIT_ENABLED", "false").lower() in ("true", "1", "yes")
AUDIT_LOG_DIR = os.getenv("MCP_AUDIT_LOG_DIR", "/var/log/mymcp")
AUDIT_MAX_BYTES = int(os.getenv("MCP_AUDIT_MAX_BYTES", str(10 * 1024 * 1024)))  # 10MB
AUDIT_BACKUP_COUNT = int(os.getenv("MCP_AUDIT_BACKUP_COUNT", "5"))

# Protected paths (APP_DIR and AUDIT_LOG_DIR are always protected)
APP_DIR = os.getenv("MCP_APP_DIR", "/opt/mymcp")
_extra = os.getenv("MCP_PROTECTED_PATHS", "")
PROTECTED_PATHS: list[str] = [APP_DIR, AUDIT_LOG_DIR]
if _extra.strip():
    PROTECTED_PATHS.extend(p.strip() for p in _extra.split(",") if p.strip())

_VERSION_FILE = os.path.join(os.path.dirname(__file__), "VERSION")


def _read_version() -> str:
    for path in [os.path.join(APP_DIR, "VERSION"), _VERSION_FILE]:
        try:
            with open(path) as f:
                return f.read().strip()
        except OSError:
            pass
    return "unknown"


APP_VERSION: str = _read_version()

METRICS_TOKEN: str = os.getenv("MCP_METRICS_TOKEN", "")
