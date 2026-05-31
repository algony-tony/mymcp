import json
from unittest.mock import patch

import pytest

from mymcp.mcp_server import _current_audit_info, _extract_params, call_tool, dispatch_tool


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
    token = _current_audit_info.set(
        {
            "token_name": "test-client",
            "role": "rw",
            "ip": "127.0.0.1",
        }
    )
    with patch("mymcp.mcp_server.log_tool_call"):
        yield
    _current_audit_info.reset(token)


@pytest.fixture
def set_ro_audit_info():
    token = _current_audit_info.set(
        {
            "token_name": "ro-client",
            "role": "ro",
            "ip": "127.0.0.1",
        }
    )
    with patch("mymcp.mcp_server.log_tool_call"):
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
    token = _current_audit_info.set(
        {
            "token_name": "client-x",
            "role": "rw",
            "ip": "10.1.2.3",
        }
    )
    try:
        with patch("mymcp.mcp_server.log_tool_call") as mock_log:
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
    token = _current_audit_info.set(
        {
            "token_name": "ro-bot",
            "role": "ro",
            "ip": "192.168.0.1",
        }
    )
    try:
        with patch("mymcp.mcp_server.log_tool_call") as mock_log:
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
    with patch("mymcp.mcp_server.log_tool_call") as mock_log:
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
    with patch("mymcp.mcp_server.log_tool_call") as mock_log:
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
    with patch("mymcp.mcp_server.log_tool_call") as mock_log:
        await call_tool("bash_execute", {"command": "true"})
        kwargs = mock_log.call_args.kwargs
        assert kwargs["result"] == "ok"
        assert kwargs["error_code"] is None


@pytest.mark.anyio
async def test_call_tool_bash_timeout_audit(set_audit_info):
    """bash timeout should be logged as error."""
    with patch("mymcp.mcp_server.log_tool_call") as mock_log:
        results = await call_tool("bash_execute", {"command": "sleep 10", "timeout": 1})
        data = json.loads(results[0].text)
        assert data["timed_out"] is True

        kwargs = mock_log.call_args.kwargs
        assert kwargs["result"] == "error"
        assert kwargs["error_code"] == "TimeoutError"


@pytest.mark.anyio
async def test_call_tool_unhandled_exception(set_audit_info):
    """Unhandled exception in dispatch should return InternalError."""
    with patch("mymcp.mcp_server.dispatch_tool", side_effect=RuntimeError("boom")):
        with patch("mymcp.mcp_server.log_tool_call") as mock_log:
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
    with patch("mymcp.mcp_server.log_tool_call"):
        results = await call_tool("glob", None)
        # glob with no pattern will likely error, but should not crash
        data = json.loads(results[0].text)
        assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# _extract_params
# ---------------------------------------------------------------------------


def test_extract_params_omits_content():
    params = _extract_params(
        "write_file",
        {
            "file_path": "/tmp/x",
            "content": "a" * 10000,
        },
    )
    assert params["file_path"] == "/tmp/x"
    assert "10000 chars" in params["content"]


def test_extract_params_omits_old_new_string():
    params = _extract_params(
        "edit_file",
        {
            "file_path": "/tmp/x",
            "old_string": "abc",
            "new_string": "def",
        },
    )
    assert "3 chars" in params["old_string"]
    assert "3 chars" in params["new_string"]


def test_extract_params_keeps_normal_fields():
    params = _extract_params(
        "bash_execute",
        {
            "command": "ls -la",
            "timeout": 30,
        },
    )
    assert params == {"command": "ls -la", "timeout": 30}


# ---------------------------------------------------------------------------
# list_tools — via _current_audit_info contextvar
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_tools_ro_role():
    """list_tools should return only read tools for ro role."""
    from mymcp.mcp_server import READ_TOOLS, _current_audit_info, list_tools

    token = _current_audit_info.set(
        {
            "token_name": "ro-user",
            "role": "ro",
            "ip": "127.0.0.1",
        }
    )
    try:
        tools = await list_tools()
        tool_names = {t.name for t in tools}
        assert tool_names == READ_TOOLS
    finally:
        _current_audit_info.reset(token)


@pytest.mark.anyio
async def test_list_tools_rw_role():
    """list_tools should return all tools for rw role."""
    from mymcp.mcp_server import ALL_TOOLS, _current_audit_info, list_tools

    token = _current_audit_info.set(
        {
            "token_name": "rw-user",
            "role": "rw",
            "ip": "127.0.0.1",
        }
    )
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
    with patch("mymcp.mcp_server.dispatch_tool", return_value="plain text not json"):
        with patch("mymcp.mcp_server.log_tool_call") as mock_log:
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
        with patch("mymcp.mcp_server.log_tool_call") as mock_log:
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
    with (
        patch(
            "mymcp.mcp_server.dispatch_tool",
            return_value=json.dumps({"content": "plain", "total_lines": 1, "truncated": False}),
        ),
        patch("mymcp.mcp_server.log_tool_call") as mock_log,
    ):
        await call_tool("read_file", {"file_path": "/tmp/x"})
        kwargs = mock_log.call_args.kwargs
        assert kwargs["result"] == "ok"
        assert kwargs["error_code"] is None


