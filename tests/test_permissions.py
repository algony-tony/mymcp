from mymcp.mcp_server import (
    ALL_TOOLS,
    READ_TOOLS,
    WRITE_TOOLS,
    check_tool_permission,
    filter_tools_by_role,
)


def test_read_tools_and_write_tools_cover_all():
    assert READ_TOOLS | WRITE_TOOLS == ALL_TOOLS


def test_filter_tools_ro_only_returns_read_tools():
    tools = filter_tools_by_role("ro")
    tool_names = {t.name for t in tools}
    assert tool_names == READ_TOOLS


def test_filter_tools_rw_returns_all_tools():
    tools = filter_tools_by_role("rw")
    tool_names = {t.name for t in tools}
    assert tool_names == ALL_TOOLS


def test_check_permission_ro_read_tool_allowed():
    err = check_tool_permission("read_file", "ro")
    assert err is None


def test_check_permission_ro_write_tool_denied():
    err = check_tool_permission("bash_execute", "ro")
    assert err is not None
    assert "rw" in err


def test_check_permission_rw_write_tool_allowed():
    err = check_tool_permission("bash_execute", "rw")
    assert err is None


def test_check_permission_unknown_tool():
    err = check_tool_permission("nonexistent", "rw")
    assert err is not None
