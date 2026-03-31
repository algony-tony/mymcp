import os

HOST = os.getenv("MCP_HOST", "0.0.0.0")
PORT = int(os.getenv("MCP_PORT", "8765"))
TOKEN_FILE = os.getenv("MCP_TOKEN_FILE", "./tokens.json")
ADMIN_TOKEN = os.getenv("MCP_ADMIN_TOKEN", "")

# bash_execute output limits
BASH_MAX_OUTPUT_BYTES = 102400        # 100 KB default
BASH_MAX_OUTPUT_BYTES_HARD = 1048576  # 1 MB hard cap

# read_file limits
READ_FILE_DEFAULT_LIMIT = 2000        # lines
READ_FILE_MAX_LIMIT = 10000           # lines
READ_FILE_MAX_LINE_BYTES = 4096       # bytes per line

# write_file limit
WRITE_FILE_MAX_BYTES = 10 * 1024 * 1024  # 10 MB

# edit_file limit
EDIT_STRING_MAX_BYTES = 1024 * 1024      # 1 MB per old/new string

# glob limit
GLOB_MAX_RESULTS = 1000

# grep limits
GREP_DEFAULT_MAX_RESULTS = 250
GREP_MAX_RESULTS = 5000