@pytest.mark.anyio
async def test_call_tool_dispatch_success_true_is_ok(set_audit_info):
    """Explicit success=True must log as ok (not treated as error)."""
    with (
        patch(
            "mymcp.mcp_server.dispatch_tool",
            return_value=json.dumps({"success": True, "bytes_written": 5}),
        ),
        patch("mymcp.mcp_server.log_tool_call") as mock_log,
    ):
        await call_tool("write_file", {"file_path": "/tmp/y", "content": "hi"})
        kwargs = mock_log.call_args.kwargs
        assert kwargs["result"] == "ok"


# ---------------------------------------------------------------------------
# dispatch_tool default arguments — pin down every .get() default
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_dispatch_bash_default_working_dir_is_root():
    """No working_dir arg → cwd must be '/' (kills mutation of the '/' default)."""
    result = await dispatch_tool("bash_execute", {"command": "pwd"})
    data = json.loads(result)
    assert data["stdout"].strip() == "/"


@pytest.mark.anyio
async def test_dispatch_bash_default_timeout_succeeds_quickly():
    """No timeout arg → default 30s applies; a fast command must succeed."""
    result = await dispatch_tool("bash_execute", {"command": "true"})
    data = json.loads(result)
    assert data["exit_code"] == 0
    assert data["timed_out"] is False


@pytest.mark.anyio
async def test_dispatch_bash_timeout_capped_at_600():
    """timeout > 600 is clamped to 600 (kills `min(..., 600)` boundary)."""
    with patch("mymcp.mcp_server.run_bash_execute") as mock_run:
        mock_run.return_value = {"stdout": "", "stderr": "", "exit_code": 0, "timed_out": False}
        await dispatch_tool("bash_execute", {"command": "x", "timeout": 99999})
        assert mock_run.call_args.kwargs["timeout"] == 600


@pytest.mark.anyio
async def test_dispatch_read_file_default_offset_is_one(tmp_path):
    """No offset → first data line numbered 1 (kills mutation of default 1)."""
    f = tmp_path / "f.txt"
    f.write_text("alpha\nbeta\ngamma\n")
    result = await dispatch_tool("read_file", {"file_path": str(f)})
    data = json.loads(result)
    # content format is "%4d\t<line>"
    assert data["content"].startswith("   1\talpha")


@pytest.mark.anyio
async def test_dispatch_read_file_limit_clamped(tmp_path):
    """limit greatly above READ_FILE_MAX_LIMIT is clamped."""
    from mymcp import config

    f = tmp_path / "f.txt"
    f.write_text("x\n")
    with patch("mymcp.mcp_server.read_file") as mock_rf:
        mock_rf.return_value = {"content": "", "total_lines": 0, "truncated": False}
        await dispatch_tool("read_file", {"file_path": str(f), "limit": 999_999})
        assert mock_rf.call_args.kwargs["limit"] == config.READ_FILE_MAX_LIMIT


@pytest.mark.anyio
async def test_dispatch_grep_default_output_mode_is_content(tmp_path):
    """No output_mode → 'content' branch yields file:line:text formatted results."""
    f = tmp_path / "f.txt"
    f.write_text("hello world\n")
    result = await dispatch_tool("grep", {"pattern": "hello", "path": str(f)})
    data = json.loads(result)
    # content mode emits "path:lineno:line"
    assert f"{f}:1:" in data["results"]


@pytest.mark.anyio
async def test_dispatch_grep_default_case_insensitive_false(tmp_path):
    """Default case_insensitive=False — uppercase pattern must not match lowercase."""
    f = tmp_path / "f.txt"
    f.write_text("hello\n")
    result = await dispatch_tool("grep", {"pattern": "HELLO", "path": str(f)})
    data = json.loads(result)
    assert data["match_count"] == 0


@pytest.mark.anyio
async def test_dispatch_grep_case_insensitive_true(tmp_path):
    """Explicit case_insensitive=True must match across case."""
    f = tmp_path / "f.txt"
    f.write_text("hello\n")
    result = await dispatch_tool(
        "grep", {"pattern": "HELLO", "path": str(f), "case_insensitive": True}
    )
    data = json.loads(result)
    assert data["match_count"] >= 1


@pytest.mark.anyio
async def test_dispatch_edit_file_default_replace_all_false(tmp_path):
    """Default replace_all=False — ambiguous match returns AmbiguousMatch."""
    f = tmp_path / "f.txt"
    f.write_text("a a a")
    result = await dispatch_tool(
        "edit_file", {"file_path": str(f), "old_string": "a", "new_string": "b"}
    )
    data = json.loads(result)
    assert data["success"] is False
    assert data["error"] == "AmbiguousMatch"


