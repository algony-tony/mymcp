import pytest
import json
from unittest.mock import patch, AsyncMock

from mymcp.mcp_server import dispatch_tool, call_tool, _current_audit_info, _extract_params


@pytest.mark.anyio
async def test_dispatch_bash_execute():
    result = await dispatch_tool("bash_execute", {"command": "echo mcp_test"})
    data = json.loads(result)
    assert "mcp_test" in data["stdout"]
    assert data["exit_code"] == 0


@pytest.mark.anyio
async def test_dispatch_read_file(tmp_path):
    f = tmp_path / "hello.txt"
    f.write_text("hello mcp\n")
    result = await dispatch_tool("read_file", {"file_path": str(f)})
    data = json.loads(result)
    assert "hello mcp" in data["content"]


@pytest.mark.anyio
async def test_dispatch_write_file(tmp_path):
    path = str(tmp_path / "out.txt")
    result = await dispatch_tool("write_file", {"file_path": path, "content": "written"})
    data = json.loads(result)
    assert data["success"] is True
    assert (tmp_path / "out.txt").read_text() == "written"


@pytest.mark.anyio
async def test_dispatch_edit_file(tmp_path):
    f = tmp_path / "edit.txt"
    f.write_text("replace_me")
    result = await dispatch_tool(
        "edit_file",
        {"file_path": str(f), "old_string": "replace_me", "new_string": "replaced"},
    )
    data = json.loads(result)
    assert data["success"] is True


@pytest.mark.anyio
async def test_dispatch_glob(tmp_path):
    (tmp_path / "a.py").write_text("")
    result = await dispatch_tool("glob", {"pattern": "*.py", "path": str(tmp_path)})
    data = json.loads(result)
    assert data["count"] >= 1


@pytest.mark.anyio
async def test_dispatch_grep(tmp_path):
    (tmp_path / "f.txt").write_text("needle in haystack\n")
    result = await dispatch_tool("grep", {"pattern": "needle", "path": str(tmp_path)})
    data = json.loads(result)
    assert data["match_count"] >= 1


@pytest.mark.anyio
async def test_dispatch_unknown_tool():
    result = await dispatch_tool("nonexistent_tool", {})
    data = json.loads(result)
    assert data["success"] is False
    assert data["error"] == "UnknownTool"


# ---------------------------------------------------------------------------
# call_tool — full pipeline (permission check + dispatch + audit)
# ---------------------------------------------------------------------------

@pytest.fixture
def set_audit_info():
    """Set contextvar and disable audit file logging for call_tool tests."""
    token = _current_audit_info.set({
        "token_name": "test-client",
        "role": "rw",
        "ip": "127.0.0.1",
    })
    with patch("mcp_server.log_tool_call"):
        yield
    _current_audit_info.reset(token)


@pytest.fixture
def set_ro_audit_info():
    token = _current_audit_info.set({
        "token_name": "ro-client",
        "role": "ro",
        "ip": "127.0.0.1",
    })
    with patch("mcp_server.log_tool_call"):
        yield
    _current_audit_info.reset(token)


@pytest.mark.anyio
async def test_call_tool_success(set_audit_info):
    results = await call_tool("bash_execute", {"command": "echo ok"})
    data = json.loads(results[0].text)
    assert data["exit_code"] == 0


@pytest.mark.anyio
async def test_call_tool_success_audit_fields():
    """Successful call must log result='ok' with all identity + timing fields."""
    token = _current_audit_info.set({
        "token_name": "client-x",
        "role": "rw",
        "ip": "10.1.2.3",
    })
    try:
        with patch("mcp_server.log_tool_call") as mock_log:
            await call_tool("bash_execute", {"command": "echo ok"})
            mock_log.assert_called_once()
            kwargs = mock_log.call_args.kwargs
            assert kwargs["result"] == "ok"
            assert kwargs["token_name"] == "client-x"
            assert kwargs["role"] == "rw"
            assert kwargs["ip"] == "10.1.2.3"
            assert kwargs["tool"] == "bash_execute"
            assert kwargs["duration_ms"] is not None
            assert kwargs["duration_ms"] >= 0
            assert kwargs["error_code"] is None
            assert kwargs["error_message"] is None
    finally:
        _current_audit_info.reset(token)


