try:
    from prometheus_client import Counter, Histogram
    ENABLED = True
    TOOL_CALLS = Counter(
        "mymcp_tool_calls_total",
        "Total MCP tool calls",
        ["tool", "role", "result"],
    )
    TOOL_DURATION = Histogram(
        "mymcp_tool_duration_seconds",
        "MCP tool call duration",
        ["tool"],
        buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 30.0],
    )
    HTTP_REQUESTS = Counter(
        "mymcp_http_requests_total",
        "Total HTTP requests",
        ["path", "method", "status"],
    )
except ImportError:
    ENABLED = False
    TOOL_CALLS = TOOL_DURATION = HTTP_REQUESTS = None
