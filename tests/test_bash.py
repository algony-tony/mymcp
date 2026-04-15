import pytest
from tools.bash import run_bash_execute


@pytest.mark.anyio
async def test_simple_command_succeeds():
    result = await run_bash_execute("echo hello")
    assert result["stdout"].strip() == "hello"
    assert result["stderr"] == ""
    assert result["exit_code"] == 0
    assert result["timed_out"] is False


@pytest.mark.anyio
async def test_nonzero_exit_code():
    result = await run_bash_execute("exit 42", working_dir="/tmp")
    assert result["exit_code"] == 42


@pytest.mark.anyio
async def test_stderr_captured():
    result = await run_bash_execute("ls /path_that_does_not_exist_xyz")
    assert result["exit_code"] != 0
    assert len(result["stderr"]) > 0


@pytest.mark.anyio
async def test_working_dir_is_respected(tmp_path):
    result = await run_bash_execute("pwd", working_dir=str(tmp_path))
    assert result["stdout"].strip() == str(tmp_path)


@pytest.mark.anyio
async def test_timeout_kills_process():
    result = await run_bash_execute("sleep 10", timeout=1)
    assert result["timed_out"] is True
    assert result["exit_code"] == -1


@pytest.mark.anyio
async def test_output_truncated_when_over_limit():
    result = await run_bash_execute(
        'python3 -c "print(\'x\' * 200000)"',
        timeout=10,
        max_output_bytes=1000,
    )
    assert "[TRUNCATED" in result["stdout"]


@pytest.mark.anyio
async def test_bad_working_dir_returns_error():
    result = await run_bash_execute("ls", working_dir="/nonexistent_dir_xyz_abc")
    assert result.get("success") is False
    assert "error" in result


@pytest.mark.anyio
async def test_permission_denied_working_dir(tmp_path):
    d = tmp_path / "noaccess"
    d.mkdir()
    d.chmod(0o000)
    try:
        result = await run_bash_execute("ls", working_dir=str(d))
        assert result.get("success") is False
        assert result["error"] == "PermissionError"
    finally:
        d.chmod(0o755)