@pytest.mark.anyio
async def test_call_tool_permission_denied(set_ro_audit_info):
    results = await call_tool("bash_execute", {"command": "echo no"})
    data = json.loads(results[0].text)
    assert data["success"] is False
    assert data["error"] == "PermissionDenied"


@pytest.mark.anyio
async def test_call_tool_permission_denied_audit_fields():
    """Denied call must log result='denied' with reason, no duration."""
    token = _current_audit_info.set({
        "token_name": "ro-bot",
        "role": "ro",
        "ip": "192.168.0.1",
    })
    try:
        with patch("mcp_server.log_tool_call") as mock_log:
            await call_tool("write_file", {"file_path": "/tmp/x", "content": "y"})
            kwargs = mock_log.call_args.kwargs
            assert kwargs["result"] == "denied"
            assert kwargs["token_name"] == "ro-bot"
            assert kwargs["role"] == "ro"
            assert kwargs["ip"] == "192.168.0.1"
            assert kwargs["tool"] == "write_file"
            assert kwargs["reason"] is not None
            assert "write_file" in kwargs["reason"]
            assert "rw" in kwargs["reason"]
    finally:
        _current_audit_info.reset(token)


@pytest.mark.anyio
async def test_call_tool_tool_error_audit(set_audit_info):
    """Tool returning success:False should be logged with error details."""
    with patch("mcp_server.log_tool_call") as mock_log:
        results = await call_tool("read_file", {"file_path": "/nonexistent_xyz"})
        data = json.loads(results[0].text)
        assert data["success"] is False

        mock_log.assert_called_once()
        kwargs = mock_log.call_args.kwargs
        assert kwargs["result"] == "error"
        assert kwargs["error_code"] == "FileNotFoundError"
        assert "nonexistent" in kwargs["error_message"]


@pytest.mark.anyio
async def test_call_tool_bash_nonzero_audit(set_audit_info):
    """bash non-zero exit should be logged as error with exit code."""
    with patch("mcp_server.log_tool_call") as mock_log:
        results = await call_tool("bash_execute", {"command": "exit 42"})
        data = json.loads(results[0].text)
        assert data["exit_code"] == 42

        kwargs = mock_log.call_args.kwargs
        assert kwargs["result"] == "error"
        assert kwargs["error_code"] == "ExitCode:42"
        assert kwargs["duration_ms"] is not None


@pytest.mark.anyio
async def test_call_tool_bash_zero_exit_is_ok(set_audit_info):
    """bash exit_code == 0 must log as 'ok', not error (boundary case)."""
    with patch("mcp_server.log_tool_call") as mock_log:
        await call_tool("bash_execute", {"command": "true"})
        kwargs = mock_log.call_args.kwargs
        assert kwargs["result"] == "ok"
        assert kwargs["error_code"] is None


@pytest.mark.anyio
async def test_call_tool_bash_timeout_audit(set_audit_info):
    """bash timeout should be logged as error."""
    with patch("mcp_server.log_tool_call") as mock_log:
        results = await call_tool("bash_execute", {"command": "sleep 10", "timeout": 1})
        data = json.loads(results[0].text)
        assert data["timed_out"] is True

        kwargs = mock_log.call_args.kwargs
        assert kwargs["result"] == "error"
        assert kwargs["error_code"] == "TimeoutError"


@pytest.mark.anyio
async def test_call_tool_unhandled_exception(set_audit_info):
    """Unhandled exception in dispatch should return InternalError."""
    with patch("mcp_server.dispatch_tool", side_effect=RuntimeError("boom")):
        with patch("mcp_server.log_tool_call") as mock_log:
            results = await call_tool("bash_execute", {"command": "echo x"})
            data = json.loads(results[0].text)
            assert data["success"] is False
            assert data["error"] == "InternalError"

            kwargs = mock_log.call_args.kwargs
            assert kwargs["result"] == "error"
            assert kwargs["error_code"] == "InternalError"


@pytest.mark.anyio
async def test_call_tool_null_arguments(set_audit_info):
    """arguments=None should be handled gracefully."""
    with patch("mcp_server.log_tool_call"):
        results = await call_tool("glob", None)
        # glob with no pattern will likely error, but should not crash
        data = json.loads(results[0].text)
        assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# _extract_params
# ---------------------------------------------------------------------------

