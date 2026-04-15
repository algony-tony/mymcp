import pytest
import json
from unittest.mock import patch, AsyncMock

from mcp_server import dispatch_tool, call_tool, _current_audit_info, _extract_params


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
async def test_call_tool_permission_denied(set_ro_audit_info):
    results = await call_tool("bash_execute", {"command": "echo no"})
    data = json.loads(results[0].text)
    assert data["success"] is False
    assert data["error"] == "PermissionDenied"


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
        assert "ExitCode:42" in kwargs["error_code"]


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
