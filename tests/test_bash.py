import pytest

from mymcp.tools.bash import run_bash_execute


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
    assert "timed out after 1s" in result["stderr"]


@pytest.mark.anyio
async def test_output_truncated_when_over_limit():
    result = await run_bash_execute(
        "python3 -c \"print('x' * 200000)\"",
        timeout=10,
        max_output_bytes=1000,
    )
    assert "[TRUNCATED" in result["stdout"]
    assert "showing first 1000 bytes" in result["stdout"]
    assert "total 200001 bytes" in result["stdout"]


@pytest.mark.anyio
async def test_bad_working_dir_returns_error():
    result = await run_bash_execute("ls", working_dir="/nonexistent_dir_xyz_abc")
    assert result["success"] is False
    assert result["error"] == "FileNotFoundError"
    assert "/nonexistent_dir_xyz_abc" in result["message"]


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


# ---------------------------------------------------------------------------
# Truncation / boundary mutation killers
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_output_not_truncated_at_exact_limit():
    """Output exactly == max_output_bytes must NOT be truncated.

    Kills `<= limit` → `< limit` and similar boundary mutations.
    """
    result = await run_bash_execute(
        "python3 -c \"import sys; sys.stdout.write('x' * 100)\"",
        timeout=10,
        max_output_bytes=100,
    )
    assert "[TRUNCATED" not in result["stdout"]
    assert len(result["stdout"]) == 100


@pytest.mark.anyio
async def test_output_truncated_one_byte_over_limit():
    """One byte over the limit must trigger truncation."""
    result = await run_bash_execute(
        "python3 -c \"import sys; sys.stdout.write('x' * 101)\"",
        timeout=10,
        max_output_bytes=100,
    )
    assert "[TRUNCATED" in result["stdout"]
    assert "total 101 bytes" in result["stdout"]
    assert "showing first 100 bytes" in result["stdout"]


@pytest.mark.anyio
async def test_timeout_message_includes_seconds():
    """Timeout stderr message must contain the seconds value."""
    result = await run_bash_execute("sleep 5", timeout=2)
    assert result["timed_out"] is True
    assert result["exit_code"] == -1
    assert result["stderr"] == "Command timed out after 2s"
    assert result["stdout"] == ""


@pytest.mark.anyio
async def test_timeout_capped_at_600():
    """timeout > 600 must clamp to 600 (the inner clamp inside run_bash_execute)."""
    # We don't want to actually wait 600s; just call a fast command with high timeout.
    result = await run_bash_execute("true", timeout=10_000)
    assert result["exit_code"] == 0
    # Span attribute would say bash.timeout_sec=600, but we can't easily read that.
    # The behavioural surface: command completes immediately, no error.
    assert result["timed_out"] is False


@pytest.mark.anyio
async def test_timeout_floored_at_one():
    """timeout=0 must be raised to 1 (kills `max(1, timeout)` → `max(0, timeout)`)."""
    result = await run_bash_execute("true", timeout=0)
    assert result["exit_code"] == 0
    assert result["timed_out"] is False


@pytest.mark.anyio
async def test_stderr_captured_separately_from_stdout():
    """stdout and stderr keys must hold their respective streams."""
    result = await run_bash_execute(
        "python3 -c \"import sys; sys.stdout.write('OUT'); sys.stderr.write('ERR')\"",
        timeout=10,
    )
    assert result["stdout"] == "OUT"
    assert result["stderr"] == "ERR"


@pytest.mark.anyio
async def test_exit_code_field_matches_command():
    """Specific non-zero exit codes propagate verbatim."""
    for code in (1, 2, 42, 127):
        result = await run_bash_execute(f"exit {code}")
        assert result["exit_code"] == code


@pytest.mark.anyio
async def test_filenotfound_message_includes_working_dir():
    """The error message must include the bad path verbatim."""
    bad = "/this/path/does/not/exist/zzzqqq"
    result = await run_bash_execute("ls", working_dir=bad)
    assert result["error"] == "FileNotFoundError"
    assert bad in result["message"]
    assert "suggestion" in result


# ---------------------------------------------------------------------------
# _is_alive / shutdown_inflight_processes (signal handling)
# ---------------------------------------------------------------------------


def test_is_alive_returns_true_for_running_then_false_after_wait(tmp_path):
    """_is_alive on a real subprocess: True while running, False after wait."""
    import subprocess

    from mymcp.tools.bash import _is_alive

    p = subprocess.Popen(["sleep", "0.2"])
    try:
        assert _is_alive(p) is True
        p.wait()
        assert _is_alive(p) is False
    finally:
        if p.poll() is None:
            p.kill()


def test_is_alive_handles_object_without_poll():
    """_is_alive on an async-style object (no .poll(), only .returncode)."""
    from mymcp.tools.bash import _is_alive

    class Fake:
        returncode = None

    class Done:
        returncode = 0

    assert _is_alive(Fake()) is True
    assert _is_alive(Done()) is False


def test_shutdown_inflight_is_noop_when_empty():
    """No tracked processes → shutdown completes immediately, no error."""
    from mymcp.tools.bash import _inflight, _inflight_lock, shutdown_inflight_processes

    with _inflight_lock:
        _inflight.clear()
    # Should return promptly even with grace > 0
    shutdown_inflight_processes(grace_sec=0)