@pytest.mark.anyio
async def test_dispatch_grep_max_results_clamped(tmp_path):
    """max_results > GREP_MAX_RESULTS must be clamped."""
    from mymcp import config

    with patch("mymcp.mcp_server.grep_files") as mock_grep:
        mock_grep.return_value = {"results": "", "match_count": 0}
        await dispatch_tool("grep", {"pattern": "x", "path": str(tmp_path), "max_results": 999_999})
        assert mock_grep.call_args.kwargs["max_results"] == config.GREP_MAX_RESULTS


@pytest.mark.anyio
async def test_dispatch_unknown_tool_exact_error():
    """UnknownTool error message must include the requested tool name."""
    result = await dispatch_tool("totally_made_up_tool", {})
    data = json.loads(result)
    assert data["success"] is False
    assert data["error"] == "UnknownTool"
    assert "totally_made_up_tool" in data["message"]


@pytest.mark.anyio
async def test_dispatch_server_overview_disabled_when_no_supervisor():
    """No supervisor → RecorderDisabled with exact error code."""
    import mymcp.mcp_server as mcp

    saved = mcp._recorder_supervisor
    mcp._recorder_supervisor = None
    try:
        result = await dispatch_tool("server_overview", {})
        data = json.loads(result)
        assert data["success"] is False
        assert data["error"] == "RecorderDisabled"
    finally:
        mcp._recorder_supervisor = saved


# ---------------------------------------------------------------------------
# call_tool — output_payload assembly (kills bash/write/edit branches)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_call_tool_bash_ok_audit_output_has_stdout_and_exit_code(set_audit_info):
    """bash_execute success → output_payload carries exit_code and timed_out=False."""
    with patch("mymcp.mcp_server.log_tool_call") as mock_log:
        await call_tool("bash_execute", {"command": "echo hello"})
        kwargs = mock_log.call_args.kwargs
        assert kwargs["result"] == "ok"
        payload = kwargs["output"]
        assert payload is not None
        assert payload["exit_code"] == 0
        assert payload["timed_out"] is False


@pytest.mark.anyio
async def test_call_tool_write_file_audit_output_records_path_and_size(set_audit_info, tmp_path):
    """write_file success → output_payload carries the path/size."""
    target = str(tmp_path / "w.txt")
    with patch("mymcp.mcp_server.log_tool_call") as mock_log:
        await call_tool("write_file", {"file_path": target, "content": "abc"})
        kwargs = mock_log.call_args.kwargs
        assert kwargs["result"] == "ok"
        assert kwargs["output"] is not None
        # write_file_output structure includes the path & bytes
        assert target in json.dumps(kwargs["output"])


@pytest.mark.anyio
async def test_call_tool_edit_file_audit_output_reports_hunk_count(set_audit_info, tmp_path):
    """edit_file success → hunk_count == replacements."""
    f = tmp_path / "e.txt"
    f.write_text("foo bar")
    with patch("mymcp.mcp_server.log_tool_call") as mock_log:
        await call_tool(
            "edit_file",
            {"file_path": str(f), "old_string": "foo", "new_string": "baz"},
        )
        kwargs = mock_log.call_args.kwargs
        assert kwargs["result"] == "ok"
        assert kwargs["output"]["hunk_count"] == 1


@pytest.mark.anyio
async def test_call_tool_bash_stderr_truncated_to_200(set_audit_info):
    """Long stderr from non-zero bash is truncated to 200 chars in audit log."""
    long_stderr = "x" * 500
    fake_result = json.dumps(
        {"stdout": "", "stderr": long_stderr, "exit_code": 7, "timed_out": False}
    )
    with (
        patch("mymcp.mcp_server.dispatch_tool", return_value=fake_result),
        patch("mymcp.mcp_server.log_tool_call") as mock_log,
    ):
        await call_tool("bash_execute", {"command": "x"})
        kwargs = mock_log.call_args.kwargs
        assert kwargs["error_code"] == "ExitCode:7"
        assert len(kwargs["error_message"]) == 200


@pytest.mark.anyio
async def test_call_tool_read_only_role_blocked_from_write(set_ro_audit_info):
    """ro role must be blocked from write_file — message must name the tool + 'rw'."""
    results = await call_tool("write_file", {"file_path": "/tmp/x", "content": "y"})
    data = json.loads(results[0].text)
    assert data["error"] == "PermissionDenied"
    assert "write_file" in data["message"]
    assert "rw" in data["message"]


@pytest.mark.anyio
async def test_call_tool_unknown_tool_via_call_tool_path(set_audit_info):
    """Unknown tool name routed through call_tool returns PermissionDenied
    (since unknown tools are not in ALL_TOOLS, check_tool_permission rejects them)."""
    results = await call_tool("not_a_tool", {})
    data = json.loads(results[0].text)
    assert data["error"] == "PermissionDenied"
    assert "Unknown tool" in data["message"]