def test_extract_params_omits_content():
    params = _extract_params("write_file", {
        "file_path": "/tmp/x",
        "content": "a" * 10000,
    })
    assert params["file_path"] == "/tmp/x"
    assert "10000 chars" in params["content"]


def test_extract_params_omits_old_new_string():
    params = _extract_params("edit_file", {
        "file_path": "/tmp/x",
        "old_string": "abc",
        "new_string": "def",
    })
    assert "3 chars" in params["old_string"]
    assert "3 chars" in params["new_string"]


def test_extract_params_keeps_normal_fields():
    params = _extract_params("bash_execute", {
        "command": "ls -la",
        "timeout": 30,
    })
    assert params == {"command": "ls -la", "timeout": 30}


# ---------------------------------------------------------------------------
# list_tools — via _current_audit_info contextvar
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_list_tools_ro_role():
    """list_tools should return only read tools for ro role."""
    from mymcp.mcp_server import list_tools, _current_audit_info, READ_TOOLS
    token = _current_audit_info.set({
        "token_name": "ro-user", "role": "ro", "ip": "127.0.0.1",
    })
    try:
        tools = await list_tools()
        tool_names = {t.name for t in tools}
        assert tool_names == READ_TOOLS
    finally:
        _current_audit_info.reset(token)


@pytest.mark.anyio
async def test_list_tools_rw_role():
    """list_tools should return all tools for rw role."""
    from mymcp.mcp_server import list_tools, _current_audit_info, ALL_TOOLS
    token = _current_audit_info.set({
        "token_name": "rw-user", "role": "rw", "ip": "127.0.0.1",
    })
    try:
        tools = await list_tools()
        tool_names = {t.name for t in tools}
        assert tool_names == ALL_TOOLS
    finally:
        _current_audit_info.reset(token)


# ---------------------------------------------------------------------------
# call_tool — JSON decode error path
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_call_tool_non_json_result(set_audit_info):
    """When dispatch_tool returns non-JSON, result_status should be 'ok'."""
    with patch("mcp_server.dispatch_tool", return_value="plain text not json"):
        with patch("mcp_server.log_tool_call") as mock_log:
            results = await call_tool("bash_execute", {"command": "echo x"})
            assert results[0].text == "plain text not json"
            kwargs = mock_log.call_args.kwargs
            assert kwargs["result"] == "ok"


# ---------------------------------------------------------------------------
# Missing / malformed fields — defaults from .get() calls must behave right
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_call_tool_contextvar_defaults_when_fields_missing():
    """When audit contextvar dict is missing fields, audit log uses 'unknown'.

    This kills mutations that change the .get() defaults on
    token_name/role/ip in call_tool.
    """
    token = _current_audit_info.set({})  # empty dict — every .get() falls back
    try:
        with patch("mcp_server.log_tool_call") as mock_log:
            # role defaults to "rw" per the get() default, so this call is allowed
            await call_tool("bash_execute", {"command": "echo ok"})
            kwargs = mock_log.call_args.kwargs
            assert kwargs["token_name"] == "unknown"
            assert kwargs["role"] == "rw"
            assert kwargs["ip"] == "unknown"
    finally:
        _current_audit_info.reset(token)


@pytest.mark.anyio
async def test_call_tool_dispatch_result_without_success_field_is_ok(set_audit_info):
    """A dispatch result dict lacking a 'success' key must default to ok.

    Kills mutations flipping the default from True to False on
    result_data.get('success', True).
    """
    with patch(
        "mcp_server.dispatch_tool",
        return_value=json.dumps({"content": "plain", "total_lines": 1, "truncated": False}),
    ):
        with patch("mcp_server.log_tool_call") as mock_log:
            await call_tool("read_file", {"file_path": "/tmp/x"})
            kwargs = mock_log.call_args.kwargs
            assert kwargs["result"] == "ok"
            assert kwargs["error_code"] is None


@pytest.mark.anyio
async def test_call_tool_dispatch_success_true_is_ok(set_audit_info):
    """Explicit success=True must log as ok (not treated as error)."""
    with patch(
        "mcp_server.dispatch_tool",
        return_value=json.dumps({"success": True, "bytes_written": 5}),
    ):
        with patch("mcp_server.log_tool_call") as mock_log:
            await call_tool("write_file", {"file_path": "/tmp/y", "content": "hi"})
            kwargs = mock_log.call_args.kwargs
            assert kwargs["result"] == "ok"
